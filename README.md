# Portfolio Trading System 📈

A distributed, enterprise-grade portfolio management and trading system. 
This project demonstrates a microservices-oriented architecture designed for high throughput and real-time market data processing.

## 🛠️ Tech Stack
**Current:**
* **Backend:** Python, FastAPI
* **Database:** PostgreSQL
* **Infrastructure:** Docker, Docker Compose

**Planned:**
* **Caching & Real-Time:** Redis, WebSockets
* **Message Brokers:** Apache Kafka, RabbitMQ
* **Orchestration:** Kubernetes (K8s)
* **Frontend:** React.js

## 🚀 How to Run (Local Development)

1. Clone the repository:
   ```bash
   git clone <YOUR_GITHUB_URL>
   cd Tradeing_app
   ```
2. Create a `.env` file in the project root:
   ```
   DB_USER=trading_user
   DB_PASS=trading_pass
   DB_NAME=trading_db
   JWT_SECRET=<at least 32 random chars, e.g. output of: openssl rand -hex 32>
   ```
3. Start everything (DB migrations run automatically before the API starts):
   ```bash
   docker compose up --build
   ```
4. Check it works:
   - Health (also pings the DB): http://localhost:8000/health
   - Interactive API docs (Swagger): http://localhost:8000/docs

### Database migrations (Alembic)
Run inside the API container:
```bash
docker compose exec api alembic upgrade head                      # apply
docker compose exec api alembic revision --autogenerate -m "msg"  # create from model changes
docker compose exec api alembic downgrade -1                      # undo last
```