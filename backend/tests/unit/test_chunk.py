"""T023 boundaries: metadata isolation, complete table rows and fail-safe budgets."""

from dataclasses import FrozenInstanceError
from importlib import import_module
from uuid import uuid4

import pytest

from app.ingestion.extract import ExtractedBlock


def chunk(blocks, **kwargs):
    return import_module("app.ingestion.chunk").chunk_document(blocks, **kwargs)


def test_sections_and_anchors_never_mix_and_revision_is_retained():
    revision_id = uuid4()
    blocks = [
        ExtractedBlock("First paragraph.", "A", citation_anchor="a"),
        ExtractedBlock("Second paragraph.", "B", citation_anchor="b"),
    ]
    result = chunk(blocks, size=20, overlap=3, revision_id=revision_id)
    assert [(c.text, c.heading, c.citation_anchor) for c in result] == [
        (b.text, b.heading, b.citation_anchor) for b in blocks
    ]
    assert all(c.revision_id == revision_id for c in result)
    with pytest.raises(FrozenInstanceError):
        result[0].text = "changed"


def test_large_table_repeats_caption_and_header_without_splitting_rows():
    block = ExtractedBlock(
        "Deadlines\nCampus | Date\nHammond | September 30\nWestville | October 1",
        "Registration",
        "Deadlines",
        "dates",
    )
    result = chunk([block], size=11, overlap=2)
    assert [c.text for c in result] == [
        "Deadlines\nCampus | Date\nHammond | September 30",
        "Deadlines\nCampus | Date\nWestville | October 1",
    ]
    assert all(c.table_context == "Deadlines" and c.citation_anchor == "dates" for c in result)


def test_oversized_table_row_fails_without_truncation():
    with pytest.raises(ValueError, match="table"):
        chunk(
            [ExtractedBlock("A | B\none two three four | five six", table_context="Table")],
            size=6,
            overlap=0,
        )


def test_empty_input_and_blank_block():
    assert chunk([], size=10, overlap=0) == []
    with pytest.raises(ValueError):
        chunk([ExtractedBlock(" ")], size=10, overlap=0)


@pytest.mark.parametrize("size,overlap", [(True, 0), (10, False), (2.5, 0), (10, 1.5)])
def test_budgets_must_be_integers(size, overlap):
    with pytest.raises(ValueError):
        chunk([], size=size, overlap=overlap)


@pytest.fixture
def calendar_blocks():
    from fixtures.corpus import SOURCES

    from app.ingestion.extract import extract_document

    return extract_document(
        (SOURCES / "academic-calendar.pdf").read_bytes(), media_type="application/pdf"
    )


def test_calendar_continuations_keep_exact_year_and_semester(calendar_blocks):
    result = chunk(calendar_blocks)
    expected = {
        "page=2": (" | 2021-2022 | 2022-2023 | 2023-2024 | 2024-2025 | 2025-2026", "Summer"),
        "page=3": (" | 2026-2027 | 2027-2028 | 2028-2029 | 2029-2030 | 2030-2031", "Summer"),
        "page=4": (" |  | 2031-2032 | 2032-2033 | 2033-2034 | 2034-2035", "Spring Semester"),
        "page=5": (" |  | 2035-2036 |  |  | ", "Spring Semester"),
    }
    for anchor, (header, semester) in expected.items():
        first = next(c for c in result if c.citation_anchor == anchor)
        assert first.text.splitlines()[:2] == [header, semester]
    for semester in ("Spring Semester", "Summer"):
        last = next(c for c in result if c.citation_anchor == "page=5" and semester in c.text)
        assert last.text.startswith(expected["page=5"][0] + "\n" + semester)
        assert "page=4" in last.table_context  # Header provenance, row citation stays page 5.


def test_calendar_every_row_keeps_original_column_mapping(calendar_blocks):
    import re

    result = chunk(calendar_blocks, size=100, overlap=10)
    header = None
    semester = None
    expected = []
    for block in calendar_blocks:
        for line in block.text.splitlines():
            cells = [c.strip() for c in line.split("|")]
            if "|" in line and any(re.fullmatch(r"\d{4}-\d{4}", c) for c in cells[1:]):
                header = line
                semester = None
            elif line in {"Fall Semester", "Spring Semester", "Summer"}:
                semester = line
            elif "|" in line:
                expected.append((block.citation_anchor, header, semester, line))
    actual = []
    for c in result:
        if not c.table_context:
            continue
        lines = c.text.splitlines()
        assert len(c.text.split()) <= 100
        assert lines[1] in {"Fall Semester", "Spring Semester", "Summer"}
        actual.extend((c.citation_anchor, lines[0], lines[1], row) for row in lines[2:])
    assert actual == expected
    assert result == chunk(calendar_blocks, size=100, overlap=10)


@pytest.mark.parametrize(
    "mutation", ["missing", "gap", "width", "section", "prose", "empty_column"]
)
def test_ambiguous_calendar_continuation_fails_closed(calendar_blocks, mutation):
    from dataclasses import replace

    blocks = list(calendar_blocks)
    if mutation == "missing":
        blocks = blocks[1:]
    elif mutation == "gap":
        blocks.pop(1)
    elif mutation == "width":
        blocks[1] = replace(blocks[1], text=blocks[1].text.replace(" | July 10", "", 1))
    elif mutation == "section":
        blocks[0] = replace(
            blocks[0], text=blocks[0].text.replace("Summer\n", "Unknown semester\n")
        )
    elif mutation == "prose":
        blocks.insert(1, ExtractedBlock("A separate document section."))
    else:
        blocks[4] = replace(
            blocks[4], text=blocks[4].text.replace(" |  | January 14", " | January 14 | January 14")
        )
    with pytest.raises(ValueError, match="manual review"):
        chunk(blocks)
