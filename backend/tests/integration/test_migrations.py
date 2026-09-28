"""Exercise the real Alembic environment with an isolated temporary revision."""

import shutil
from io import StringIO
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command

BACKEND = Path(__file__).resolve().parents[2]


def test_fresh_and_existing_database(tmp_path):
    scripts = tmp_path / "alembic"
    shutil.copytree(BACKEND / "alembic", scripts, ignore=shutil.ignore_patterns("versions"))
    (scripts / "versions").mkdir()
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(scripts))
    command.revision(config, message="test infrastructure", rev_id="test_001")
    revision = next((scripts / "versions").glob("test_001*.py"))
    revision.write_text(
        revision.read_text().replace(
            "    pass",
            "    op.create_table('probe', sa.Column('id', sa.Integer, primary_key=True))",
            1,
        )
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'database.sqlite'}")
    for _ in range(2):
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            assert "probe" in inspect(connection).get_table_names()
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "test_001"
    engine.dispose()


def test_offline_postgres_needs_only_database_configuration(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:p%25ss@invalid/db")
    monkeypatch.delenv("AI_API_KEY", raising=False)
    output = StringIO()
    config = Config(str(BACKEND / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    assert "BEGIN;" in output.getvalue()
    assert "p%25ss" not in output.getvalue()
