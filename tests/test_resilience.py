import asyncio

import pytest

from app import cache, db, redis_client
from app.breaker import CircuitBreaker


def test_breaker_opens_then_probes_then_closes(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("app.breaker.time.monotonic", lambda: now[0])
    b = CircuitBreaker(threshold=3, reset_after=2.0)
    assert b.allow()
    for _ in range(3):
        b.failure()
    assert not b.allow()                 # OPEN: refuse instantly
    now[0] += 2.1
    assert b.allow()                     # HALF-OPEN: exactly one probe gets through...
    assert not b.allow()                 # ...everyone else is still refused
    b.success()
    assert b.allow() and b.allow()       # probe succeeded -> CLOSED


def test_breaker_reopens_if_probe_fails(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("app.breaker.time.monotonic", lambda: now[0])
    b = CircuitBreaker(threshold=2, reset_after=1.0)
    b.failure(); b.failure()
    now[0] += 1.1
    assert b.allow()                     # probe
    b.failure()                          # probe failed
    assert not b.allow()


def test_redis_down_trips_breaker_and_redirects_still_work(client, monkeypatch):
    code = client.post("/api/links", json={"url": "https://example.com/r"}).json()["code"]
    calls = {"n": 0}

    class Dead:
        async def _boom(self, *a, **k):
            calls["n"] += 1
            raise ConnectionError("redis down")
        get = set = delete = xadd = _boom

    monkeypatch.setattr(redis_client, "_client", Dead())
    for _ in range(10):
        assert client.get(f"/{code}").status_code == 302
    # After the breaker opened, Redis was no longer even attempted:
    assert redis_client.breaker.is_open
    assert calls["n"] <= 6, f"expected few Redis attempts, got {calls['n']}"


def test_postgres_down_gives_fast_503_not_a_hang(client, monkeypatch):
    async def dead_acquire(*a, **k):
        raise ConnectionRefusedError("postgres down")

    class DeadPool:
        acquire = staticmethod(dead_acquire)
        async def release(self, c): ...

    # an uncached code must hit Postgres
    monkeypatch.setattr(db, "_pool", DeadPool())
    for _ in range(5):
        r = client.get("/never-seen-" + str(_))
        assert r.status_code == 503 and r.headers["retry-after"] == "2"
    assert db.breaker.is_open


def test_single_flight_one_db_query_for_many_concurrent_misses(client, run, monkeypatch):
    code = client.post("/api/links", json={"url": "https://example.com/sf"}).json()["code"]
    run(cache.invalidate, code)
    queries = {"n": 0}
    real = db.fetchrow

    async def counting(*a, **k):
        queries["n"] += 1
        await asyncio.sleep(0.05)       # keep the query "in flight" while others arrive
        return await real(*a, **k)

    monkeypatch.setattr(db, "fetchrow", counting)

    async def stampede():
        return await asyncio.gather(*[cache.resolve(code) for _ in range(100)])

    results = run(stampede)
    assert all(r and r["url"] == "https://example.com/sf" for r in results)
    assert queries["n"] == 1


def test_single_flight_propagates_errors_to_all_waiters(client, run, monkeypatch):
    async def failing(*a, **k):
        await asyncio.sleep(0.02)
        raise db.DBUnavailable("down")
    monkeypatch.setattr(db, "fetchrow", failing)

    async def stampede():
        return await asyncio.gather(*[cache.resolve("some-uncached-code") for _ in range(10)],
                                    return_exceptions=True)
    results = run(stampede)
    assert all(isinstance(r, db.DBUnavailable) for r in results)
    assert not cache._inflight           # no leaked entries
