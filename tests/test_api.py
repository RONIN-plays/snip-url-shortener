"""Integration tests. They need Postgres running (docker compose up -d db)."""
from datetime import datetime, timedelta, timezone

import pytest


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_and_redirect(client):
    r = client.post("/api/links", json={"url": "https://example.com/some/long/path?x=1"})
    assert r.status_code == 201
    body = r.json()
    assert len(body["code"]) == 7
    assert body["short_url"].endswith("/" + body["code"])

    r2 = client.get(f"/{body['code']}")
    assert r2.status_code == 302
    assert r2.headers["location"] == "https://example.com/some/long/path?x=1"


def test_unknown_code_404(client):
    assert client.get("/doesnotexist").status_code == 404


def test_expired_link_410(client, run):
    # Create with a valid future expiry, then age it directly in the DB.
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    code = client.post("/api/links", json={"url": "https://example.com", "expires_at": soon}).json()["code"]
    from app import cache, db
    async def expire():
        await cache.invalidate(code)
        await db.execute("UPDATE links SET expires_at = now() - interval '1 minute' WHERE code=$1", code)
    run(expire)
    assert client.get(f"/{code}").status_code == 410


def test_past_expiry_rejected(client):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    r = client.post("/api/links", json={"url": "https://example.com", "expires_at": past})
    assert r.status_code == 422


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "ftp://x.com/f", "not a url", "http://", "file:///etc/passwd"])
def test_bad_urls_rejected(client, bad):
    assert client.post("/api/links", json={"url": bad}).status_code == 422


def test_custom_alias_and_conflict(client):
    alias = "my-alias-" + datetime.now().strftime("%H%M%S%f")[:12]
    alias = alias[:30]
    assert client.post("/api/links", json={"url": "https://a.com", "alias": alias}).status_code == 201
    assert client.post("/api/links", json={"url": "https://b.com", "alias": alias}).status_code == 409
    assert client.get(f"/{alias}").headers["location"] == "https://a.com"


@pytest.mark.parametrize("alias", ["api", "ab", "has space", "x" * 31])
def test_bad_alias_rejected(client, alias):
    assert client.post("/api/links", json={"url": "https://a.com", "alias": alias}).status_code == 422


def test_self_link_rejected(client):
    assert client.post("/api/links", json={"url": "http://localhost:8000/abc"}).status_code == 422
