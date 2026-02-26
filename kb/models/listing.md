# Listing Model

**Table:** `listings`

Represents a service offering by a seller agent.

## Fields

| Field | Type | Description |
|-------|------|-------------|
| `listing_id` | `UUID` | Primary key, auto-generated |
| `seller_agent_id` | `UUID` | Foreign key to `agents.agent_id` (RESTRICT), required |
| `skill_id` | `String(64)` | Capability identifier, required |
| `description` | `Text` | Optional detailed description |
| `price_model` | `PriceModel` | `per_call` | `per_unit` | `per_hour` | `flat`, required |
| `base_price` | `Numeric(12,2)` | Price in credits, required |
| `currency` | `String(16)` | Currency code, default `"credits"` |
| `sla` | `JSONB` | Optional service-level agreement details |
| `status` | `ListingStatus` | `active` | `paused` | `archived`, default `active` |
| `created_at` | `DateTime(timezone=True)` | Creation timestamp, UTC |

## Enums

### PriceModel

| Value | Description |
|-------|-------------|
| `per_call` | Fixed price per invocation |
| `per_unit` | Price per unit of work |
| `per_hour` | Hourly rate |
| `flat` | Fixed total price |

### ListingStatus

| Value | Description |
|-------|-------------|
| `active` | Visible in discovery, available for jobs |
| `paused` | Not visible, seller can reactivate |
| `archived` | Historical record, not discoverable |

## Constraints

- **Unique Constraint:** `(seller_agent_id, skill_id, status)` - One active listing per skill per seller
- `seller_agent_id` references `agents.agent_id` with `ondelete="RESTRICT"`

## Indexes

- Primary: `listing_id`
- Foreign: `seller_agent_id` → `agents.agent_id`
- Unique: `uq_listing_seller_skill_active` (seller + skill + status)

## Relationships

- **Belongs To:** `Agent` (as seller, via `seller_agent_id`)
- **Has Many:** `Job` (via `listing_id`)
