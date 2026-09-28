"""Opt-in real PostgreSQL test: use only a dedicated disposable validation database."""

import os
import shutil
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from alembic import command
from app.db.base import TimestampMixin, UUIDMixin
from app.db.session import create_db_engine, create_session_factory, session_scope


class ProbeBase(DeclarativeBase):
    pass


class Probe(UUIDMixin, TimestampMixin, ProbeBase):
    __tablename__ = "t006_probe"
    name: Mapped[str] = mapped_column(unique=True)


def test_postgres_migration_sessions_and_persistence(tmp_path, monkeypatch):
    url = os.environ.get("T006_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set T006_TEST_DATABASE_URL to a dedicated validation database")
    monkeypatch.setenv("DATABASE_URL", url)
    backend = Path(__file__).resolve().parents[2]
    scripts = tmp_path / "alembic"
    shutil.copytree(backend / "alembic", scripts, ignore=shutil.ignore_patterns("versions"))
    (scripts / "versions").mkdir()
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(scripts))
    (scripts / "versions" / "t006_probe.py").write_text("""
from alembic import op
import sqlalchemy as sa
revision = "t006_probe"
down_revision = None

def upgrade():
    op.create_table("t006_probe",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table("t006_probe")
""")
    engine = create_db_engine(url)
    factory = create_session_factory(engine)
    try:
        if os.environ.get("T006_EXPECT_PERSISTED") == "1":
            with session_scope(factory) as session:
                assert session.scalar(select(Probe).where(Probe.name == "persisted")) is not None
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with session_scope(factory) as session:
            assert session.scalar(text("SHOW timezone")) == "UTC"
            assert session.scalar(text("SHOW statement_timeout")) == "2s"
            assert session.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            )
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == "t006_probe"
            row = session.scalar(select(Probe).where(Probe.name == "persisted"))
            if row is None:
                row = Probe(name="persisted")
                session.add(row)
                session.flush()
            session.refresh(row)
            assert isinstance(row.id, UUID)
            assert row.created_at.utcoffset() == timedelta(0)
            assert row.updated_at.utcoffset() == timedelta(0)
        with pytest.raises(RuntimeError), session_scope(factory) as session:
            session.add(Probe(name="rolled_back"))
            session.flush()
            raise RuntimeError("rollback")
        with session_scope(factory) as session:
            assert session.scalar(select(Probe).where(Probe.name == "rolled_back")) is None
    finally:
        engine.dispose()
