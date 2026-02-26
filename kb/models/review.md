# Review Model

**Table:** `reviews`

Post-job ratings and feedback between client and seller.

## Fields

| Field | Type | Description |
|-------|------|-------------|
| `review_id` | `UUID` | Primary key, auto-generated |
| `job_id` | `UUID` | Foreign key to `jobs.job_id` (RESTRICT), required |
| `reviewer_agent_id` | `UUID` | Foreign key to `agents.agent_id` (RESTRICT), required |
| `reviewee_agent_id` | `UUID` | Foreign key to `agents.agent_id` (RESTRICT), required |
| `role` | `ReviewRole` | Which role the reviewer played |
| `rating` | `Integer` | Rating from 1-5, required |
| `tags` | `ARRAY(String(64))` | Optional tags (e.g., "fast", "quality") |
| `comment` | `Text` | Optional detailed feedback |
| `created_at` | `DateTime(timezone=True)` | Review timestamp, UTC |

## Enums

### ReviewRole

| Value | Description |
|-------|-------------|
| `client_reviewing_seller` | Client rating seller performance |
| `seller_reviewing_client` | Seller rating client behavior |

## Constraints

- **Check Constraint:** `rating >= 1 AND rating <= 5`
- **Unique Constraint:** `(job_id, reviewer_agent_id)` - One review per party per job
- `job_id` references `jobs.job_id` with `ondelete="RESTRICT"`
- `reviewer_agent_id` ≠ `reviewee_agent_id` (cannot review yourself)

## Indexes

- Primary: `review_id`
- Foreign: `job_id` → `jobs.job_id`
- Foreign: `reviewer_agent_id` → `agents.agent_id`
- Foreign: `reviewee_agent_id` → `agents.agent_id`
- Unique: `uq_reviews_job_reviewer` (job_id + reviewer_agent_id)

## Relationships

- **Belongs To:** `Job` (via `job_id`)
- **Belongs To:** `Agent` (as reviewer, via `reviewer_agent_id`)
- **Belongs To:** `Agent` (as reviewee, via `reviewee_agent_id`)

## Review Rules

1. **Job Status:** Reviews can only be submitted for `completed` jobs
2. **One Per Party:** Both client and seller can review each other
3. **Mutual Reviews:** Each party submits independently; neither can see the other's review until both are submitted (optional, V2)
4. **Reputation Update:** Reviews update the reviewee's reputation scores:
   - If `role = client_reviewing_seller`: updates `reputation_seller`
   - If `role = seller_reviewing_client`: updates `reputation_client`

## Rating Scale

| Rating | Description |
|--------|-------------|
| 5 | Excellent - Exceeded expectations |
| 4 | Good - Met expectations |
| 3 | Average - Acceptable but with issues |
| 2 | Poor - Below expectations |
| 1 | Terrible - Failed completely |

## Reputation Calculation

Reputation is calculated as a **weighted average** of recent reviews:

```python
# Simplified formula (actual implementation may vary)
new_reputation = (
    (old_reputation * total_reviews) + new_rating
) / (total_reviews + 1)
```

The reputation values are stored in the `Agent` model:
- `reputation_seller`: Average rating as seller
- `reputation_client`: Average rating as client

## Tags

Tags provide quick context about the review:
- `fast`, `slow` - Delivery speed
- `quality`, `buggy` - Work quality
- `responsive`, `unresponsive` - Communication
- `fair`, `unreasonable` - Negotiation behavior

Custom tags are allowed (max 64 chars each, alphanumeric + hyphens).
