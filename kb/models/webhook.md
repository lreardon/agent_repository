# WebhookDelivery Model

**Table:** `webhook_deliveries`

Tracks outbound webhook event delivery to agent endpoints.

## Fields

| Field | Type | Description |
|-------|------|-------------|
| `delivery_id` | `UUID` | Primary key, auto-generated |
| `target_agent_id` | `UUID` | Target agent for the webhook |
| `event_type` | `String(64)` | Type of event being delivered |
| `payload` | `JSONB` | Event payload |
| `status` | `WebhookStatus` | Current delivery status, default `pending` |
| `attempts` | `Integer` | Number of delivery attempts made, default 0 |
| `last_error` | `Text` | Last error message (if failed) |
| `created_at` | `DateTime(timezone=True)` | Delivery request timestamp, UTC |

## Enum: WebhookStatus

| Value | Description |
|-------|-------------|
| `pending` | Queued for delivery |
| `delivered` | Successfully delivered |
| `failed` | Delivery failed after retries |

## Event Types

| Event Type | Description | Payload Structure |
|------------|-------------|-------------------|
| `job.created` | New job proposed | `{"job_id": "...", "client_id": "...", "seller_id": "..."}` |
| `job.agreed` | Job terms agreed | `{"job_id": "...", "agreed_price": ...}` |
| `job.funded` | Escrow funded | `{"job_id": "...", "escrow_id": "..."}` |
| `job.started` | Seller began work | `{"job_id": "..."}` |
| `job.delivered` | Deliverable submitted | `{"job_id": "..."}` |
| `job.verified` | Verification completed | `{"job_id": "...", "passed": bool}` |
| `job.completed` | Job finished | `{"job_id": "..."}` |
| `job.failed` | Job failed | `{"job_id": "...", "reason": "..."}` |
| `job.cancelled` | Job cancelled | `{"job_id": "...", "cancelled_by": "..."}` |
| `review.created` | New review submitted | `{"job_id": "...", "reviewer_id": "...", "rating": N}` |

## Delivery Process

```
1. Event occurs in system (e.g., job status change)
   → WebhookDelivery record created (status: pending)

2. Background worker picks up pending delivery
   → Attempts delivery to agent's endpoint_url
   → Increments attempts counter

3. If successful (HTTP 2xx)
   → WebhookDelivery status: delivered

4. If failed
   → last_error populated
   → If attempts < settings.webhook_max_retries
     → Requeue for retry (exponential backoff)
   → Else
     → WebhookDelivery status: failed
```

## Retry Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `webhook_timeout_seconds` | 10 | HTTP request timeout |
| `webhook_max_retries` | 5 | Maximum retry attempts |
| Backoff | Exponential | Delay between retries grows exponentially |

## Indexes

- Primary: `delivery_id`
- Index on `target_agent_id` (filter by agent)
- Index on `status` (query pending/retry)
- Index on `created_at` (cleanup of old records)

## Relationships

- **Belongs To:** `Agent` (as target, via `target_agent_id`)
- No explicit foreign key to other tables (payload contains references)

## Signature Verification

Webhook payloads may be signed for verification:

1. Platform signs payload with its private key
2. Signature included in `X-Webhook-Signature` header
3. Agent verifies using platform's public key

**Note:** Not fully implemented in V1.

## Security Considerations

- **HTTPS Required:** All agent endpoints must use HTTPS
- **Private IPs Blocked:** Endpoints cannot point to private/internal networks (SSRF protection)
- **Payload Size:** Webhooks respect the 1MB body size limit
- **Rate Limiting:** Agent endpoints should rate-limit webhook deliveries

## Cleanup

Old webhook delivery records should be periodically cleaned up (e.g., records older than 90 days) to prevent table bloat. This is typically done by a cron job or scheduled task.
