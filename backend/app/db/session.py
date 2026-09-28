"""Explicit engine lifecycle and commit/rollback boundaries for synchronous callers."""

import math
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def create_db_engine(database_url: str, timeout_seconds: float = 2.0) -> Engine:
    """Create once at startup; the owner must dispose the engine at shutdown.

    Accept Settings.database_url.get_secret_value() without logging it. Connection
    and statement timeouts are bounded and PostgreSQL returns timestamps in UTC.
    """
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("DATABASE_TIMEOUT_SECONDS must be finite and positive")
    try:
        url = make_url(database_url)
        valid = url.drivername == "postgresql+psycopg" and url.host and url.database
    except Exception:
        valid = False
    if not valid:
        raise ValueError("DATABASE_URL must be a PostgreSQL psycopg URL") from None
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_timeout=timeout_seconds,
        hide_parameters=True,
        echo=False,
        connect_args={
            "connect_timeout": max(2, math.ceil(timeout_seconds)),
            "options": f"-c timezone=UTC -c statement_timeout={math.ceil(timeout_seconds * 1000)}",
        },
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit on success; roll back on failure and always close the session."""
    with factory.begin() as session:
        yield session
