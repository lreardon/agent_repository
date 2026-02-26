"""Token bucket rate limiter backed by Redis."""

import time

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, Response

from app.config import settings
from app.redis import get_redis

# Lua script for atomic token bucket check-and-consume
_TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])

if tokens == nil then
    tokens = capacity
    last_refill = now
end

local elapsed = now - last_refill
local new_tokens = math.min(capacity, tokens + elapsed * (refill_rate / 60.0))

if new_tokens >= 1 then
    new_tokens = new_tokens - 1
    redis.call('HMSET', key, 'tokens', new_tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 120)
    return {1, math.floor(new_tokens), math.floor((1 - (new_tokens - math.floor(new_tokens))) * 60 / refill_rate)}
else
    local retry_after = math.ceil((1 - new_tokens) * 60 / refill_rate)
    redis.call('HMSET', key, 'tokens', new_tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 120)
    return {0, 0, retry_after}
end
"""


def _get_rate_config(method: str, path: str) -> tuple[int, int, str]:
    """Return (capacity, refill_per_min, category) based on endpoint."""
    if "/discover" in path:
        return (
            settings.rate_limit_discovery_capacity,
            settings.rate_limit_discovery_refill_per_min,
            "discovery",
        )
    # Registration endpoint gets its own tight limit (per-IP since unauthenticated)
    if method == "POST" and path.rstrip("/") == "/agents":
        return (
            settings.rate_limit_registration_capacity,
            settings.rate_limit_registration_refill_per_min,
            "registration",
        )
    if method in ("POST", "PATCH", "DELETE"):
        # Job lifecycle endpoints get tighter limits
        if "/jobs" in path:
            return 20, 5, "job_lifecycle"
        return (
            settings.rate_limit_write_capacity,
            settings.rate_limit_write_refill_per_min,
            "write",
        )
    return (
        settings.rate_limit_read_capacity,
        settings.rate_limit_read_refill_per_min,
        "read",
    )


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For for reverse proxies."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # First IP in the chain is the original client
        return forwarded_for.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


async def check_rate_limit(
    request: Request,
    response: Response,
    redis: aioredis.Redis = Depends(get_redis),
) -> None:
    """Rate limit dependency. Extract agent_id from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    agent_id: str | None = None
    if auth_header.startswith("AgentSig "):
        try:
            agent_id = auth_header[9:].split(":", 1)[0]
        except (ValueError, IndexError):
            pass

    method = request.method.upper()
    path = request.url.path
    capacity, refill_rate, category = _get_rate_config(method, path)

    if agent_id:
        bucket_key = f"ratelimit:{agent_id}:{category}"
    else:
        client_ip = _get_client_ip(request)
        bucket_key = f"ratelimit:ip:{client_ip}:{category}"
    now = time.time()

    result = await redis.eval(
        _TOKEN_BUCKET_SCRIPT, 1, bucket_key, capacity, refill_rate, now
    )

    allowed, remaining, retry_after = int(result[0]), int(result[1]), int(result[2])

    response.headers["X-RateLimit-Limit"] = str(capacity)
    response.headers["X-RateLimit-Remaining"] = str(remaining)

    if not allowed:
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
