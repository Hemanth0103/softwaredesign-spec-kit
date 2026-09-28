import pytest
from fixtures.corpus import load_corpus


def test_corpus_coverage(corpus):
    assert {"hammond", "westville", "all"} <= {case.campus for case in corpus.cases}
    assert {"active", "superseded", "retired", "pending_review"} <= {
        case.revision_status for case in corpus.cases
    }
    assert {"draft", "approved", "rejected"} <= {case.approval for case in corpus.cases}
    assert any(case.program and case.course and case.academic_term for case in corpus.cases)
    assert len([case for case in corpus.cases if case.conflict_group]) >= 2
    assert any(not case.readable for case in corpus.cases)
    assert any(contact.phone == "911" for contact in corpus.contacts)


@pytest.mark.parametrize("environment", ["production", "staging", "", "TEST"])
def test_fixture_approval_is_local_only(environment):
    with pytest.raises(ValueError):
        load_corpus(environment=environment)


def test_corpus_is_fresh_per_load(corpus):
    other = load_corpus(environment="test")
    corpus.cases.clear()
    assert other.cases


def test_tampered_snapshot_rejected(tmp_path):
    import shutil

    from fixtures.corpus import SOURCES

    shutil.copytree(SOURCES, tmp_path / "sources")
    (tmp_path / "sources" / "registrar.html").write_text("tampered")
    with pytest.raises(ValueError, match="hash"):
        load_corpus(environment="test", manifest=tmp_path / "sources" / "manifest.json")
