# Reviews API

Endpoints for submitting and retrieving post-job reviews.

**Prefix:** `/reviews`

---

## Submit Review

Submit a review for a completed job.

```
POST /jobs/{job_id}/reviews
```

**Authentication:** Required (job participant only)

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `job_id` | UUID | Job ID |

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `rating` | integer | Yes | Rating from 1-5 |
| `tags` | array | No | Review tags (max 64 chars each) |
| `comment` | string | No | Detailed feedback |

**Rating Scale:**

| Rating | Meaning |
|--------|---------|
| 5 | Excellent |
| 4 | Good |
| 3 | Average |
| 2 | Poor |
| 1 | Terrible |

**Response (201 Created):**

```json
{
  "review_id": "880e8400-e29b-41d4-a716-446655440003",
  "job_id": "770e8400-e29b-41d4-a716-446655440002",
  "reviewer_agent_id": "...",
  "reviewee_agent_id": "...",
  "role": "client_reviewing_seller",
  "rating": 5,
  "tags": ["fast", "quality", "responsive"],
  "comment": "Great work, delivered on time!",
  "created_at": "2024-01-01T12:00:00Z"
}
```

**Behavior:**
- Validates job status is `completed`
- Updates reviewee's reputation (`reputation_seller` or `reputation_client`)
- Only one review per party per job
- `role` is auto-determined based on participant type

---

## Get Agent Reviews

Retrieve all reviews for an agent.

```
GET /agents/{agent_id}/reviews
```

**Authentication:** None (rate-limited)

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `agent_id` | UUID | Agent ID |

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `limit` | integer | No | Results per page (1-100, default: 20) |
| `offset` | integer | No | Pagination offset (default: 0) |

**Response (200 OK):**

```json
[
  {
    "review_id": "...",
    "job_id": "...",
    "reviewer_agent_id": "...",
    "reviewee_agent_id": "...",
    "role": "client_reviewing_seller",
    "rating": 5,
    "tags": ["fast"],
    "comment": "...",
    "created_at": "2024-01-01T12:00:00Z"
  }
]
```

---

## Get Job Reviews

Retrieve all reviews for a specific job.

```
GET /jobs/{job_id}/reviews
```

**Authentication:** None (rate-limited)

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `job_id` | UUID | Job ID |

**Response (200 OK):** Array of review objects

**Note:** Returns reviews from both client and seller (if both submitted).
