"""Shared database primitives, without a PostgreSQL server."""

from datetime import UTC
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin, utc_now
from app.db.session import create_db_engine, create_session_factory, session_scope


class Record(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "test_record"
    name: Mapped[str] = mapped_column()


def test_defaults_and_transaction_boundaries():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Record.__table__])
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        row = Record(name="committed")
        session.add(row)
        session.flush()
        assert isinstance(row.id, UUID)
        assert row.created_at.tzinfo == UTC
        assert row.updated_at.tzinfo == UTC
        original = row.updated_at
        row.name = "updated"
        session.flush()
        assert row.updated_at >= original
    with pytest.raises(RuntimeError), session_scope(factory) as session:
        session.add(Record(name="rolled back"))
        session.flush()
        raise RuntimeError("abort")
    with session_scope(factory) as session:
        assert list(session.scalars(select(Record.name))) == ["updated"]
    engine.dispose()


def test_engine_is_lazy_and_hides_parameters():
    engine = create_db_engine("postgresql+psycopg://user:pass@invalid/db", 2.5)
    assert engine.hide_parameters
    assert not engine.echo
    assert utc_now().tzinfo == UTC
    engine.dispose()
