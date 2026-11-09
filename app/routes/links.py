import time
from datetime import date, datetime, timedelta, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator
from starlette.background import BackgroundTask

from .. import analytics, cache, db
from ..auth import optional_user, require_user
from ..config import settings
from ..idgen import generate_code
from ..ratelimit import client_ip, limit_create, limit_redirect
from ..validation import validate_alias, validate_target_url

api_router = APIRouter(prefix="/api")
redirect_router = APIRouter()


# ---------- schemas ----------
class CreateLink(BaseModel):
    url: str
    alias: str | None = None
    expires_at: datetime | None = None

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return validate_target_url(v)

    @field_validator("alias")
    @classmethod
    def _alias(cls, v: str | None) -> str | None:
        return validate_alias(v) if v is not None else None

    @field_validator("expires_at")
    @classmethod
    def _expiry(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:  # treat naive timestamps as UTC
            v = v.replace(tzinfo=timezone.utc)
        if v <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        return v


class LinkOut(BaseModel):
    code: str
    short_url: str
    original_url: str
    created_at: datetime
    expires_at: datetime | None
    clicks: int | None = None


def _short_url(code: str) -> str:
    return f"{settings.base_url.rstrip('/')}/{code}"


# ---------- create ----------
@api_router.post("/links", response_model=LinkOut, status_code=201,
                 dependencies=[Depends(limit_create)])
async def create_link(body: CreateLink, user_id: int | None = Depends(optional_user)):
    async with db.connection() as conn:
        # 1. Take the next number from the sequence (atomic, race-free).
        link_id = await conn.fetchval("SELECT nextval('links_id_seq')")
        # 2. Custom alias wins; otherwise derive the code from the number.
        code = body.alias or generate_code(link_id, settings.id_secret)
        try:
            row = await conn.fetchrow(
                """INSERT INTO links (id, code, original_url, user_id, expires_at)
                   VALUES ($1, $2, $3, $4, $5)
                   RETURNING code, original_url, created_at, expires_at""",
                link_id, code, body.url, user_id, body.expires_at,
            )
        except asyncpg.UniqueViolationError:
            # The UNIQUE index is the single source of truth, so this is safe
            # even if two requests ask for the same alias at the same moment.
            raise HTTPException(409, "That alias is already taken")

    # If this code was recently looked up and cached as "doesn't exist",
    # drop that stale negative entry so the new link works immediately.
    await cache.invalidate(code)
    return LinkOut(short_url=_short_url(code), **dict(row))


# ---------- my links ----------
@api_router.get("/links", response_model=list[LinkOut])
async def my_links(user_id: int = Depends(require_user),
                   limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    rows = await db.fetch(
        """SELECT l.code, l.original_url, l.created_at, l.expires_at,
                  (SELECT count(*) FROM clicks c WHERE c.link_id = l.id) AS clicks
           FROM links l WHERE l.user_id = $1
           ORDER BY l.created_at DESC LIMIT $2 OFFSET $3""",
        user_id, limit, offset,
    )
    return [LinkOut(short_url=_short_url(r["code"]), **dict(r)) for r in rows]


@api_router.delete("/links/{code}", status_code=204)
async def delete_link(code: str, user_id: int = Depends(require_user)):
    # "AND user_id = $2" is the ownership check. A link that is someone else's
    # looks exactly like one that doesn't exist (404), so ids can't be probed.
    deleted = await db.fetchval(
        "DELETE FROM links WHERE code = $1 AND user_id = $2 RETURNING id", code, user_id
    )
    if deleted is None:
        raise HTTPException(404, "Link not found")
    await cache.invalidate(code)


# ---------- stats ----------
@api_router.get("/links/{code}/stats")
async def link_stats(code: str, days: int = Query(30, ge=1, le=365),
                     user_id: int = Depends(require_user)):
    link = await db.fetchrow(
        "SELECT id, original_url FROM links WHERE code = $1 AND user_id = $2", code, user_id
    )
    if link is None:
        raise HTTPException(404, "Link not found")
    lid = link["id"]
    since = datetime.now(timezone.utc) - timedelta(days=days)

    total = await db.fetchval("SELECT count(*) FROM clicks WHERE link_id = $1", lid)
    unique = await db.fetchval(
        "SELECT count(DISTINCT ip_hash) FROM clicks WHERE link_id = $1", lid)
    per_day = await db.fetch(
        """SELECT (clicked_at AT TIME ZONE 'UTC')::date AS day, count(*) AS n
           FROM clicks WHERE link_id = $1 AND clicked_at >= $2 GROUP BY 1""", lid, since)
    referrers = await db.fetch(
        """SELECT COALESCE(NULLIF(referrer, ''), '(direct)') AS name, count(*) AS n
           FROM clicks WHERE link_id = $1 GROUP BY 1 ORDER BY n DESC LIMIT 10""", lid)
    devices = await db.fetch(
        """SELECT COALESCE(device, 'unknown') AS name, count(*) AS n
           FROM clicks WHERE link_id = $1 GROUP BY 1 ORDER BY n DESC""", lid)

    counts = {r["day"]: r["n"] for r in per_day}
    today = datetime.now(timezone.utc).date()
    series = []
    for i in range(days - 1, -1, -1):          # fill days with zero clicks
        d: date = today - timedelta(days=i)
        series.append({"date": d.isoformat(), "clicks": counts.get(d, 0)})

    return {
        "code": code,
        "original_url": link["original_url"],
        "total_clicks": total,
        "unique_visitors": unique,
        "clicks_by_day": series,
        "top_referrers": [{"name": r["name"], "clicks": r["n"]} for r in referrers],
        "devices": [{"name": r["name"], "clicks": r["n"]} for r in devices],
    }


# ---------- redirect ----------
# 302 (temporary) on purpose: browsers cache 301s forever, so repeat clicks
# would never reach us and could not be counted for the stats feature.
@redirect_router.get("/{code}", dependencies=[Depends(limit_redirect)])
async def follow(code: str, request: Request):
    rec = await cache.resolve(code)
    if rec is None:
        raise HTTPException(404, "Link not found")
    if rec["exp"] is not None and rec["exp"] <= time.time():
        raise HTTPException(410, "Link has expired")
    response = RedirectResponse(rec["url"], status_code=302)
    # Runs AFTER the response is sent, so analytics adds no latency to the user.
    response.background = BackgroundTask(
        analytics.record_click, rec["id"],
        request.headers.get("referer"), request.headers.get("user-agent"), client_ip(request),
    )
    return response
