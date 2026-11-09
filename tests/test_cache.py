import json
from datetime import datetime, timedelta, timezone

from app import cache, redis_client


def _create(client, **kw):
    return client.post("/api/links", json={"url": "https://example.com/x", **kw}).json()["code"]


def test_redirect_populates_cache(client, run):
    code = _create(client)
    run(cache.invalidate, code)
    assert run(redis_client.client().get, f"link:{code}") is None
    assert client.get(f"/{code}").status_code == 302            # miss -> Postgres -> cached
    cached = json.loads(run(redis_client.client().get, f"link:{code}"))
    assert cached["url"] == "https://example.com/x"
    assert client.get(f"/{code}").status_code == 302            # served from cache


def test_cache_ttl_never_exceeds_link_expiry(client, run):
    soon = (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()
    code = _create(client, expires_at=soon)
    run(cache.invalidate, code)
    client.get(f"/{code}")
    ttl = run(redis_client.client().ttl, f"link:{code}")
    assert 0 < ttl <= 90


def test_negative_caching_and_alias_invalidation(client, run, unique):
    alias = f"neg-{unique}"
    assert client.get(f"/{alias}").status_code == 404           # cached as "does not exist"
    assert run(redis_client.client().get, f"link:{alias}") == "!"
    # creating that alias must clear the stale negative entry immediately
    assert client.post("/api/links", json={"url": "https://a.com", "alias": alias}).status_code == 201
    assert client.get(f"/{alias}").status_code == 302


def test_redirects_survive_redis_outage(client, monkeypatch):
    code = _create(client)

    class Dead:
        async def get(self, *a, **k):
            raise ConnectionError("redis down")
        set = delete = xadd = get

    monkeypatch.setattr(redis_client, "_client", Dead())
    r = client.get(f"/{code}")                                  # falls back to Postgres
    assert r.status_code == 302 and r.headers["location"] == "https://example.com/x"
