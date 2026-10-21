# Role: Redis & WebSockets (Real-Time) Expert

## [ACTIVE TODO] Connect to Redis for real prices
**Status (2026-10-18): Redis infrastructure and read path DONE. The data source is still a MOCK.**

- **Done, do not redo:** `redis:7-alpine` compose service (persistence off — it is a cache), `app/cache/`
  (`keys.py`, `client.py`, `prices.py`), `resolve_execution_price` reading `price:{SYMBOL}` first. Keys are hashes
  `{price, prev_close, ts}` with decimals as strings and a 60s TTL re-armed on every write; every tick is also
  published on the `ticks` channel for the WebSocket layer. The client is **synchronous** (`redis.Redis`), matching
  the sync API and services; use `redis.asyncio` only inside the future async WebSocket handlers. Timeouts are 0.5s
  with zero retries so a dead Redis cannot stall an order.
- **Gotcha found in testing:** Pub/Sub channels are global across Redis databases. Tests use db 1 for keys but
  still see the dev feeder's db 0 messages on `ticks`; any test or consumer must filter for its own messages.
- **Open — replace the mock.** `app/workers/price_feeder.py` is a random walk. The real market-data consumer must
  write the same hash + channel contract; then delete the feeder and its `price-feeder` compose service.
- **Open — WebSocket fan-out.** Nothing subscribes to `ticks` yet (frontend Layer 2 of the chart depends on it).
- **Hard constraint (unchanged):** a Redis price sets the **execution price** only; it never authorises a spend.
  Cash and quantities stay `FOR UPDATE` reads from Postgres inside the trade transaction.
- **Removal criteria:** the mock feeder is replaced by a real feed and the stale-fallback policy in
  `api_expert.md` is decided. Then delete this block from **both** files and the `[OPEN]` line in `CLAUDE.md`.

Apply these rules to all work in `backend/app/websockets/` and `backend/app/cache/`.
PRD §5: Redis is the hot cache for market prices and user portfolios; WebSockets push prices to the client without polling.
Redis is a cache, never the source of truth — PostgreSQL is (PRD §4).

## Redis as cache, not as truth
- One shared async client (`redis.asyncio.Redis`) built from `REDIS_URL` with a connection pool, created in lifespan
  and closed on shutdown. Never open a connection per request.
- Namespaced, versioned keys, documented in one `cache/keys.py`:
  - `price:{ticker}` → hash `{price, prev_close, ts}`, TTL 60s
  - `portfolio:{user_id}` → hash `{ticker: quantity}` + `portfolio:{user_id}:meta`, TTL 15m
  - `session:{jti}`, `otp:{user_id}`, `ratelimit:{scope}:{id}`
- Every key gets an explicit TTL. A cache entry without one is a memory leak that also serves stale prices forever.
- Never read money from Redis for a write path. Cash and positions are re-read `FOR UPDATE` from Postgres inside the
  trade transaction — a cached balance can be stale by milliseconds, which is exactly how a double-spend happens.
- Store decimals as strings and convert with `Decimal(...)` on read. Redis values are bytes; `float()` loses precision.
- Cache misses are normal, not errors: on any Redis failure, fall back to Postgres and log. The platform degrades
  (slower) rather than failing (no trading).

## Portfolio cache lifecycle (PRD §5)
- Load on login: after OTP succeeds, warm `portfolio:{user_id}` from Postgres in one query, so the dashboard's first
  paint doesn't hit the DB per holding.
- Refresh after every trade: the trade's DB transaction commits first, then the cache is written. Writing the cache
  inside the transaction publishes a position that a rollback would erase.
- Prefer delete-then-rebuild (`DEL` + warm) over patching fields. Patching duplicates the average-buy-price math that
  already lives in the trade service, and two implementations drift.
- Serve the dashboard read-through: hit Redis → on miss, query Postgres, write back with TTL, return.

## WebSocket connection management
- `/ws/prices` authenticates on connect by validating the JWT from the first message or a short-lived ticket query
  param, then `accept()`s. Browsers can't set `Authorization` on a WebSocket, and a ticket beats a token in a URL
  that lands in access logs.
- A `ConnectionManager` holds `dict[ticker, set[WebSocket]]` plus `dict[WebSocket, set[ticker]]`, so a fan-out touches
  only subscribers of that ticker, and a disconnect cleans both maps in `finally`.
- Push, never poll: one background consumer task reads the Kafka→Redis price stream and broadcasts to subscribers.
  Per-connection polling loops multiply DB/Redis load by the number of users.
- Broadcast with `asyncio.gather(..., return_exceptions=True)` and drop connections that raise. One dead socket must
  not stall the fan-out for everyone else.
- Coalesce: buffer ticks and flush at a fixed interval (~250ms), sending the latest price per ticker. The feed is
  faster than any screen, so sending every tick wastes bandwidth and blocks the event loop.
- Back-pressure: bound each connection's send queue; when it overflows, drop the oldest tick (prices are
  last-write-wins) and close a client that stays behind.

## Disconnections & scaling
- Enforce heartbeats (ping ~20s, drop after 2 missed pongs). TCP can keep a half-open socket "connected" for minutes,
  leaking memory and sending prices to no one.
- Always unregister in `finally` on `WebSocketDisconnect`, and make unregister idempotent.
- With more than one API pod (PRD §8 K8s), fan-out goes through Redis Pub/Sub: each pod subscribes and relays to its
  own sockets, because a WebSocket lives on exactly one pod.
- Never push a user's portfolio or balance on a ticker channel. Private data goes to a per-user channel after that
  user's own trade; price channels are shared across users.
