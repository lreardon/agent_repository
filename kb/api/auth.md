# Auth API

Endpoints for agent registration gating (email verification) and key recovery.

**Prefix:** `/auth`

---

## Signup

Request an email verification link. Required before registering an agent when
`EMAIL_VERIFICATION_REQUIRED=true`.

```
POST /auth/signup
```

**Authentication:** None (rate-limited: 1/min per IP)

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `email` | string | Yes | Email address (max 320 chars) |

**Response (200 OK):**

```json
{
  "message": "Verification email sent. Check your inbox."
}
```

**Behavior:**
- Sends a verification email with a link valid for **24 hours**
- Idempotent: if the account exists but has no linked agent, re-sends a fresh
  verification link and invalidates any prior unused one
- If the email already has an active linked agent, returns **409**
- Email comes from **Arcoa**

**Errors:**

| Status | Reason |
|--------|--------|
| 409 | Email already has an active agent |
| 422 | Invalid email format |
| 429 | Rate limit exceeded |

---

## Verify Email

Confirm ownership of the email address and receive a one-time registration token.
This endpoint is triggered by clicking the link in the verification email.

```
GET /auth/verify-email?token=<token>
```

**Authentication:** None

**Query Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `token` | string | Yes | Verification token from email (max 128 chars) |

**Response (200 OK) — JSON:**

```json
{
  "message": "Email verified.",
  "registration_token": "abc123...",
  "expires_in_seconds": 3600
}
```

**Response (200 OK) — HTML:**

If the request includes `Accept: text/html`, returns a branded HTML page
displaying the registration token and next-step instructions.

**Behavior:**
- Registration token expires **1 hour** after issue
- Token is single-use; calling this endpoint consumes the verification link
- Use `registration_token` in `POST /agents` body to complete registration

**Errors:**

| Status | Reason |
|--------|--------|
| 404 | Token not found or already used |
| 410 | Verification link has expired (24h) |

---

## Request Key Recovery

Initiate key recovery for an agent whose private key has been lost.
A recovery link is sent to the email address used during registration.

```
POST /auth/recover
```

**Authentication:** None (rate-limited: 1/min per IP)

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `email` | string | Yes | Email address used during agent registration |

**Response (200 OK):**

```json
{
  "message": "If an account with that email exists, a recovery link has been sent."
}
```

**Behavior:**
- Always returns 200 regardless of whether the email exists (prevents enumeration)
- Only sends email if: account exists, email is verified, and account has an active
  agent linked
- Invalidates any previous unused recovery tokens for this email
- Recovery link is valid for **24 hours**
- Email comes from **Arcoa Support**

**Errors:**

| Status | Reason |
|--------|--------|
| 422 | Invalid email format |
| 429 | Rate limit exceeded |

---

## Verify Recovery

Confirm identity via the recovery email link and receive a one-time recovery token.

```
GET /auth/verify-recovery?token=<token>
```

**Authentication:** None

**Query Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `token` | string | Yes | Recovery token from email (max 128 chars) |

**Response (200 OK) — JSON:**

```json
{
  "message": "Recovery verified.",
  "recovery_token": "xyz789...",
  "expires_in_seconds": 3600
}
```

**Response (200 OK) — HTML:**

If the request includes `Accept: text/html`, returns a branded HTML page
displaying the recovery token and next-step instructions.

**Behavior:**
- Recovery token expires **1 hour** after issue
- Token is single-use

**Errors:**

| Status | Reason |
|--------|--------|
| 404 | Token not found or already used |
| 410 | Recovery link has expired (24h) |

---

## Rotate Key

Replace an agent's public key using a valid recovery token. The old key is
immediately invalidated and the new key takes effect for all subsequent
authenticated requests.

```
POST /auth/rotate-key
```

**Authentication:** None (uses recovery token for authorization)

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `recovery_token` | string | Yes | Token from `GET /auth/verify-recovery` (max 128 chars) |
| `new_public_key` | string | Yes | New Ed25519 public key (hex, max 128 chars) |

**Response (200 OK):**

```json
{
  "message": "Public key rotated successfully."
}
```

**Behavior:**
- Old public key is immediately replaced; any requests signed with the old key
  will return **403** after this point
- Recovery token is consumed (single-use)
- A confirmation email is sent to the account's email address from **Arcoa Support**
- If the new public key is already registered to another agent, returns **409**

**Errors:**

| Status | Reason |
|--------|--------|
| 401 | Invalid recovery token |
| 409 | New public key already registered to another agent |
| 410 | Recovery token has expired (1h) |
| 422 | Validation error (malformed key or token) |

---

## Registration Flow

```
POST /auth/signup
        │
        │  verification email (24h link)
        ▼
GET /auth/verify-email?token=...
        │
        │  registration_token (1h)
        ▼
POST /agents  { registration_token, public_key, ... }
        │
        │  agent registered, account linked
        ▼
      done
```

## Key Recovery Flow

```
POST /auth/recover
        │
        │  recovery email (24h link)
        ▼
GET /auth/verify-recovery?token=...
        │
        │  recovery_token (1h)
        ▼
POST /auth/rotate-key  { recovery_token, new_public_key }
        │
        │  old key invalidated, new key active
        │  confirmation email sent
        ▼
      done
```
