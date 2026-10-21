# Role: PostgreSQL & Data Persistence Expert

Apply these rules to all work in `backend/app/models/`, `backend/app/core/database.py`, services, and migrations.
The stack is sync SQLAlchemy 2.x + psycopg2. Async (`AsyncSession` + asyncpg) is a possible later migration; do not mix the two.

## Connections & sessions
- There is one engine per process (`core/database.py`), with `pool_pre_ping=True` and explicit `pool_size` / `max_overflow`.
- Build the DB URL from `DATABASE_URL` (env). Never hardcode host or credentials.
- There is one session per request via `get_db`. Never share a session across threads or requests.
- Use SQLAlchemy 2.0 style: `select()`, `db.execute()`, `db.scalars()`, `Mapped[...]` + `mapped_column()`.
  Do not use the legacy `db.query()` in new code.

## Transactions & ACID (required for trading)
- Every multi-step write is one atomic unit:
  ```python
  with db.begin():          # commit on success, rollback on any exception
      user = lock_user(db, user_id)
      user.cash_balance -= cost
      db.add(Trade(...))
  ```
- A trade means a balance change + an order/trade row + a position update, all in one transaction. A partial write is a bug.
- Do not call `commit()` in helpers. Only the outermost unit of work commits.
- Store money and quantities as `Numeric(18, 8)` ↔ `Decimal`. Never use `Float`, because binary rounding loses money.
- Enforce invariants in the DB too: `CheckConstraint("cash_balance >= 0")`, `NOT NULL`, FKs with `ondelete`.

## Locking & race conditions
- Read-modify-write on balances or positions: lock the row first.
  `select(User).where(User.id == uid).with_for_update()`, so concurrent orders on the same user run one after another.
- When locking several rows, always lock in a consistent order (e.g. by id ascending) to avoid deadlocks.
- For low-contention rows, the alternative is optimistic locking (`version_id_col`) with a retry on `StaleDataError`.
- Prefer atomic SQL where possible: `UPDATE users SET cash_balance = cash_balance - :c WHERE id = :id AND cash_balance >= :c`
  and check `rowcount`.
- Use unique constraints + catch `IntegrityError` for "create if not exists". Do not check-then-insert.

## Indexing & query performance
- Index every FK column and every column used in `WHERE` / `ORDER BY` on hot paths.
  Add composite indexes in query order (e.g. `(user_id, created_at DESC)` for trade history).
- No N+1 queries: use `selectinload()` for collections and `joinedload()` for many-to-one.
  Never lazy-load inside loops or response serialisation.
- Paginate every list query (`limit`/`offset`, or keyset for large tables).
- Check suspicious queries with `EXPLAIN ANALYZE`. Use `echo=True` only locally.

## PRD schema (§4): Postgres is the Source of Truth
- `users` — identity, account settings, `cash_balance Numeric(18,8)` with `CheckConstraint("cash_balance >= 0")`.
- `portfolios` — current holdings: `(user_id, ticker)` **unique**, `quantity Numeric(18,8)`,
  `average_buy_price Numeric(18,8)`. Recompute the average on every additional purchase inside the trade transaction:
  `avg = (avg * qty + price * new_qty) / (qty + new_qty)`. It is a derived field, so it is only ever written where
  the quantity is written.
- `history` — append-only ledger of executed orders: `user_id`, `ticker`, `side`, `quantity`, `price`, `status`,
  `executed_at timestamptz`. Never `UPDATE` or `DELETE` a history row; a correction is a new compensating row, because
  an audit trail you can edit is not an audit trail.
- Timestamps are `timestamptz` (`DateTime(timezone=True)`) with `server_default=func.now()`. A naive timestamp is
  ambiguous across DST and across pods in different zones.
- A sell must verify the position with the same `FOR UPDATE` lock as the cash check, or two concurrent sells both pass
  the quantity check and oversell the holding.

## Schema changes
- Use Alembic migrations for every schema change (`alembic revision --autogenerate`, then review the diff by hand).
- `Base.metadata.create_all` is for bootstrap/tests only. Do not rely on it for evolving the schema.
- Migrations must be reversible (`downgrade()`) and safe on existing data (add nullable column → backfill → set NOT NULL).
