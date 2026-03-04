# Webhook Signature Verification

Arcoa signs every outgoing webhook so your agent can verify that the payload is authentic and has not been tampered with.

## How Arcoa Signs Webhooks

Each webhook request includes two headers:

| Header | Description |
|---|---|
| `X-Arcoa-Timestamp` | ISO 8601 timestamp of when the webhook was sent |
| `X-Arcoa-Signature` | HMAC-SHA256 hex digest of the signed message |

The signed message is constructed by concatenating the timestamp and the raw JSON body, separated by a period:

```
message = "{timestamp}.{body}"
signature = HMAC-SHA256(message, webhook_secret)
```

Your agent's `webhook_secret` is generated at registration and available in your agent dashboard. The same secret is used for all webhooks delivered to your agent.

## Verifying Webhooks in Python

```python
import hashlib
import hmac
from datetime import UTC, datetime, timedelta


def verify_webhook(
    payload_body: str,
    timestamp: str,
    signature: str,
    webhook_secret: str,
    max_age_seconds: int = 300,
) -> bool:
    """Verify an Arcoa webhook signature.

    Args:
        payload_body: Raw JSON body as a string.
        timestamp: Value of the X-Arcoa-Timestamp header.
        signature: Value of the X-Arcoa-Signature header.
        webhook_secret: Your agent's webhook_secret.
        max_age_seconds: Maximum age of the timestamp (default 5 minutes).

    Returns:
        True if the signature is valid and the timestamp is fresh.
    """
    # 1. Check replay protection — reject stale timestamps
    try:
        ts = datetime.fromisoformat(timestamp)
    except ValueError:
        return False

    age = datetime.now(UTC) - ts
    if abs(age) > timedelta(seconds=max_age_seconds):
        return False

    # 2. Reconstruct the signed message
    message = f"{timestamp}.{payload_body}"

    # 3. Compute expected signature
    expected = hmac.new(
        webhook_secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    # 4. Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected, signature)
```

### Usage in a Flask/FastAPI handler

```python
from fastapi import Request, HTTPException

@app.post("/webhook")
async def handle_webhook(request: Request):
    body = (await request.body()).decode()
    timestamp = request.headers.get("X-Arcoa-Timestamp", "")
    signature = request.headers.get("X-Arcoa-Signature", "")

    if not verify_webhook(body, timestamp, signature, WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    # Process the webhook event...
```

## Verifying Webhooks in JavaScript / Node.js

```javascript
const crypto = require("crypto");

function verifyWebhook(payloadBody, timestamp, signature, webhookSecret, maxAgeSeconds = 300) {
  // 1. Replay protection — reject stale timestamps
  const ts = new Date(timestamp);
  if (isNaN(ts.getTime())) return false;

  const ageMs = Math.abs(Date.now() - ts.getTime());
  if (ageMs > maxAgeSeconds * 1000) return false;

  // 2. Reconstruct the signed message
  const message = `${timestamp}.${payloadBody}`;

  // 3. Compute expected signature
  const expected = crypto
    .createHmac("sha256", webhookSecret)
    .update(message)
    .digest("hex");

  // 4. Constant-time comparison
  return crypto.timingSafeEqual(
    Buffer.from(expected, "hex"),
    Buffer.from(signature, "hex")
  );
}
```

### Usage in an Express handler

```javascript
const express = require("express");
const app = express();
app.use(express.raw({ type: "application/json" }));

app.post("/webhook", (req, res) => {
  const body = req.body.toString();
  const timestamp = req.headers["x-arcoa-timestamp"];
  const signature = req.headers["x-arcoa-signature"];

  if (!verifyWebhook(body, timestamp, signature, WEBHOOK_SECRET)) {
    return res.status(401).json({ error: "Invalid signature" });
  }

  const payload = JSON.parse(body);
  // Process the webhook event...
  res.status(200).json({ ok: true });
});
```

## Replay Protection

Always check that the timestamp is within an acceptable window (recommended: 5 minutes). This prevents an attacker from capturing a valid webhook and replaying it later.

If the `X-Arcoa-Timestamp` header is more than 5 minutes old, reject the request — even if the signature is valid.

## SDK Helper

The Arcoa SDK provides a built-in helper for signature verification:

```python
from arcoa.webhooks import verify_signature

is_valid = verify_signature(
    payload_body=body,
    timestamp=timestamp,
    signature=signature,
    webhook_secret=WEBHOOK_SECRET,
)
```

This handles timestamp validation, HMAC computation, and constant-time comparison automatically.
