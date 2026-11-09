import time

from app import redis_client, worker
from app.config import settings


def _drain(run):
    """Process everything currently in the test stream; returns how many entries were handled."""
    async def go():
        await worker.ensure_group()
        total = 0
        while True:
            n = await worker.run_once("test-consumer", block_ms=100)
            if n == 0:
                return total
            total += n
    return run(go)


def test_clicks_flow_through_stream_to_stats(client, run, user):
    code = client.post("/api/links", json={"url": "https://example.com"}, headers=user["headers"]).json()["code"]
    _drain(run)                                                    # clear leftovers from other tests
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile Safari"
    for _ in range(3):
        client.get(f"/{code}", headers={"user-agent": ua, "referer": "https://twitter.com/post/1"})
    client.get(f"/{code}", headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0)"})

    assert _drain(run) >= 4
    s = client.get(f"/api/links/{code}/stats", headers=user["headers"]).json()
    assert s["total_clicks"] == 4
    assert {d["name"]: d["clicks"] for d in s["devices"]} == {"mobile": 3, "desktop": 1}
    assert {r["name"]: r["clicks"] for r in s["top_referrers"]} == {"twitter.com": 3, "(direct)": 1}
    assert sum(d["clicks"] for d in s["clicks_by_day"]) == 4


def test_unacked_events_are_redelivered(client, run, user, monkeypatch):
    """If storing fails (DB down), nothing is acknowledged and the batch is retried later."""
    code = client.post("/api/links", json={"url": "https://example.com"}, headers=user["headers"]).json()["code"]
    _drain(run)
    client.get(f"/{code}")

    async def boom(_):
        raise ConnectionError("db down")
    real = worker._store
    monkeypatch.setattr(worker, "_store", boom)
    try:
        run(worker.run_once, "test-consumer", 100)
    except ConnectionError:
        pass
    monkeypatch.setattr(worker, "_store", real)

    time.sleep(worker.RECLAIM_IDLE_MS / 1000 + 0.3)   # wait until the entry counts as abandoned
    assert _drain(run) >= 1
    assert client.get(f"/api/links/{code}/stats", headers=user["headers"]).json()["total_clicks"] == 1


def test_deleted_link_and_poison_events_do_not_block_queue(client, run):
    async def go():
        await worker.ensure_group()
        r = redis_client.client()
        await r.xadd(settings.click_stream, {"garbage": "1"})                                  # malformed
        await r.xadd(settings.click_stream, {"link_id": "999999999", "ts": "1700000000000"})   # no such link
    run(go)
    _drain(run)   # must neither raise nor loop forever
