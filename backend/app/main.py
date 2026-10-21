from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.cache.client import redis_available
from app.api import assets, auth, orders, positions, transactions, users
from app.core.database import SessionLocal


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Schema is owned by Alembic (run before uvicorn starts) and DB readiness by the
    # compose healthcheck, so startup only fails fast if the DB is unreachable.
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    yield


app = FastAPI(title="Portfolio Trading System", lifespan=lifespan)

# All business routes live under /api; /health and / stay unprefixed for probes.
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(assets.router, prefix="/api")
app.include_router(positions.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(transactions.router, prefix="/api")


@app.get("/")
def read_root() -> dict[str, str]:
    return {"status": "System is running"}


@app.get("/health")
def health() -> dict[str, str]:
    # A real round-trip to Postgres, so this endpoint proves the DB is reachable.
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
    # Redis is a cache, not the source of truth, so its absence degrades the service (orders
    # fall back to stored candles) but must not fail the health probe and get the pod killed.
    return {"status": "ok", "database": "connected", "redis": "connected" if redis_available() else "unavailable"}
