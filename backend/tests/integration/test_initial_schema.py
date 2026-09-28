"""Real initial revision on a disposable, initially empty PostgreSQL database."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.db.base import Base
from app.models.referral import ReferralDirectoryEntry


def test_initial_schema_fresh_repeat_and_downgrade(monkeypatch):
    url = os.environ.get("T006_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL database with CREATEDB privilege required")
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    name = "t009_" + uuid4().hex
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    test_url = make_url(url).set(database=name)
    engine = create_engine(test_url)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='vector'"))
                == 0
            )
        command.upgrade(config, "0001_initial")
        old_source, old_revision = uuid4(), uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO approved_source "
                    "(id,canonical_url,title,owner_office,subject_area) "
                    "VALUES (:id,'https://www.pnw.edu/test','Test','Registrar','Test')"
                ),
                {"id": old_source},
            )
            connection.execute(
                text(
                    "INSERT INTO source_revision (id,source_id,content_hash,retrieved_at) "
                    "VALUES (:id,:source,:hash,now())"
                ),
                {"id": old_revision, "source": old_source, "hash": "b" * 64},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT readable FROM source_revision WHERE id=:id"), {"id": old_revision}
                )
                is False
            )

        with Session(engine) as session:
            row = ReferralDirectoryEntry(topic="Registration", office="Registrar", phone="123")
            session.add(row)
            session.commit()
            identity = row.id
        engine.dispose()
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT id FROM referral_directory_entry")) == identity
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0002_eligibility"
            )
            tables = set(inspect(connection).get_table_names())
            expected = {
                "approved_source",
                "source_revision",
                "source_chunk",
                "source_review_event",
                "referral_directory_entry",
            }
            assert tables == expected | {"alembic_version"}
            metadata = MetaData()
            for table in Base.metadata.sorted_tables:
                if table.name in expected:
                    table.to_metadata(metadata)
            assert compare_metadata(MigrationContext.configure(connection), metadata) == []
            indexes = connection.scalars(
                text("SELECT indexdef FROM pg_indexes WHERE schemaname='public'")
            ).all()
            assert any("USING gin (search_vector)" in index for index in indexes)
            assert not any("USING hnsw" in index or "USING ivfflat" in index for index in indexes)
        with engine.begin() as connection:
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO referral_directory_entry (id,topic,office) "
                        "VALUES (:id,'Topic','Office')"
                    ),
                    {"id": uuid4()},
                )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO source_revision (id,source_id,content_hash,retrieved_at) "
                        "VALUES (:id,:source,:hash,now())"
                    ),
                    {"id": uuid4(), "source": uuid4(), "hash": "a" * 64},
                )
        command.downgrade(config, "base")
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) == {"alembic_version"}
            assert (
                connection.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='vector'"))
                == 1
            )
        command.upgrade(config, "head")
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
