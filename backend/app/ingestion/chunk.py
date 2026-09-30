"""Pure word-budget chunking; governance metadata stays on the linked revision."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.ingestion.extract import ExtractedBlock


@dataclass(frozen=True)
class DocumentChunk:
    ordinal: int
    text: str
    heading: str
    table_context: str
    citation_anchor: str | None
    revision_id: UUID | None = None


_SEMESTERS = {"Fall Semester", "Spring Semester", "Summer"}


def _emit_pdf_rows(
    output: list[ExtractedBlock],
    block: ExtractedBlock,
    rows: list[str],
    header: str,
    section: str,
    header_anchor: str | None,
    section_anchor: str | None,
) -> None:
    if rows:
        output.append(
            ExtractedBlock(
                "\n".join([header, section, *rows]),
                block.heading,
                f"{block.table_context}; year columns ({header_anchor}): {header}; "
                f"section ({section_anchor}): {section}",
                block.citation_anchor,
            )
        )
        rows.clear()


def _pdf_calendar_blocks(blocks: Sequence[ExtractedBlock]) -> list[ExtractedBlock]:
    """Repeat only explicit year cells/semester labels in adjacent PDF pages.

    This narrow grammar relies on T022's verified cell boundaries. Empty header
    cells stay empty; a new header replaces, rather than merges, the old schema.
    No dates, years, section labels or column positions are derived from values.
    """
    output: list[ExtractedBlock] = []
    header = section = ""
    header_anchor = section_anchor = None
    columns: list[str] = []
    previous_page: int | None = None
    previous_heading = ""
    for block in blocks:
        page_match = re.fullmatch(r"PDF page (\d+)", block.table_context)
        if not page_match:
            header = section = ""
            previous_page = None
            output.append(block)
            continue
        page = int(page_match[1])
        if previous_page is None or page != previous_page + 1 or block.heading != previous_heading:
            header = section = ""
            columns = []
        previous_page, previous_heading = page, block.heading
        rows: list[str] = []
        prefix: list[str] = []

        for line in block.text.splitlines():
            if not line.strip():
                continue
            if "|" in line:
                cells = [cell.strip() for cell in line.split("|")]
                is_header = (
                    not cells[0]
                    and any(re.fullmatch(r"\d{4}-\d{4}", c) for c in cells[1:])
                    and all(not c or re.fullmatch(r"\d{4}-\d{4}", c) for c in cells[1:])
                )
                if is_header:
                    _emit_pdf_rows(
                        output, block, rows, header, section, header_anchor, section_anchor
                    )
                    if prefix:
                        output.append(
                            ExtractedBlock(
                                "\n".join(prefix),
                                block.heading,
                                citation_anchor=block.citation_anchor,
                            )
                        )
                        prefix.clear()
                    named = [c for c in cells[1:] if c]
                    if len(named) != len(set(named)):
                        raise ValueError("Duplicate year columns require manual review")
                    header, columns, header_anchor = line, cells, block.citation_anchor
                    section = ""
                else:
                    if (
                        not header
                        or not section
                        or prefix
                        or len(cells) != len(columns)
                        or any(
                            value and not year
                            for value, year in zip(cells[1:], columns[1:], strict=True)
                        )
                    ):
                        raise ValueError("Ambiguous PDF table continuation requires manual review")
                    rows.append(line)
            elif line in _SEMESTERS:
                _emit_pdf_rows(output, block, rows, header, section, header_anchor, section_anchor)
                if not header or prefix:
                    raise ValueError("PDF semester lacks year columns; manual review required")
                section, section_anchor = line, block.citation_anchor
            elif not header:
                prefix.append(line)
            else:
                raise ValueError("Unknown PDF table section requires manual review")
        _emit_pdf_rows(output, block, rows, header, section, header_anchor, section_anchor)
        if prefix:
            # A page without table rows cannot establish a continuation schema.
            raise ValueError("PDF table lacks explicit year columns; manual review required")
    return output


def _table_parts(block: ExtractedBlock, size: int) -> list[str]:
    if len(block.text.split()) <= size:
        return [block.text]
    lines = block.text.splitlines()
    first_row = next((i for i, line in enumerate(lines) if "|" in line), None)
    # PDF calendar groups have already been validated and scoped to one schema.
    if first_row is None:
        raise ValueError("Oversized table requires manual review or a larger chunk size")
    if "; year columns (" in block.table_context:
        header = lines[:2]
        rows = lines[2:]
    else:
        header = lines[: first_row + 1]
        rows = lines[first_row + 1 :]
    if not rows or any("|" not in row for row in rows):
        raise ValueError("Oversized table structure requires manual review")
    parts = []
    current = list(header)
    for row in rows:
        if len("\n".join([*header, row]).split()) > size:
            raise ValueError("Complete table row and header exceed chunk size")
        if len("\n".join([*current, row]).split()) > size:
            parts.append("\n".join(current))
            current = list(header)
        current.append(row)
    parts.append("\n".join(current))
    return parts


def chunk_document(
    blocks: Sequence[ExtractedBlock],
    *,
    size: int = 800,
    overlap: int = 100,
    revision_id: UUID | None = None,
) -> list[DocumentChunk]:
    """Preserve citations and metadata, splitting at semantic and budget boundaries.

    Size/overlap are whitespace-separated word counts (not provider tokens).
    Prose overlap applies within a block only. Table splits repeat the caption
    and first row as a header; PDF calendar splits repeat explicit year columns
    and semester labels. Rows stay intact, with no word overlap. Ambiguous PDF
    continuations fail closed. The caller must bind the collected revision
    when persisting: revision → source retains URL/title/office, campus/program/
    course/term, effective dates and immutable hash without copying or inference.
    """
    if type(size) is not int or type(overlap) is not int or not 0 <= overlap < size:
        raise ValueError("Chunk size must be positive and overlap between zero and size")
    chunks: list[DocumentChunk] = []
    for block in _pdf_calendar_blocks(blocks):
        words = block.text.split()
        if not words:
            raise ValueError("Cannot chunk an empty extracted block")
        if block.table_context:
            parts = _table_parts(block, size)
        elif len(words) <= size:
            parts = [block.text]
        else:
            parts = []
            start = 0
            while start < len(words):
                end = min(start + size, len(words))
                parts.append(" ".join(words[start:end]))
                if end == len(words):
                    break
                start = end - overlap
        for text in parts:
            chunks.append(
                DocumentChunk(
                    len(chunks),
                    text,
                    block.heading,
                    block.table_context,
                    block.citation_anchor,
                    revision_id,
                )
            )
    return chunks
