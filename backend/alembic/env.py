"""One-off migrations, independent of AI/OIDC configuration and volume init scripts."""

import os

from alembic import context
from app.db.base import Base
from app.db.session import create_db_engine
from app.models import (  # noqa: F401 -- register source/revision metadata
    referral,
    review_event,
    source,
    source_chunk,
)

# All domain tables are registered before migration autogeneration.
target_metadata = Base.metadata


def run_migrations() -> None:
    supplied_connection = context.config.attributes.get("connection")
    if supplied_connection is not None:
        context.configure(connection=supplied_connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    database_url = os.environ.get("DATABASE_URL", "")
    try:
        timeout = float(os.environ.get("DATABASE_TIMEOUT_SECONDS", "2"))
    except ValueError:
        raise ValueError("DATABASE_TIMEOUT_SECONDS must be a number") from None
    engine = create_db_engine(database_url, timeout)
    try:
        if context.is_offline_mode():
            context.configure(
                url=engine.url,
                target_metadata=target_metadata,
                literal_binds=True,
                dialect_opts={"paramstyle": "named"},
            )
            with context.begin_transaction():
                context.run_migrations()
        else:
            with engine.connect() as connection:
                context.configure(connection=connection, target_metadata=target_metadata)
                with context.begin_transaction():
                    context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
