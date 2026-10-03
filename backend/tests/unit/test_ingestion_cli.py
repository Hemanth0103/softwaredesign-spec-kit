"""CLI boundaries and orchestration, without implicit external services."""

from contextlib import nullcontext
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("arguments", [[], ["--rebuild"], ["--manifest", "x", "--seed-fixtures"]])
def test_usage_and_fixture_gate(arguments):
    cli = import_module("app.ingestion.__main__")
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2


def test_configuration_failure_is_safe(monkeypatch, capsys):
    cli = import_module("app.ingestion.__main__")
    monkeypatch.setattr(cli, "load_settings", Mock(side_effect=ValueError("secret-password")))
    assert cli.main(["--manifest", "missing"]) == 1
    assert "secret-password" not in capsys.readouterr().err


@pytest.mark.parametrize("rebuild", [False, True])
def test_orchestration_and_pending_review(monkeypatch, capsys, test_environment, rebuild):
    cli = import_module("app.ingestion.__main__")
    for key, value in test_environment.items():
        monkeypatch.setenv(key, value)
    factory = Mock()
    factory.begin.return_value = nullcontext(Mock())
    engine = Mock()
    monkeypatch.setattr(cli, "create_db_engine", Mock(return_value=engine))
    monkeypatch.setattr(cli, "create_session_factory", Mock(return_value=factory))
    monkeypatch.setattr(cli, "load_manifest", Mock())
    pending = SimpleNamespace(review_required=True, revision_id="pending", content=None)
    ready = SimpleNamespace(
        review_required=False, revision_id="ready", content=b"html", media_type="text/html"
    )
    monkeypatch.setattr(cli, "collect_manifest", Mock(return_value=[pending, ready]))
    ingest, refresh = Mock(return_value=(1, 2)), Mock(return_value=(1, 2))
    monkeypatch.setattr(cli, "ingest_revision", ingest)
    monkeypatch.setattr(cli, "rebuild_revision", refresh)
    args = ["--manifest", "x", "--environment", "test", "--deterministic"]
    assert cli.main(args + (["--rebuild"] if rebuild else [])) == 1
    selected = refresh if rebuild else ingest
    assert selected.call_count == 1
    assert selected.call_args.kwargs["revision_id"] == "ready"
    engine.dispose.assert_called_once()
    output = capsys.readouterr()
    assert "prepared=1 chunks=2 review_required=1 failed=0" in output.out
    assert "owning-office review" in output.err


def test_deterministic_requires_explicit_environment():
    cli = import_module("app.ingestion.__main__")
    with pytest.raises(SystemExit) as error:
        cli.main(["--manifest", "x", "--deterministic"])
    assert error.value.code == 2


def test_provider_registration_is_explicit(test_environment, monkeypatch):
    cli = import_module("app.ingestion.__main__")
    for key, value in test_environment.items():
        monkeypatch.setenv(key, value)
    settings = cli.load_settings()
    with pytest.raises(ValueError, match="provider-factory"):
        cli.provider(settings, None, False)
    selected = Mock()
    constructor = Mock(return_value=selected)
    monkeypatch.setattr(cli, "import_module", Mock(return_value=SimpleNamespace(build=constructor)))
    adapter = cli.provider(settings, "deployment:build", False)
    constructor.assert_called_once_with(settings)
    selected.embed.return_value = [[1, 0, 0]]
    assert adapter.embed(["policy"]) == [(1.0, 0.0, 0.0)]
