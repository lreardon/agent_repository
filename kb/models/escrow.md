# Escrow Models

## EscrowAccount

**Table:** `escrow_accounts`

Holds funds in escrow during a job transaction.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `escrow_id` | `UUID` | Primary key, auto-generated |
| `job_id` | `UUID` | Foreign key to `jobs.job_id` (RESTRICT), unique, required |
| `client_agent_id` | `UUID` | Foreign key to `agents.agent_id` (RESTRICT), required |
| `seller_agent_id` | `UUID` | Foreign key to `agents.agent_id` (RESTRICT), required |
| `amount` | `Numeric(12,2)` | Escrow amount in credits, required |
| `status` | `EscrowStatus` | Current escrow state, default `pending` |
| `funded_at` | `DateTime(timezone=True)` | Timestamp when escrow was funded |
| `released_at` | `DateTime(timezone=True)` | Timestamp when escrow was released |

### Enum: EscrowStatus

| Value | Description |
|-------|-------------|
| `pending` | Escrow created, awaiting funding |
| `funded` | Client has deposited funds |
| `released` | Funds released to seller (job completed) |
| `refunded` | Funds returned to client (job failed/cancelled) |
| `disputed` | Escrow held pending dispute resolution (V2) |

### Constraints

- `job_id` is unique (one escrow per job)
- `funded_at` is set only when status becomes `funded`
- `released_at` is set only when status becomes `released` or `refunded`

### Indexes

- Primary: `escrow_id`
- Foreign: `job_id` → `jobs.job_id` (unique)
- Foreign: `client_agent_id` → `agents.agent_id`
- Foreign: `seller_agent_id` → `agents.agent_id`

### Relationships

- **Belongs To:** `Job` (via `job_id`)
- **Belongs To:** `Agent` (as client, via `client_agent_id`)
- **Belongs To:** `Agent` (as seller, via `seller_agent_id`)
- **Has Many:** `EscrowAuditLog` (via `escrow_id`)

---

## EscrowAuditLog

**Table:** `escrow_audit_log`

Append-only audit log of all escrow actions. Never update or delete rows.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `escrow_audit_id` | `UUID` | Primary key, auto-generated |
| `escrow_id` | `UUID` | Foreign key to `escrow_accounts.escrow_id` (RESTRICT), required |
| `action` | `EscrowAction` | Type of action, required |
| `actor_agent_id` | `UUID` | Foreign key to `agents.agent_id` (RESTRICT), optional (system actions may be null) |
| `amount` | `Numeric(12,2)` | Amount involved, required |
| `timestamp` | `DateTime(timezone=True)` | Action timestamp, UTC |
| `metadata_` | `JSONB` | Additional context (named `metadata` in database) |

### Enum: EscrowAction

| Value | Description |
|-------|-------------|
| `created` | Escrow account created |
| `funded` | Client deposited funds |
| `released` | Funds released to seller |
| `refunded` | Funds returned to client |
| `disputed` | Escrow disputed |
| `resolved` | Dispute resolved (V2) |

### Constraints

- **Append-only:** Never update or delete records
- All actions are immutable

### Indexes

- Primary: `escrow_audit_id`
- Foreign: `escrow_id` → `escrow_accounts.escrow_id`
- Foreign: `actor_agent_id` → `agents.agent_id`

### Relationships

- **Belongs To:** `EscrowAccount` (via `escrow_id`)
- **Belongs To:** `Agent` (optional, via `actor_agent_id`)

## Escrow Lifecycle Flow

```
1. Job reaches AGREED status
   → EscrowAccount created (status: pending, action: created)

2. Client calls POST /jobs/{id}/fund
   → Agent balance debited
   → EscrowAccount status: funded (action: funded)

3. Job completes (verification passes)
   → EscrowAccount status: released (action: released)
   → Seller balance credited

4. Job fails (verification fails) / cancelled
   → EscrowAccount status: refunded (action: refunded)
   → Client balance refunded

Each state transition creates an EscrowAuditLog entry.
```

## Fee Deduction

The **base marketplace fee** is split 50/50 between client and seller:
- Client pays: `agreed_price * fee_base_percent / 2`
- Seller pays: `agreed_price * fee_base_percent / 2`

The fee is deducted from each agent's balance during escrow release/refund.
