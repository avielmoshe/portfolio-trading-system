FROM python:3.11-slim

WORKDIR /app

# Dev image: requirements-dev.txt pulls in requirements.txt plus the test tooling, so the
# running container can execute the E2E suite against the test database.
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

# Apply pending DB migrations first; `&&` aborts startup if a migration fails.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]
