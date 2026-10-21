import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def _build_database_url() -> str:
    # docker-compose injects DATABASE_URL; the DB_* fallback keeps local runs working
    # (set DB_HOST=localhost when running uvicorn outside Docker).
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    user = os.getenv("DB_USER", "trading_user")
    password = os.getenv("DB_PASS", "trading_pass")
    name = os.getenv("DB_NAME", "trading_db")
    host = os.getenv("DB_HOST", "db")
    return f"postgresql://{user}:{password}@{host}:5432/{name}"


SQLALCHEMY_DATABASE_URL = _build_database_url()

# pool_pre_ping tests a pooled connection before use, so a restarted DB
# doesn't surface as a random "connection closed" error on the next request.
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"connect_timeout": 10},
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
