import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from redis.exceptions import RedisError

from . import db, redis_client
from .routes import auth, links

log = logging.getLogger("app")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    redis_client.connect()
    yield
    await redis_client.close()
    await db.disconnect()


app = FastAPI(title="URL Shortener", lifespan=lifespan)


# ---------- graceful degradation ----------
# If Postgres is down or too slow, answer 503 ("try again shortly") quickly
# instead of a 500 stack trace or a hung request.
async def _unavailable(request: Request, exc: Exception):
    log.warning("dependency failure on %s: %s: %s", request.url.path, type(exc).__name__, exc)
    return JSONResponse({"detail": "Service temporarily unavailable"}, status_code=503,
                        headers={"Retry-After": "2"})


for _exc in (db.DBUnavailable, asyncpg.PostgresError, asyncpg.InterfaceError,
             asyncio.TimeoutError, OSError):
    app.add_exception_handler(_exc, _unavailable)


# ---------- health ----------
@app.get("/health")
async def health():
    """Liveness: is the process up? Deliberately checks nothing else, so an
    orchestrator doesn't restart a healthy API just because Postgres is down."""
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """Readiness: can we serve traffic? Postgres is required; Redis is not
    (we degrade gracefully without it)."""
    status = {"postgres": "ok", "redis": "ok"}
    try:
        await db.fetchval("SELECT 1")
    except Exception:
        status["postgres"] = "down"
    try:
        await redis_client.guard(lambda: redis_client.client().ping())
    except (RedisError, OSError, asyncio.TimeoutError):
        status["redis"] = "down"
    return JSONResponse(status, status_code=200 if status["postgres"] == "ok" else 503)


# ---------- frontend ----------
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")

app.include_router(auth.router)
app.include_router(links.api_router)
# Registered LAST: "/{code}" matches any single path segment, so it must come
# after every fixed route like /health.
app.include_router(links.redirect_router)
