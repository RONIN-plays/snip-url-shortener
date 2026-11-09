import asyncio
import logging
from contextlib import asynccontextmanager

import asyncpg

from .breaker import CircuitBreaker
from .config import settings

log = logging.getLogger("db")

# One pool per process, created at startup. Opening a Postgres connection is
# expensive (TCP + auth), so we reuse a small set of them.
_pool: asyncpg.Pool | None = None
breaker = CircuitBreaker(settings.breaker_threshold, settings.breaker_reset)

# Errors that mean "Postgres is unreachable / the connection is broken"
# (as opposed to a normal SQL error like a unique violation).
_CONNECTION_ERRORS = (
    OSError, asyncio.TimeoutError, asyncpg.InterfaceError, asyncpg.PostgresConnectionError,
)


class DBUnavailable(Exception):
    """Postgres is down or the breaker is open. Mapped to HTTP 503."""


async def connect(retries: int = 30) -> None:
    """Create the pool, retrying so the app can start before Postgres is ready."""
    global _pool
    for attempt in range(1, retries + 1):
        try:
            _pool = await asyncpg.create_pool(
                settings.database_url,
                min_size=settings.db_pool_min,
                max_size=settings.db_pool_max,
                command_timeout=settings.db_timeout,
                timeout=settings.db_acquire_timeout * 3,   # TCP connect timeout
            )
            return
        except (OSError, asyncpg.PostgresError) as e:
            log.warning("Postgres not ready (%s), attempt %d/%d", e, attempt, retries)
            await asyncio.sleep(1)
    raise RuntimeError("Could not connect to Postgres")


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    assert _pool is not None, "DB pool not initialised"
    return _pool


@asynccontextmanager
async def connection():
    """Borrow a connection, guarded by a circuit breaker.

    * Breaker open  -> raise DBUnavailable immediately (no 1-3 s wait).
    * Can't get a connection within db_acquire_timeout -> count a failure.
    * Connection-level error while using it -> count a failure.
    * Ordinary SQL errors (e.g. unique violation) pass through untouched.
    """
    if not breaker.allow():
        raise DBUnavailable("circuit open")
    try:
        conn = await pool().acquire(timeout=settings.db_acquire_timeout)
    except _CONNECTION_ERRORS as e:
        breaker.failure()
        raise DBUnavailable(str(e)) from e
    try:
        yield conn
    except _CONNECTION_ERRORS:
        breaker.failure()
        raise
    else:
        breaker.success()
    finally:
        await pool().release(conn)


async def fetchrow(query: str, *args):
    async with connection() as c:
        return await c.fetchrow(query, *args)


async def fetch(query: str, *args):
    async with connection() as c:
        return await c.fetch(query, *args)


async def fetchval(query: str, *args):
    async with connection() as c:
        return await c.fetchval(query, *args)


async def execute(query: str, *args):
    async with connection() as c:
        return await c.execute(query, *args)
