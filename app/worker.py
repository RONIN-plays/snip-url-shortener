"""Analytics worker: Redis Stream -> Postgres.   Run with:  python -m app.worker

Delivery guarantee: AT-LEAST-ONCE.
  * A consumer group tracks which stream entries each worker has been given.
  * We only XACK (acknowledge) after the batch is safely in Postgres.
  * If the worker crashes or Postgres is down, un-acked entries stay "pending";
    XAUTOCLAIM hands them out again once they've been idle for a few seconds.
  * Worst case a click is stored twice (crash between INSERT and XACK). That's
    fine for counters; exactly-once would need an idempotency key per event.
Several workers can run at once: the group splits the entries between them.
"""

import asyncio
import logging
import socket
from datetime import datetime, timezone

from redis.exceptions import RedisError, ResponseError

from . import db, redis_client
from .config import settings

log = logging.getLogger("worker")

GROUP = "analytics"
BATCH = 500
RECLAIM_IDLE_MS = 5000   # entries not acked within 5s are given to a worker again


def device_from_ua(ua: str) -> str:
    u = ua.lower()
    if not u or any(k in u for k in ("bot", "crawler", "spider", "curl", "python-requests", "k6")):
        return "bot"
    if "ipad" in u or "tablet" in u:
        return "tablet"
    if "mobi" in u or "android" in u or "iphone" in u:
        return "mobile"
    return "desktop"


async def ensure_group() -> None:
    try:
        await redis_client.client().xgroup_create(settings.click_stream, GROUP, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):   # group already exists -> fine
            raise


async def _store(messages: list[tuple[str, dict]]) -> None:
    """Bulk-insert one batch with a single statement."""
    ids, tss, refs, uas, devs, ips = [], [], [], [], [], []
    for _, f in messages:
        try:
            ids.append(int(f["link_id"]))
            tss.append(datetime.fromtimestamp(int(f["ts"]) / 1000, tz=timezone.utc))
        except (KeyError, ValueError):
            continue  # malformed ("poison") event: drop it, don't block the queue
        refs.append(f.get("ref", ""))
        uas.append(f.get("ua", ""))
        devs.append(device_from_ua(f.get("ua", "")))
        ips.append(f.get("ip", ""))
    if not ids:
        return
    # The EXISTS filter silently drops clicks for links deleted in the meantime
    # (otherwise the foreign key would reject the whole batch, forever).
    await db.execute(
        """INSERT INTO clicks (link_id, clicked_at, referrer, user_agent, device, ip_hash)
           SELECT t.link_id, t.ts, t.ref, t.ua, t.dev, t.ip
           FROM unnest($1::bigint[], $2::timestamptz[], $3::text[],
                       $4::text[], $5::text[], $6::text[]) AS t(link_id, ts, ref, ua, dev, ip)
           WHERE EXISTS (SELECT 1 FROM links l WHERE l.id = t.link_id)""",
        ids, tss, refs, uas, devs, ips,
    )


async def run_once(consumer: str, block_ms: int = 2000) -> int:
    """Read one batch, store it, acknowledge it. Returns how many entries were handled."""
    r = redis_client.client()
    stream = settings.click_stream
    messages: list[tuple[str, dict]] = []
    try:
        # 1) take over entries other (crashed) workers - or we - failed to ack
        claimed = await r.xautoclaim(stream, GROUP, consumer, min_idle_time=RECLAIM_IDLE_MS,
                                     start_id="0-0", count=BATCH)
        messages += [m for m in claimed[1] if m[1]]   # skip entries trimmed from the stream
        # 2) then new entries
        if len(messages) < BATCH:
            resp = await r.xreadgroup(GROUP, consumer, {stream: ">"},
                                      count=BATCH - len(messages), block=block_ms)
            for _, entries in resp or []:
                messages += entries
    except ResponseError as e:
        if "NOGROUP" in str(e):          # Redis lost its data (restart without persistence)
            await ensure_group()
            return 0
        raise
    if not messages:
        return 0
    await _store(messages)                                   # may raise -> nothing acked -> retried
    await r.xack(stream, GROUP, *[m[0] for m in messages])
    return len(messages)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    await db.connect()
    # socket timeout must exceed the 2s blocking read (see redis_client.connect)
    redis_client.connect(socket_timeout=10)
    consumer = socket.gethostname()
    while True:
        try:
            await ensure_group()
            break
        except (RedisError, OSError) as e:
            log.warning("waiting for Redis: %s", e)
            await asyncio.sleep(1)
    log.info("worker %s started", consumer)
    while True:
        try:
            n = await run_once(consumer)
            if n:
                log.info("stored %d clicks", n)
        except Exception as e:  # keep the worker alive through any outage
            log.warning("batch failed (%s: %s); retrying", type(e).__name__, e)
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
