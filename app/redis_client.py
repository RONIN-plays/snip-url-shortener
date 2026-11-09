import asyncio

import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from .breaker import CircuitBreaker
from .config import settings

_client: redis.Redis | None = None
breaker = CircuitBreaker(settings.breaker_threshold, settings.breaker_reset)

# Errors that mean "Redis is unreachable" (as opposed to a bad command).
_NETWORK_ERRORS = (RedisConnectionError, RedisTimeoutError, OSError, asyncio.TimeoutError)


class RedisUnavailable(RedisError):
    """Raised instantly when the breaker is open, or after a network failure."""


def connect(socket_timeout: float | None = None) -> None:
    """Lazy: no network call until the first command, so the app boots even if
    Redis is down.

    The API keeps the default short timeout: if Redis is slow or dead we must
    fail fast and fall back to Postgres rather than make every redirect wait.
    The worker passes a longer one, because its blocking XREADGROUP legitimately
    waits for new events and must not be cut off by the short API timeout.
    """
    global _client
    _client = redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=socket_timeout or settings.redis_timeout,
        socket_connect_timeout=settings.redis_timeout,
    )


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def client() -> redis.Redis:
    assert _client is not None, "Redis client not initialised"
    return _client


async def guard(make_call):
    """Run one Redis call through the circuit breaker.

    `make_call` is a zero-arg function returning the awaitable, e.g.
        await guard(lambda: client().get(key))
    Raises RedisUnavailable (a RedisError) if Redis is down or the breaker is open.
    """
    if not breaker.allow():
        raise RedisUnavailable("circuit open")
    try:
        result = await make_call()
    except _NETWORK_ERRORS as e:
        breaker.failure()
        raise RedisUnavailable(str(e)) from e
    breaker.success()
    return result
