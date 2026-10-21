# Role: FastAPI & API Design Expert

## [ACTIVE TODO] Connect to Redis for real prices
**Status (2026-10-18): Redis read path DONE. Real data source and the stale-price policy still OPEN.**

- **Done:** `services/pricing.resolve_execution_price(db, asset)` now prefers the live Redis tick
  (`price:{SYMBOL}`, read via `app/cache/prices.get_tick`), then falls back to the latest OHLCV close, then raises
  `PriceUnavailableError` (409). Ticks older than `MAX_TICK_AGE_SECONDS` (10s) or dated in the future are ignored.
  The order and `GET /api/assets/{symbol}/quote` share this one resolver and report `source` (`redis`|`candle`).
  The client-price exploit stays closed (`OrderRequest` has `extra="forbid"`, test proves 422). Redis being down
  degrades to candles, never to a client price or an error.
- **Open 1 — the ticks are MOCK.** `app/workers/price_feeder.py` (compose service `price-feeder`) random-walks
  prices; it is not market data. Replace it with the real market-data consumer (Kafka, `events_expert.md`) writing the
  same `price:{SYMBOL}` hash + `ticks` channel, then delete the feeder module and its compose service. Until then
  these prices must never be treated as real.
- **Open 2 — fallback policy needs a decision.** With Redis down or the feed stalled, an order currently fills at a
  stored candle close up to `MAX_PRICE_AGE_SECONDS` (default 24h) old. That keeps trading alive but fills at a stale
  price. For live trading either tighten it to minutes or refuse (409) when no fresh tick exists. The `source` field
  on every order makes affected fills auditable, but does not make them correct.
- **Invariants to preserve:** resolve the price **before** any row lock is taken; a Redis price may set the
  **execution price** only and never decides affordability or quantity (those are re-read `FOR UPDATE` from
  Postgres, `db_admin.md`); keep rejecting an unpriced symbol (409) rather than defaulting it.
- **Removal criteria:** the feeder is replaced by a real feed, and Open 2 is decided and enforced. Then delete this
  block from **both** this file and `realtime_expert.md`, and the `[OPEN]` line in `.claude/CLAUDE.md`.

Apply these rules to all work in `backend/app/api/` and `backend/app/schemas/`.

## Routing
- One `APIRouter` per domain module (`api/users.py`, `api/trades.py`, ...), with `prefix` and `tags`.
- Register every router in `main.py` via `app.include_router(...)`. Keep `main.py` limited to wiring.
- Routes are thin: validate → call a service function → return. No SQL or business rules in route bodies.
- Use the plural resource noun and correct verbs: `POST` create (201), `GET` read, `PATCH` partial update, `DELETE` (204).

## Pydantic v2 only
- Use `model_config = ConfigDict(from_attributes=True)`. Never use v1 `class Config` or `orm_mode`.
- Keep three layers separate:
  - Request: `XCreate`, `XUpdate` (all fields optional in Update).
  - Response: `XResponse`. Never expose `hashed_password` or other internal fields.
  - DB: SQLAlchemy models in `models/`. Never return an ORM object without a `response_model`.
- Constrain inputs with `Field(...)` (`gt=0`, `max_length`, `pattern`), plus `EmailStr` and `Annotated` types.
- Use `Decimal` for money and quantities in schemas, never `float`.
- Use `@field_validator` / `@model_validator` for cross-field rules.

## Errors: one uniform shape
- Define an `AppError(Exception)` base class with `status_code`, `code`, and `message` in `core/errors.py`. Subclass it per case
  (`NotFoundError`, `ConflictError`, `InsufficientFundsError`, ...).
- Register exception handlers in `main.py` so every error returns:
  `{"error": {"code": "INSUFFICIENT_FUNDS", "message": "...", "details": {...} | null}}`
- Map `RequestValidationError` to the same shape (422).
- Use the correct status codes: 400 bad input, 401 unauthenticated, 403 forbidden, 404 not found,
  409 conflict (duplicate email), 422 validation.

## OpenAPI / Swagger
- Every route sets `response_model`, `status_code`, `summary`, and `responses={409: {...}}` for known errors.
- Add `description` / `examples` on schema fields that are not self-explanatory.

## Dependency Injection
- The DB session always comes from `db: Annotated[Session, Depends(get_db)]`. Never create sessions inside routes.
- Get auth from `current_user: Annotated[User, Depends(get_current_user)]`.
- Get permissions from composable dependencies (`require_admin`).
- Use shared dependencies for pagination (`limit` and `offset` with bounds).
- Use `Depends` overrides (`app.dependency_overrides`) in tests. Do not monkeypatch.

## Security
- Hash passwords with passlib argon2 (already used). Never log or return secrets.
- Use OAuth2 password flow + JWT for auth. Read secrets from env vars.
- Trading, withdrawal, and portfolio routes always depend on `get_current_user`; see `security_expert.md` for the
  JWT + OTP step-up rules.

## Routers per PRD domain
- `api/auth.py` (register, login, verify-otp, refresh, logout), `api/users.py`, `api/portfolio.py`,
  `api/orders.py`, `api/watchlist.py`, `api/stocks.py` — one router per PRD §3 screen.
- `POST /orders` validates and enqueues, returning **202 + a `PENDING` order**, not a filled trade. Execution belongs
  to the RabbitMQ worker (`events_expert.md`), so a slow broker can't hold an HTTP connection open mid-trade.
- Orders accept an `Idempotency-Key` header, stored unique per user, so a retried request returns the original order
  instead of placing a second one.
- `GET /portfolio` returns holdings plus `average_buy_price`; live price and P&L are merged from the Redis cache,
  never recomputed per request from `history`.
- Paginate `GET /history` by keyset on `(executed_at, id)` — the ledger grows forever, and `OFFSET` degrades linearly.
