"""Redis cache in front of the redirect lookup (cache-aside pattern).

  redirect -> Redis GET  --hit--> answer (no Postgres at all)
                  |
                 miss -> Postgres SELECT -> store in Redis -> answer

Design notes
  * The cache is an optimisation, never a dependency: any Redis error is treated
    as a miss, so redirects keep working (slower) if Redis dies. A circuit breaker
    (see redis_client.guard) makes that failure instant instead of a timeout.
  * TTL never exceeds the link's own expiry, so we can't serve an expired link
    from cache for longer than a moment.
  * Negative caching: unknown codes are remembered briefly, so someone hammering
    /random-code can't turn every request into a Postgres query.
  * Single-flight: if 500 requests for the same uncached code arrive together
    (a "cache stampede"), only ONE queries Postgres; the rest wait for its answer.
"""

import asyncio
import json
import time

from redis.exceptions import RedisError

from . import db, redis_client
from .config import settings

NOT_FOUND = object()  # sentinel: "we know this code doesn't exist"
_NEG = "!"
_ERRORS = (RedisError, OSError, asyncio.TimeoutError)
_inflight: dict[str, asyncio.Future] = {}


def _key(code: str) -> str:
    return f"link:{code}"


async def _cache_get(code: str):
    """Returns a record dict, NOT_FOUND, or None (miss / Redis problem)."""
    try:
        raw = await redis_client.guard(lambda: redis_client.client().get(_key(code)))
    except _ERRORS:
        return None
    if raw is None:
        return None
    if raw == _NEG:
        return NOT_FOUND
    return json.loads(raw)


async def _cache_put(code: str, rec: dict | None) -> None:
    try:
        if rec is None:
            await redis_client.guard(
                lambda: redis_client.client().set(_key(code), _NEG, ex=settings.negative_ttl))
            return
        ttl = settings.cache_ttl
        if rec["exp"] is not None:
            ttl = min(ttl, int(rec["exp"] - time.time()))
        if ttl > 0:
            await redis_client.guard(
                lambda: redis_client.client().set(_key(code), json.dumps(rec), ex=ttl))
    except _ERRORS:
        pass


async def invalidate(code: str) -> None:
    try:
        await redis_client.guard(lambda: redis_client.client().delete(_key(code)))
    except _ERRORS:
        pass


async def _load_from_db(code: str) -> dict | None:
    row = await db.fetchrow(
        "SELECT id, original_url, expires_at FROM links WHERE code = $1", code
    )
    if row is None:
        return None
    exp = row["expires_at"].timestamp() if row["expires_at"] else None
    return {"id": row["id"], "url": row["original_url"], "exp": exp}


async def _load_single_flight(code: str) -> dict | None:
    """Concurrent callers for the same code share one database query."""
    existing = _inflight.get(code)
    if existing is not None:
        return await existing
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    # Mark any exception as "retrieved" so asyncio doesn't warn when nobody else awaited.
    fut.add_done_callback(lambda f: f.cancelled() or f.exception())
    _inflight[code] = fut
    try:
        rec = await _load_from_db(code)
        fut.set_result(rec)
        return rec
    except BaseException as e:
        fut.set_exception(e)
        raise
    finally:
        del _inflight[code]


async def resolve(code: str) -> dict | None:
    """code -> {"id", "url", "exp"} or None if it doesn't exist."""
    if settings.cache_enabled:
        hit = await _cache_get(code)
        if hit is NOT_FOUND:
            return None
        if hit is not None:
            return hit
        rec = await _load_single_flight(code)
        await _cache_put(code, rec)
        return rec
    return await _load_from_db(code)
