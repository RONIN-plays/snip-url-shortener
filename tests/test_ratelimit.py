import asyncio
import time

from app import ratelimit, redis_client


def test_burst_then_blocked_then_refills(run, unique):
    key = f"rl:test:{unique}"

    async def scenario():
        # capacity 3, refill 2 tokens/sec
        results = [await ratelimit.take(key, 3, 2) for _ in range(4)]
        assert [a for a, _ in results] == [True, True, True, False]   # burst of 3; 4th blocked
        assert 0 < results[3][1] <= 0.6                               # ~0.5s until 1 token
        await asyncio.sleep(0.6)                                      # let >=1 token refill
        allowed, _ = await ratelimit.take(key, 3, 2)
        assert allowed
    run(scenario)


def test_buckets_are_independent_per_key(run, unique):
    async def scenario():
        assert (await ratelimit.take(f"rl:a:{unique}", 1, 0.01))[0]
        assert not (await ratelimit.take(f"rl:a:{unique}", 1, 0.01))[0]
        assert (await ratelimit.take(f"rl:b:{unique}", 1, 0.01))[0]   # other client unaffected
    run(scenario)


def test_concurrent_requests_cannot_oversubscribe(run, unique):
    """The Lua script is atomic: 50 simultaneous requests vs a 10-token bucket -> exactly 10 pass."""
    async def scenario():
        res = await asyncio.gather(*[ratelimit.take(f"rl:conc:{unique}", 10, 0.001) for _ in range(50)])
        assert sum(a for a, _ in res) == 10
    run(scenario)


def test_fails_open_when_redis_down(run, monkeypatch):
    class Dead:
        def register_script(self, *a, **k):
            async def boom(**kw):
                raise ConnectionError("down")
            return boom

    ratelimit.reset_script_cache()
    monkeypatch.setattr(redis_client, "_client", Dead())
    allowed, _ = run(ratelimit.take, "rl:x", 1, 1)
    ratelimit.reset_script_cache()
    assert allowed


def test_http_429_with_retry_after(client, monkeypatch, unique):
    from app.config import settings
    monkeypatch.setattr(settings, "rl_redirect_burst", 2)
    monkeypatch.setattr(settings, "rl_redirect_rps", 0.01)
    # fresh bucket per run: the test client always has the same address, so clear it first
    run_key = "rl:redir:testclient"
    client.portal.call(redis_client.client().delete, run_key)
    codes = [client.get(f"/nonexistent-{unique}").status_code for _ in range(3)]
    assert codes[:2] == [404, 404] and codes[2] == 429
    r = client.get(f"/nonexistent-{unique}")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    client.portal.call(redis_client.client().delete, run_key)
