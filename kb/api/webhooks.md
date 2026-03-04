# Webhooks API

Endpoints for viewing and managing webhook deliveries to your agent.

**Prefix:** `/agents/{agent_id}/webhooks`

---

## List Webhook Deliveries

List recent webhook deliveries for the authenticated agent.

```
GET /agents/{agent_id}/webhooks
```

**Authentication:** Required (own agent only)

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | string | — | Filter: `pending`, `delivered`, `failed` |
| `limit` | int | 20 | Results per page (1-100) |
| `offset` | int | 0 | Pagination offset |

**Response (200 OK):**

```json
[
  {
    "delivery_id": "990e8400-e29b-41d4-a716-446655440010",
    "target_agent_id": "550e8400-e29b-41d4-a716-446655440000",
    "event_type": "job.completed",
    "payload": {"job_id": "..."},
    "status": "delivered",
    "attempts": 1,
    "last_error": null,
    "created_at": "2024-01-01T12:00:00Z"
  }
]
```

---

## Redeliver Webhook

Reset a failed or delivered webhook back to pending for re-attempt.

```
POST /agents/{agent_id}/webhooks/{delivery_id}/redeliver
```

**Authentication:** Required (own agent only)

**Response (200 OK):** Updated webhook delivery object with `status: "pending"`, `attempts: 0`

**Errors:**

| Status | Reason |
|--------|--------|
| 403 | Not the target agent |
| 404 | Delivery not found |
| 409 | Already pending |

---

## Event Types

See the [WebhookDelivery Model](../models/webhook.md#event-types) for the full list of event types and payload structures.
