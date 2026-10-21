# Tradeing_app

Portfolio trading system. FastAPI + PostgreSQL 15 + Docker Compose. Code lives in `backend/app/`:
`api/` (routers) → `schemas/` (Pydantic) → `models/` (SQLAlchemy) → `core/` (DB, config). Sync SQLAlchemy + psycopg2.
Run the app with `docker compose up --build`. Swagger UI is at http://localhost:8000/docs.

## Routing: load only the role the task needs
Adopt the matching role before writing code, and apply its rules together with the PRD's business logic.

- Endpoints, routers, request/response schemas, errors, auth deps → `.claude/roles/api_expert.md`
- Models, sessions, transactions, locking, queries, indexes, migrations → `.claude/roles/db_admin.md`
- React SPA: dashboard, Watchlist, charts, order panel, client-side JWT/2FA → `.claude/roles/frontend_expert.md`
- Redis caching, WebSocket push, connection management → `.claude/roles/realtime_expert.md`
- Kafka market-data pipe, RabbitMQ task queue, workers → `.claude/roles/events_expert.md`
- JWT, OTP/2FA, password hashing, protecting money endpoints → `.claude/roles/security_expert.md`
- Dockerfiles, compose, K8s manifests, GitHub Actions → `.claude/roles/devops_expert.md`

Combinations:
- Anything that moves money or positions (trades, balances) → `db_admin` + `api_expert` + `security_expert`,
  and `events_expert` when the order is executed by a worker.
- Login / registration flows → `api_expert` + `security_expert` (+ `events_expert` for OTP delivery).
- Live prices end to end → `realtime_expert` + `events_expert` (+ `frontend_expert` for the client).
- Pure docs work → no role file.

## Open TODOs (always in effect — role files are only loaded on demand, this list is not)
- **[OPEN] Connect to Redis for real prices.** Redis is wired in: orders and quotes use the live tick, falling
  back to the latest candle, and the client cannot set a price. Still open: (1) the ticks come from a **mock
  random-walk feeder**, not market data; (2) a stale-candle fallback up to 24h old is still allowed when Redis is
  down. Details and removal criteria in `.claude/roles/api_expert.md` and `.claude/roles/realtime_expert.md` —
  read one before touching any money endpoint. Delete this line only when it is removed from both role files.

## "Punchline and technique" rule
Before every commit, each piece of non-obvious logic gets one brief comment that states:
1. what actually happens under the hood (the mechanism), and
2. why this technique was chosen over the obvious alternative.
Do not restate what the code says. Example:
`# Row lock (FOR UPDATE) serialises concurrent trades on this user, so two orders can't both spend the same cash.`

## Conventions
- Python 3.11. Full type hints on every function signature and return. Use `X | None`, `list[int]`.
- Clean, small functions. Routes stay thin; business logic goes in `services/`.
- No breaking changes: read the code before editing, keep existing routes and response shapes working,
  and verify (run the app or tests) before saying a task is done.
- Never read or print `.env` or secrets. Config comes from environment variables.
- Pin new dependencies in `backend/requirements.txt`.
