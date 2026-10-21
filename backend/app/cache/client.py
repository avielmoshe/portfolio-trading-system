import os
from functools import lru_cache

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

# Short timeouts are a deliberate safety choice. This client is used on the order path, and the
# redis-py default is to wait indefinitely: a hung Redis would then hang every order. Half a
# second makes a dead cache fail fast so the resolver can fall back to Postgres candles.
_CONNECT_TIMEOUT_SECONDS = 0.5
_READ_TIMEOUT_SECONDS = 0.5


def build_client(url: str) -> redis.Redis:
    return redis.Redis.from_url(
        url,
        # decode_responses: Redis returns bytes by default; text keeps Decimal(...) parsing simple.
        decode_responses=True,
        socket_connect_timeout=_CONNECT_TIMEOUT_SECONDS,
        socket_timeout=_READ_TIMEOUT_SECONDS,
        # Zero retries, stated explicitly. redis-py 8.1 happens to default to none, but that default
        # has differed between major versions, and a retry-with-backoff policy here would multiply
        # every timeout on the order path. Pinning it keeps the worst case at one timeout; the caller
        # already has a fallback (Postgres candles) and the feeder simply tries again next cycle.
        retry=Retry(NoBackoff(), retries=0),
        health_check_interval=30,
    )


@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    """One shared client per process. redis-py keeps an internal connection pool, so reusing
    this object is what avoids opening a new TCP connection per request."""
    return build_client(os.getenv("REDIS_URL", "redis://redis:6379/0"))


def redis_available() -> bool:
    try:
        return bool(get_redis().ping())
    except redis.RedisError:
        return False
