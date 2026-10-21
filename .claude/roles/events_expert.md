# Role: Event-Driven & Message Broker Expert (Kafka & RabbitMQ)

Apply these rules to all work in `backend/app/workers/` and `backend/app/events/`.
PRD §6 splits the two brokers by job, and that split is not negotiable:
**Kafka = high-throughput market-data pipe. RabbitMQ = reliable task queue for critical operations.**

## The split (never mix them)
- Kafka carries market prices: high volume, replayable, loss of one tick is harmless because the next tick supersedes it.
- RabbitMQ carries buy/sell orders, 2FA/OTP sends, and notifications: low volume, each message must be processed
  exactly once and never lost, even if a worker crashes mid-task.
- Never route an order through Kafka or a price feed through RabbitMQ. Kafka's throughput comes from accepting
  at-least-once delivery of disposable data; RabbitMQ's per-message ack is what makes an order survivable.
- Every published message carries `{event_id (uuid), event_type, occurred_at, version, payload}`. Consumers switch on
  `event_type` and ignore unknown ones, so a new producer version can't break a running worker.

## Kafka: market data ingestion → Redis
- Topic `market.prices.v1`, partitioned by ticker (`key=ticker`), so all ticks for one symbol stay ordered within a
  partition — partition order is Kafka's only ordering guarantee.
- The ingestion producer uses `acks=1`, `linger_ms=5`, `compression=lz4`. Market data trades durability for latency;
  `acks=all` buys a guarantee that a 200ms-old price doesn't need.
- The consumer group `price-cache-writer` does one thing: write `price:{ticker}` into Redis (pipelined) and publish to
  the WebSocket fan-out. No DB writes, no business logic — PRD §6: the pipe feeds Redis, and Redis feeds the client.
- Consume in batches and commit offsets after the Redis write, so a restart replays at most one batch of ticks.
  Replayed prices are self-correcting; the newest tick wins.
- On lag, skip rather than catch up: `auto_offset_reset=latest` for price consumers. A stale price delivered late is
  worse than a gap.
- Never persist raw ticks to Postgres from this path. It would turn a firehose into DB write amplification and
  compete with trade transactions for connections.

## RabbitMQ: critical task queue (zero loss)
- Durable topology, declared in code at startup: durable exchanges + durable queues + `delivery_mode=2` persistent
  messages. Any non-durable link in that chain silently discards the queue on a broker restart.
- Queues per task type: `orders.execute`, `notifications.otp`, `notifications.email`, `notifications.sms`,
  bound to a topic exchange by routing key (`order.buy`, `order.sell`, `notify.otp.sms`).
- Consumers use manual ack: `basic_ack` **after** the DB transaction commits, `basic_nack(requeue=False)` on a
  permanent failure. Auto-ack loses the message the instant a worker dies mid-order.
- `prefetch_count=1` for order workers, so a slow order doesn't let one worker hoard the queue while others idle.
- Every queue has a dead-letter exchange (`x-dead-letter-exchange`) plus a retry queue with TTL for backoff. A poison
  message that is requeued forever blocks the queue head and stops all trading.
- Consumers are idempotent: a `processed_events(event_id)` table with a unique constraint, inserted in the same
  transaction as the work. RabbitMQ guarantees at-least-once, so a redelivery must not execute a second buy.

## Order flow & publishing from the API
- The API route validates, authorizes, persists an `orders` row as `PENDING`, and publishes the task. Routes never
  execute the trade inline — the worker owns the money-moving transaction (see `db_admin.md`).
- Publish via the transactional outbox: write the outbox row inside the trade transaction and let a relay publish it.
  Publishing before commit announces a trade a rollback erases; publishing after commit can be lost on crash.
- The worker executes the trade in one transaction (lock user → debit cash → write `History` → upsert `Portfolio`
  with the new average buy price), then invalidates `portfolio:{user_id}` and pushes the result over the user's
  WebSocket channel.
- Terminal order states are `FILLED` / `REJECTED` with a reason, and every transition is appended to `History`.
  PRD §4 calls History an immutable ledger — append only, never update or delete.
- Workers run as their own container/deployment (PRD §8), not as a thread inside the API. The API scales on request
  load, workers scale on queue depth.
