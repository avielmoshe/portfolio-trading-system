# Role: DevOps & Orchestration Expert (Docker, K8s, CI/CD)

Apply these rules to `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, `.github/workflows/`, and `k8s/`.
PRD §8: every component is its own container; K8s provides self-healing and auto-scaling; GitHub Actions gates every merge.

## Docker: one process per container
- Separate services, each with its own image: `frontend`, `api`, `worker-orders`, `worker-notifications`,
  `worker-market-data`, `db` (postgres:15), `redis`, `rabbitmq`, `kafka`. Workers never run inside the API container —
  they scale on queue depth, the API on request rate, and a crashed worker must not take the API down with it.
- Multi-stage builds: build deps in a `builder` stage, copy only the installed packages/artifacts into a slim runtime.
  The toolchain is what makes an image large, and every byte in it is attack surface in production.
- Run as a non-root `USER`, pin base images by major+minor (`python:3.11-slim`), and order layers
  dependencies-then-source so a code change doesn't reinstall `requirements.txt`.
- Every service declares a `healthcheck`, and dependents use `depends_on: condition: service_healthy` (as `db` already
  does). `depends_on` alone only waits for the container to start, not for the service to accept connections.
- Named volumes for stateful services only (`postgres_data`, `redis_data`, `rabbitmq_data`). The bind mount of
  `./backend:/app` is a dev convenience — never ship it in the production compose/K8s spec.
- Pin every new dependency in `backend/requirements.txt`, and keep dev and prod on the same image so
  "works on my machine" is structurally impossible (PRD §8).

## Configuration & secrets
- All config comes from environment variables; nothing environment-specific is baked into an image. The same
  immutable image tag must be promotable dev → staging → prod.
- `.env` is git-ignored and never read or printed. Commit a `.env.example` with keys and dummy values only.
- In K8s: non-secret config in `ConfigMap`, credentials in `Secret` (`JWT_SECRET`, `DB_PASS`, SMS/email keys),
  mounted as env vars. In CI: GitHub Actions secrets, never workflow literals.
- The app fails fast at startup on a missing required variable, so a misconfigured pod crash-loops visibly instead of
  serving traffic with a default secret.

## Kubernetes
- One `Deployment` per stateless service (api, frontend, workers); Postgres/Redis/Kafka/RabbitMQ are `StatefulSet`s
  with `PersistentVolumeClaim`s or managed cloud services — a `Deployment` can reschedule a pod away from its disk.
- Every pod defines `resources.requests` and `limits`. Requests drive scheduling and HPA; without limits one runaway
  container starves its node.
- Probes are distinct: `readinessProbe` → `/health` (checks the DB round-trip, gates traffic),
  `livenessProbe` → a cheap `/` (restarts a wedged process), `startupProbe` for slow boots. Pointing liveness at the
  DB check turns a brief DB blip into a cluster-wide restart storm (PRD §8 self-healing).
- `HorizontalPodAutoscaler` on the API by CPU/RPS, and on workers by RabbitMQ queue depth (KEDA), because a worker's
  real load is backlog, not CPU.
- `RollingUpdate` with `maxUnavailable: 0`, plus a `PodDisruptionBudget`, so deploys never drop live WebSocket traffic
  more than necessary; set `terminationGracePeriodSeconds` long enough for in-flight orders to ack.
- Alembic migrations run as a `Job`/init container that must succeed before the new ReplicaSet rolls out — never from
  app startup, where N replicas would race the same migration.

## CI/CD (GitHub Actions)
- Pipeline order on every PR to a feature branch (PRD §2): lint (`ruff`) → type-check (`mypy`) → `pytest` against a
  real `postgres:15` + `redis` service container → build images. Fail fast: cheap checks first.
- Integration tests run against real service containers, not mocks. Row locking, transactions, and queue acks only
  break under the real engine, which is precisely what this system depends on.
- Tag images with the git SHA (never only `latest`), so a rollback is `kubectl set image` to a known SHA.
- Deploy on merge to the main branch only, with environment protection for prod. Keep secrets in GitHub environments
  and use OIDC for cloud auth rather than long-lived keys.
- Run `pip-audit`/`npm audit` and build with a cached layer per branch. A security advisory on a pinned dependency
  should fail CI, not surface in production.
