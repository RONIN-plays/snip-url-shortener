"""Producer side of click analytics.

The redirect must stay fast, so it does NOT write to Postgres. It drops a small
event onto a Redis Stream (one cheap in-memory append, after the response has
already been sent) and a separate worker (app/worker.py) stores the events in
Postgres in batches.

  redirect --XADD--> [ Redis Stream "clicks" ] --XREADGROUP--> worker --INSERT--> Postgres
"""

import asyncio
import hashlib
import hmac
import time
from urllib.parse import urlsplit

from redis.exceptions import RedisError

from . import redis_client
from .config import settings


def _hash_ip(ip: str) -> str:
    """Keyed hash: lets us count unique visitors without storing raw IPs."""
    return hmac.new(settings.id_secret.encode(), ip.encode(), hashlib.sha256).hexdigest()[:16]


def _referrer_host(referrer: str | None) -> str:
    if not referrer:
        return ""
    try:
        return (urlsplit(referrer).hostname or "")[:255]
    except ValueError:
        return ""


async def record_click(link_id: int, referrer: str | None, user_agent: str | None, ip: str) -> None:
    """Fire-and-forget. Analytics must never break a redirect, so errors are
    swallowed (an event may be lost if Redis is down - acceptable for stats)."""
    try:
        event = {
            "link_id": link_id,
            "ts": int(time.time() * 1000),
            "ref": _referrer_host(referrer),
            "ua": (user_agent or "")[:300],
            "ip": _hash_ip(ip),
        }
        await redis_client.guard(lambda: redis_client.client().xadd(
            settings.click_stream, event,
            maxlen=settings.click_stream_maxlen,
            approximate=True,  # "~" trimming: much cheaper, may keep slightly more
        ))
    except (RedisError, OSError, asyncio.TimeoutError):
        pass
