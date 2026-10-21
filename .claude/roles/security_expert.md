# Role: Security & Authentication Expert

Apply these rules to `backend/app/core/security.py`, `backend/app/api/auth.py`, and any endpoint that moves money.
PRD §7: dynamic JWTs with expiration, and 2FA/OTP over SMS or email for login and critical operations.

## Password hashing
- Argon2id via passlib (`passlib[argon2]`, already pinned), through one `hash_password` / `verify_password` pair.
  Argon2 is memory-hard, so GPU cracking of a leaked `users` table is orders of magnitude slower than with bcrypt.
- Tune to ~100ms on the target pod (`memory_cost`, `time_cost`, `parallelism`) and store params in the hash string,
  so cost can be raised later without invalidating existing hashes.
- Rehash on login when `needs_update()` says the params changed — a transparent upgrade, since that's the only moment
  the plaintext exists.
- Always run `verify_password` even when the email doesn't exist (verify against a dummy hash), so response time
  doesn't disclose which accounts are real.
- Never log, return, or put a password or hash in a schema. `UserResponse` excludes `hashed_password` by construction.

## JWT
- Short-lived access token (15 min) + long-lived refresh token (7 days, `httpOnly; Secure; SameSite=Strict` cookie).
  A stolen access token expires on its own; revocation is handled at refresh time.
- Claims: `sub` (user id), `exp`, `iat`, `jti`, `token_type` ("access" | "refresh"), `mfa` (bool). Reject a refresh
  token presented as an access token — without `token_type`, one is silently usable as the other.
- HS256 with `JWT_SECRET` from the environment (min 32 bytes), never a literal in code or committed config.
  Verify with an explicit `algorithms=["HS256"]` allow-list, so a forged `alg: none` header is rejected.
- Decode failures are always a flat 401 `{"error": {"code": "INVALID_TOKEN", ...}}` — never echo the jose exception,
  which tells an attacker whether the signature or the expiry failed.
- Logout and password change revoke by storing `jti` in Redis (`session:{jti}` denylist) with TTL = remaining token
  life, so the key expires exactly when the token would.
- `get_current_user` is the single dependency that decodes the token and loads the user; no route parses a token itself.

## 2FA / OTP (via RabbitMQ)
- Login is two steps: password check issues a short-lived (5 min) `mfa_token` with `mfa: false` and no API rights;
  only `POST /auth/verify-otp` exchanges a correct code for a real access token carrying `mfa: true`.
- Generate 6 digits with `secrets.randbelow(10**6)`, store **only the hash** in `otp:{user_id}` with a 5-minute TTL,
  and delete on success. `random` is predictable from observed outputs; a stored plaintext OTP is a stored password.
- Compare with `secrets.compare_digest`, cap at 5 attempts (counter in the same Redis key), then invalidate the OTP
  entirely — otherwise 10^6 codes are brute-forceable in minutes.
- Delivery is always published to RabbitMQ (`notifications.otp` → `notify.otp.sms` / `notify.otp.email`), never sent
  inline. The auth endpoint must not block on a third-party SMS gateway or fail login when it is down.
- Rate-limit by IP *and* by account (`ratelimit:login:{ip}`, `ratelimit:otp:{user_id}`) in Redis, and return 429.
  IP-only limits are bypassed by a botnet; account-only limits let one IP enumerate many accounts.

## Protecting critical endpoints
- Withdrawals and orders require step-up auth: a fresh OTP-backed `mfa_token` in the request, re-verified server-side.
  PRD §7 requires this for critical operations — a live session alone must not be able to move money out.
- Authorization is always `resource.user_id == current_user.id`, checked in the service against the row just loaded,
  never from a client-supplied `user_id`. Trusting a body field is a one-line path to trading another user's portfolio.
- Audit every security-relevant event (login, OTP sent/failed, token refresh, withdrawal, order) with user id, IP,
  and outcome — and no secrets, codes, or tokens in the log line.
- CORS is an explicit origin allow-list from env (never `*` with credentials), HTTPS only, and secrets come from env /
  K8s Secrets. Never read or print `.env`.

## How auth works in this codebase (implemented today)
The sections above are the target design. This section is what exists now, so read it before touching auth code.

**Files**
- `core/security.py`: `hash_password`, `verify_password`, `verify_and_update_password` (dummy-hash timing equalisation +
  transparent rehash), `create_access_token`, `decode_access_token`. `JWT_SECRET` (>= 32 chars) is read at import, so
  the app refuses to start without it.
- `services/auth.py`: `authenticate(db, email, password) -> User | None`. Never says whether email or password was wrong.
- `api/auth.py`: `POST /api/auth/login` (JSON `{email, password}`) -> `{access_token, token_type: "bearer", expires_in}`.
- `api/deps.py`: `get_current_user` (the only token parser), `require_admin`, and the aliases `DbSession`,
  `CurrentUser`, `AdminUser`.

**Login flow**: client POSTs credentials -> `authenticate` -> on success a 15-minute HS256 JWT is returned -> client sends
`Authorization: Bearer <token>` on every protected call -> `get_current_user` decodes it (allow-list `["HS256"]`,
requires `exp iat sub jti`, rejects `token_type != "access"`), loads the user, and returns it. Any failure is one flat
401 `Invalid or expired token`. Claims: `sub` (user id as string), `exp`, `iat`, `jti`, `token_type`, `mfa` (always
`false` until the OTP step exists).

**Protecting a route** (copy this, do not parse tokens yourself):
```python
def deposit(body: MoneyRequest, current_user: CurrentUser, db: DbSession) -> UserResponse: ...
def list_users(_admin: AdminUser, db: DbSession) -> list[UserResponse]: ...
```
- The acting user is always `current_user.id` from the token, never a `user_id` from the body or path.
- Admin rights are read from `users.is_admin` in the DB on every request, not from a token claim. Promote a user with SQL;
  there is deliberately no endpoint for it.
- Money changes go through a service that locks the row: `select(User)...with_for_update().execution_options(populate_existing=True)`.
  `populate_existing` matters: `get_current_user` already loaded the user in the same session, and without it the lock
  query returns that stale in-memory copy instead of the fresh locked row.
- Services never commit; the route commits after the service succeeds (see `api/users.py`).

**Not implemented yet** (do not assume they exist): refresh tokens and the cookie, logout/`jti` revocation, Redis rate
limiting, OTP/2FA step-up for withdraw and orders, the uniform `{"error": {...}}` body (errors still use FastAPI's
`{"detail": ...}`), and audit logging.

**Testing recipe**: `POST /api/users/createUser` -> `POST /api/auth/login` -> paste the token into Swagger **Authorize**
(or `-H "Authorization: Bearer $TOKEN"` in curl). Expect 401 for missing/garbage/forged (`alg: none`) tokens and 403 for a
non-admin on admin routes.
