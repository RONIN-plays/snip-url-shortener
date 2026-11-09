"""Test setup. Env vars must be set BEFORE `app` is imported (settings are read at import)."""
import os

# Tests create many anonymous links; the default per-IP limit would block them.
# The limiter itself is tested directly in test_ratelimit.py.
os.environ["RL_CREATE_ANON_BURST"] = "100000"
os.environ["RL_AUTH_BURST"] = "100000"
os.environ["RL_REDIRECT_BURST"] = "100000"
# A separate stream, so the real worker container (reading "clicks") can't steal test events.
os.environ["CLICK_STREAM"] = "clicks-test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    # Entering the context runs the app's lifespan (DB pool + Redis client).
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture
def run(client):
    """Run a coroutine on the app's event loop (where the DB pool / Redis client live)."""
    return client.portal.call


_n = 0


@pytest.fixture
def unique():
    """Unique-ish strings so tests never collide with leftovers from earlier runs."""
    import time
    global _n
    _n += 1
    return f"{int(time.time()*1000) % 10**9}{_n}"


@pytest.fixture
def user(client, unique):
    """A registered user + auth headers."""
    email = f"user{unique}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "correct-horse"})
    assert r.status_code == 201
    return {"email": email, "headers": {"Authorization": f"Bearer {r.json()['access_token']}"}}


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Tests that simulate outages must not leave a breaker open for the next test."""
    from app import db, redis_client
    db.breaker.reset()
    redis_client.breaker.reset()
    yield
    db.breaker.reset()
    redis_client.breaker.reset()
