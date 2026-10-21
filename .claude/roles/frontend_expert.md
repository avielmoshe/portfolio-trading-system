# Role: Frontend & React Expert (SPA)

Apply these rules to all work in `frontend/`. React.js SPA (PRD §3): secure personal area, trading dashboard,
Watchlist, expanded stock page. The backend is the only source of truth; the client never computes authoritative money.

## State: separate server state from UI state
- Server state (portfolio, balances, order history, fundamentals) belongs in a query cache (TanStack Query),
  keyed by resource (`['portfolio', userId]`). Never copy it into `useState` — a second copy goes stale after a trade.
- Live prices live in one WebSocket store (Zustand/Context) keyed by ticker: `{ [ticker]: { price, prevClose, ts } }`.
  Prices update many times a second, so they stay out of the query cache to avoid re-rendering the whole dashboard.
- P&L is derived, never stored: `unrealized = (livePrice - avgBuyPrice) * quantity`, computed in a `useMemo`
  from holdings (server) × price (socket). Storing it creates two truths that drift.
- After a trade succeeds, invalidate `['portfolio']` and `['history']` rather than patching the cache by hand —
  the server already recomputed `average_buy_price` and the cash balance, and the client must not duplicate that math.
- Render money with `Intl.NumberFormat`, and keep API decimal strings as strings until formatting.
  JS numbers are binary floats; parsing a balance into one reintroduces exactly the error `Numeric(18,8)` prevents.

## Real-time Watchlist (WebSockets, no polling)
- One shared socket per session, opened in a provider at the authenticated-area root. Never open a socket per component —
  N tickers must not mean N connections.
- Subscribe/unsubscribe by ticker on mount/unmount (`{"action":"subscribe","tickers":[...]}`), so the server pushes
  only what is on screen.
- Throttle UI updates to ~4/sec with `requestAnimationFrame` or a batched buffer. The feed is faster than the screen
  refreshes, and rendering every tick burns frames without adding information.
- Flash green/red on change (PRD §3 "visual up/down indicators") by comparing to the previous tick, and derive the
  arrow from `price vs prevClose` — not from the flash, which is transient.
- Handle disconnects explicitly: reconnect with exponential backoff + jitter, mark prices stale (dimmed) while down,
  and refetch the portfolio on reconnect. A frozen price shown as live is worse than a price shown as stale.

## Dual-Layer Chart Architecture (mandatory for every asset chart)
An asset chart is built from **two** sources, never one. Neither can do the other's job: REST has history but no
present, the socket has the present but no past.

- **Layer 1 — historical (REST, cold):** fetch aggregated OHLCV candles from
  `GET /api/assets/{symbol}/prices?interval=1m|5m|15m|1h|1d&from=&to=` and render them as the static timeline
  (candlestick or line). This is server-aggregated data: the client never builds candles out of tick history.
  Cache it in TanStack Query keyed by `['prices', symbol, interval, range]` — a closed candle is immutable, so it
  can be cached hard and never refetched on a tick.
- **Layer 2 — live (WebSocket/Redis, hot):** subscribe to the same ticker and render the real-time blinking price on
  the **active (right) edge** only — the in-progress candle plus the current price marker. Live ticks mutate only
  that last candle: extend `high`/`low`, overwrite `close`, leave `open` fixed. Every candle to its left is frozen.
- **The seam is the contract.** Ticks must never be appended as new candles, or the chart grows a fake bar per tick
  and the x-axis stops matching the interval. When a candle period closes, drop the locally-built bar and let the
  next REST refetch supply the authoritative one — the server's aggregate wins over anything assembled client-side.
- **Gap handling on connect:** REST first, then socket. If the newest candle is older than one interval, show the gap
  as empty rather than interpolating — a straight line drawn through missing data is a lie about the price.
- **Degrade honestly:** socket down → keep the historical chart, dim or hide the live edge, and label it stale.
  REST failed but socket up → show the live price alone with no chart, never a one-point chart implying a trend.
- **Performance:** keep candles out of React state (they are large and immutable) and feed the charting library
  directly; throttle the live edge to the same ~4/sec as the Watchlist. Downsample server-side via `interval`,
  never by fetching `1m` candles for a one-year range and thinning them in the browser.

## Auth: JWT on the client
- Keep the access token in memory only (module-scope or context). `localStorage` is readable by any injected script,
  and this client holds trading rights.
- The refresh token belongs in an `httpOnly; Secure; SameSite=Strict` cookie set by the backend. The SPA never reads it.
- One axios/fetch wrapper attaches `Authorization: Bearer`, and on 401 performs a single refresh, replays the request,
  and de-duplicates concurrent refreshes with one shared promise, so a dashboard with 5 in-flight calls refreshes once.
- `<ProtectedRoute>` wraps the authenticated area and redirects to login on a missing/expired token. Route guards are
  UX only — every real check is server-side.
- Clear the query cache and close the socket on logout. A cached portfolio surviving logout leaks the previous user's data.

## 2FA / OTP flow (PRD §7)
- Login is two steps: `POST /auth/login` returns `{"mfa_required": true, "mfa_token": ...}` — not a session —
  then `POST /auth/verify-otp` exchanges `mfa_token` + code for the real JWT. The interim token can do nothing else.
- The OTP screen is its own route with 6 single-char inputs (`inputMode="numeric"`, `autocomplete="one-time-code"`),
  paste-aware, with a resend countdown driven by the server's `retry_after`, never a local guess.
- Critical actions (withdrawal, and orders above the PRD threshold) re-prompt for OTP in a modal and send the resulting
  `mfa_token` with the request — step-up auth, so a stolen live session still can't move money.
- Never reveal from the UI whether the email existed; render the generic server message verbatim.

## Component structure
- `features/<domain>/` (dashboard, watchlist, stock, orders, auth) with `components/`, `hooks/`, `api/` per feature.
  Shared primitives in `components/ui/`. No cross-feature imports except through `shared/`.
- Charts (PRD §3 interactive price history) use a lightweight canvas library (`lightweight-charts`); SVG/DOM charts
  stall at thousands of candles. Memoize the series, update via the chart's `update()` API, never by re-mounting.
- The order panel is a controlled form (react-hook-form + zod) mirroring the backend schema: `ticker`, `side`,
  `quantity > 0`. Client validation is UX; it never replaces the server's.
- Disable submit while in flight and send an idempotency key per order, so a double-click cannot place two trades.
- Show an optimistic "pending" row for an order, but never an optimistic balance. Balances come back from the
  transaction that actually moved the money.
