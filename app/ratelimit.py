"""Token-bucket rate limiting, stored in Redis.

How a token bucket works
  * Each client (an IP or a user) has a bucket that holds up to `capacity` tokens.
  * Every request removes 1 token. If the bucket is empty -> HTTP 429.
  * Tokens refill continuously at `rate` per second.
  So a client can burst up to `capacity` requests at once, but over time is held
  to `rate` requests per second. (A fixed "N per minute" window would allow 2N
  requests around a window boundary; the bucket doesn't have that flaw.)

Why a Lua script?  Read-modify-write from Python is a race: two API workers could
both read "1 token left" and both let a request through. Redis runs a Lua script
atomically, so check + decrement is a single indivisible step, shared by every
API process. The script uses Redis' own clock (TIME), so API servers with
slightly different clocks can't disagree.
"""

import asyncio
import math

from fastapi import Depends, HTTPException, Request
from redis.exceptions import RedisError

from . import redis_client
from .auth import optional_user
from .config import settings

_LUA = """
local capacity = tonumber(ARGV[1])
local rate     = tonumber(ARGV[2])
local cost     = tonumber(ARGV[3])
local t   = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local d = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(d[1])
local ts     = tonumber(d[2])
if tokens == nil then tokens = capacity; ts = now end
tokens = math.min(capacity, tokens + (now - ts) * rate)
local allowed = 0
local retry = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry = (cost - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
-- idle buckets expire on their own (twice the time needed to refill fully)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate) * 2)
return {allowed, tostring(retry)}
"""

_script = None


async def take(key: str, capacity: float, rate: float, cost: float = 1) -> tuple[bool, float]:
    """Try to take `cost` tokens. Returns (allowed, seconds_until_retry).

    Fails OPEN: if Redis is unreachable we let the request through. For a public
    redirect service, being briefly unprotected beats being fully down.
    """
    global _script
    try:
        if _script is None:
            _script = redis_client.client().register_script(_LUA)
        allowed, retry = await redis_client.guard(
            lambda: _script(keys=[key], args=[capacity, rate, cost]))
        return bool(allowed), float(retry)
    except (RedisError, OSError, asyncio.TimeoutError):
        return True, 0.0


def reset_script_cache() -> None:
    global _script
    _script = None


def client_ip(request: Request) -> str:
    # Behind a reverse proxy, uvicorn's --proxy-headers rewrites request.client
    # to the real client address (from X-Forwarded-For).
    return request.client.host if request.client else "unknown"


async def _enforce(key: str, capacity: float, rate: float) -> None:
    if not settings.rate_limit_enabled:
        return
    allowed, retry = await take(key, capacity, rate)
    if not allowed:
        raise HTTPException(
            429, "Too many requests",
            headers={"Retry-After": str(max(1, math.ceil(retry)))},
        )


# ----- FastAPI dependencies -----
async def limit_redirect(request: Request) -> None:
    await _enforce(f"rl:redir:{client_ip(request)}",
                   settings.rl_redirect_burst, settings.rl_redirect_rps)


async def limit_auth(request: Request) -> None:
    await _enforce(f"rl:auth:{client_ip(request)}",
                   settings.rl_auth_burst, settings.rl_auth_rps)


async def limit_create(request: Request, user_id: int | None = Depends(optional_user)) -> None:
    if user_id is not None:  # logged-in users: per-user bucket, more generous
        await _enforce(f"rl:create:u:{user_id}",
                       settings.rl_create_user_burst, settings.rl_create_user_rps)
    else:                    # anonymous: per-IP bucket, strict
        await _enforce(f"rl:create:ip:{client_ip(request)}",
                       settings.rl_create_anon_burst, settings.rl_create_anon_rps)
