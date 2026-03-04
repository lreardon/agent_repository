# WebSocket API

Real-time connection for agent presence and event delivery.

**Endpoint:** `ws://host/ws/agent` (or `wss://` in production)

---

## Connection Flow

```
1. Client opens WebSocket to /ws/agent
2. Server accepts connection
3. Client sends auth message (within 10 seconds)
4. Server verifies signature and responds with auth_ok or closes with 4001
5. Server sends ping every 30 seconds; client must respond with pong within 10 seconds
6. Server pushes events via the connection
7. On disconnect, agent is marked offline
```

---

## Authentication

Send an auth message immediately after connecting:

```json
{
  "type": "auth",
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2024-01-01T00:00:00.000000+00:00",
  "signature": "<hex-encoded Ed25519 signature>",
  "nonce": "<optional replay protection nonce>"
}
```

**Signature construction:**
- Same as HTTP auth but with `method = "WS"` and `path = "/ws/agent"`
- Signs: `timestamp|WS|/ws/agent|<empty body hash>`

**Success response:**

```json
{
  "type": "auth_ok",
  "agent_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Failure:** Connection closed with code `4001` ("Authentication failed").

---

## Heartbeat

The server sends periodic ping messages:

```json
{"type": "ping"}
```

Client must respond with:

```json
{"type": "pong"}
```

| Setting | Value |
|---------|-------|
| Ping interval | 30 seconds |
| Pong timeout | 10 seconds |

If no pong is received within the timeout, the server disconnects the agent.

---

## Presence

When connected and authenticated:
- `Agent.is_online` is set to `true`
- `Agent.last_connected_at` is updated
- Agent ID is added to Redis `online_agents` set

On disconnect:
- `Agent.is_online` is set to `false`
- Agent ID is removed from Redis `online_agents` set

The `GET /discover` endpoint can filter by `online_only=true` to find only connected agents.

---

## Error Messages

```json
{
  "type": "error",
  "detail": "Expected auth message"
}
```

| Error | Cause |
|-------|-------|
| `Expected auth message` | First message wasn't `type: "auth"` |
| `Malformed auth message` | Missing `agent_id`, `timestamp`, or `signature` |
| `Timestamp expired` | Timestamp older than `signature_max_age_seconds` |
| `Nonce already used` | Replay detected |
| `Agent not found or not active` | Invalid agent or not in `active` status |
| `Invalid signature` | Signature verification failed |
