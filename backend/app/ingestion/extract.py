"""Pure, all-or-nothing extraction of governed HTML, digital PDF and DOCX bytes.

No network, OCR, policy inference or publication. Blocks retain source wording;
HTML fragments, PDF page locators and DOCX bookmarks remain citation metadata.
"""

import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from threading import get_ident
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pdfplumber
from bs4 import BeautifulSoup
from bs4.element import Comment, NavigableString, Tag
from pdfplumber.utils.text import extract_text as geometry_text
from pypdf import PdfReader

MAX_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 40 * 1024 * 1024
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ExtractionError(ValueError):
    """The complete document cannot be reliably extracted; nothing is returned."""


@dataclass(frozen=True)
class ExtractedBlock:
    text: str
    heading: str = ""
    table_context: str = ""
    citation_anchor: str | None = None


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    if "\ufffd" in text or any(
        unicodedata.category(c) == "Cc" and c not in "\n\r\t\f" for c in text
    ):
        raise ExtractionError("Document contains undecodable or unreadable text")
    return " ".join(text.split())


def _html(content: bytes) -> list[ExtractedBlock]:
    # Decode strictly; never replace undecodable bytes with apparently valid policy.
    soup = BeautifulSoup(content.decode("utf-8-sig"), "html.parser")
    if not soup.find():
        raise ExtractionError("HTML markup is required")
    for node in list(
        soup.select(
            "head, nav, footer, script, style, noscript, template, form, "
            '[role="navigation"], [role="banner"], [role="contentinfo"], '
            '[hidden], [aria-hidden="true"]'
        )
    ):
        node.decompose()
    roots: list[Tag] = list(soup.select('main, [role="main"]'))
    roots = [root for root in roots if not any(parent in roots for parent in root.parents)]
    if not roots:
        roots = [soup.body or soup]
    for line_break in soup.find_all("br"):
        line_break.replace_with(" ")
    blocks: list[ExtractedBlock] = []
    headings: dict[int, str] = {}
    section_anchor: str | None = None

    def emit(text: str, anchor: str | None, table: str = "") -> None:
        cleaned = _clean(text)
        if cleaned:
            blocks.append(
                ExtractedBlock(
                    cleaned, " > ".join(headings.values()), table, anchor or section_anchor
                )
            )

    def walk(node: Tag, anchor: str | None = None) -> None:
        nonlocal section_anchor
        own = node.get("id") or (node.get("name") if node.name == "a" else None)
        if isinstance(own, str) and own:
            anchor = own
        if re.fullmatch(r"h[1-6]", node.name or ""):
            level = int(node.name[1])
            for key in list(headings):
                if key >= level:
                    del headings[key]
            headings[level] = _clean(node.get_text())
            section_anchor = anchor
            emit(node.get_text(), anchor)
            return
        if node.name == "table":
            if node.find("table"):
                raise ExtractionError("Nested HTML tables require manual review")
            rows = []
            width = None
            for row in node.find_all("tr"):
                cells = row.find_all(["td", "th"], recursive=False)
                if not cells:
                    raise ExtractionError("HTML table row has no cells")
                if any(c.get("rowspan", "1") != "1" or c.get("colspan", "1") != "1" for c in cells):
                    raise ExtractionError("Merged HTML table cells require manual review")
                if width is not None and width != len(cells):
                    raise ExtractionError("HTML table has inconsistent columns")
                width = len(cells)
                rows.append(" | ".join(_clean(c.get_text(" ")) for c in cells))
            caption = node.find("caption")
            context = _clean(caption.get_text()) if caption else " > ".join(headings.values())
            if not rows or not any(_clean(row.replace("|", "")) for row in rows):
                raise ExtractionError("HTML table is unreadable")
            # Keep line boundaries rather than flattening row/column relationships.
            blocks.append(
                ExtractedBlock(
                    "\n".join(([context] if caption else []) + rows),
                    " > ".join(headings.values()),
                    context,
                    anchor or section_anchor,
                )
            )
            return
        buffer: list[str] = []
        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                buffer.append(str(child))
            elif isinstance(child, Tag):
                if child.name == "br":
                    buffer.append(" ")
                elif child.name in {"a", "span", "strong", "em", "b", "i", "u", "sup", "sub"}:
                    buffer.append(child.get_text())
                    child_anchor = child.get("id") or child.get("name")
                    if isinstance(child_anchor, str):
                        anchor = child_anchor
                else:
                    emit("".join(buffer), anchor)
                    buffer.clear()
                    walk(child, anchor)
        emit("".join(buffer), anchor)

    for root in roots:
        walk(root)
    return blocks


class _PdfWarnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.warned = False
        self.thread = get_ident()

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread == self.thread and record.levelno >= logging.WARNING:
            self.warned = True


PdfBox = tuple[float, float, float, float]
PdfChar = dict[str, Any]


def _pdf_cell_text(chars: list[PdfChar]) -> str:
    text = geometry_text(chars)
    # Only join a complete month/day range whose numeric endpoint wrapped.
    # Do not repair arbitrary hyphenated prose or uncertain continuations.
    if re.fullmatch(
        r"(?:January|February|March|April|May|June|July|August|September|October|"
        r"November|December) \d{1,2}-\n\d{1,2}", text
    ):
        text = text.replace("\n", "")
    return text


def _pdf_row(values: list[str]) -> list[str]:
    label_lines = values[0].splitlines()
    output = []
    if label_lines and label_lines[0].strip() in {"Fall Semester", "Spring Semester", "Summer"}:
        output.append(_clean(label_lines[0]))
        values = ["\n".join(label_lines[1:]), *values[1:]]
    values = [_clean(value) for value in values]
    if any(values) or not output:
        output.append(" | ".join(values))
    return output


def _pdf_geometry(
    page: Any, digital_page: Any, inherited: list[float] | None = None
) -> tuple[str, bool, list[float] | None]:
    """Use explicit rectangular clipping cells, or aligned year-header columns.

    This deliberately supports a narrow, verifiable table layout. Unruled
    multi-column documents without aligned headers require manual review.
    """
    chars = page.chars
    if not chars or any(not c["upright"] for c in chars):
        raise ExtractionError("PDF text geometry requires manual review")
    boxes = set()
    rectangles = set()
    path: list[tuple[float, float]] = []

    def operand(op: bytes, args: Any, cm: Any, tm: Any) -> None:
        if op == b"m":
            path[:] = [(float(args[0]), float(args[1]))]
        elif op == b"l":
            path.append((float(args[0]), float(args[1])))
        elif op == b"W" and len(path) == 4:
            points = [
                (x * cm[0] + y * cm[2] + cm[4], x * cm[1] + y * cm[3] + cm[5]) for x, y in path
            ]
            xs, ys = sorted(set(x for x, y in points)), sorted(set(y for x, y in points))
            if len(xs) != 2 or len(ys) != 2:
                raise ExtractionError("PDF nonrectangular cells require manual review")
            boxes.add((xs[0], page.height - ys[1], xs[1], page.height - ys[0]))
        elif op == b"re":
            x, y, width, height = map(float, args)
            if cm != [1, 0, 0, 1, 0, 0]:
                raise ExtractionError("Transformed PDF rectangles require manual review")
            rectangles.add((x, page.height - y - height, x + width, page.height - y))
            path.clear()
        elif op in (b"n", b"f", b"S", b"Q"):
            path.clear()

    digital_page.extract_text(visitor_operand_before=operand)
    if boxes:
        # Producers also emit rounded duplicate rectangle clips. Prefer the
        # precise polygon; retain rectangle-only cells.
        boxes.update(
            r
            for r in rectangles
            if not any(all(abs(a - b) <= 1.5 for a, b in zip(r, box, strict=True)) for box in boxes)
        )
        # A few complete rows have no clip operators. Reuse the explicit
        # columns only when the whole line lies in a gap between clipped rows.
        sample = min(boxes, key=lambda b: b[1])
        columns = sorted(b for b in boxes if b[1:2] == sample[1:2] and b[3] == sample[3])
        for line in page.extract_text_lines():
            if line["top"] < min(b[1] for b in boxes):
                continue
            if not any(b[1] <= (line["top"] + line["bottom"]) / 2 <= b[3] for b in boxes):
                if any(max(line["top"], b[1]) < min(line["bottom"], b[3]) for b in boxes):
                    raise ExtractionError("PDF row overlaps cells; manual review required")
                boxes.update((b[0], line["top"], b[2], line["bottom"]) for b in columns)
        cells: dict[PdfBox, list[PdfChar]] = {box: [] for box in boxes}
        prefix_chars = []
        first_top = min(b[1] for b in boxes)
        for char in chars:
            x, y = (char["x0"] + char["x1"]) / 2, (char["top"] + char["bottom"]) / 2
            matches = [b for b in boxes if b[0] <= x <= b[2] and b[1] <= y <= b[3]]
            if not matches and char["bottom"] <= first_top and inherited:
                prefix_chars.append(char)
                continue
            if not char["text"].strip() and len(matches) != 1:
                continue
            if len(matches) != 1:
                raise ExtractionError("PDF cell assignment is ambiguous; manual review required")
            box = matches[0]
            if char["text"].strip() and (char["x0"] < box[0] - 1 or char["x1"] > box[2] + 1):
                raise ExtractionError("PDF text crosses a cell boundary; manual review required")
            cells[box].append(char)
        rows: dict[tuple[float, float], list[PdfBox]] = {}
        for box in sorted(boxes):
            rows.setdefault((box[1], box[3]), []).append(box)
        lines = []
        if prefix_chars:
            prefix = page.filter(
                lambda obj: obj.get("object_type") == "char" and obj in prefix_chars
            )
            prefix_text, _, _ = _pdf_unruled(prefix, inherited)
            lines.append(prefix_text)
        for _, row in sorted(rows.items()):
            row.sort()
            if any(abs(left[2] - right[0]) > 1 for left, right in zip(row, row[1:], strict=False)):
                raise ExtractionError("PDF table has inconsistent cells; manual review required")
            lines.extend(_pdf_row([_pdf_cell_text(cells[b]) for b in row]))
        last_row = sorted(rows.items())[-1][1]
        return "\n".join(lines), True, [b[0] for b in sorted(last_row)[1:]]

    return _pdf_unruled(page, inherited)


def _pdf_unruled(
    page: Any, inherited: list[float] | None = None
) -> tuple[str, bool, list[float] | None]:
    chars = page.chars
    lines = page.extract_text_lines()
    headers = [
        line for line in lines if re.fullmatch(r"\d{4}-\d{4}(?: \d{4}-\d{4})+", line["text"])
    ]
    if len(headers) == 1 or inherited:
        header = headers[0] if headers else {"top": -1}
        words = page.extract_words()
        years = [w for w in words if abs(w["top"] - header["top"]) < 1]
        # Find an actual empty vertical gutter immediately before each
        # aligned header; reject if the body leaves no such boundary.
        body = [c for c in chars if c["top"] >= header["top"] - 1]
        boundaries = list(inherited or []) if not headers else []
        for word in years if headers else []:
            candidates = [word["x0"] - offset / 2 for offset in range(24, 1, -1)]
            gutter = next(
                (
                    b
                    for b in candidates
                    if not any(c["x0"] - 1 < b < c["x1"] + 1 for c in body if c["text"].strip())
                ),
                None,
            )
            if gutter is None:
                raise ExtractionError("PDF columns have no clear gutter; manual review required")
            boundaries.append(gutter)
        output = []
        pending = ""
        for line in lines:
            if line["top"] < header["top"] - 1:
                output.append(_clean(line["text"]))
                continue
            cells: list[list[PdfChar]] = [[] for _ in range(len(boundaries) + 1)]
            for char in chars:
                if not (
                    line["top"] - 0.1 <= char["top"] and char["bottom"] <= line["bottom"] + 0.1
                ):
                    continue
                if not char["text"].strip() and any(
                    char["x0"] < b < char["x1"] for b in boundaries
                ):
                    continue
                if any(char["x0"] < b < char["x1"] for b in boundaries):
                    raise ExtractionError(
                        "PDF text crosses inferred columns; manual review required"
                    )
                column = sum(char["x0"] >= b for b in boundaries)
                cells[column].append(char)
            values = [_pdf_cell_text(c) for c in cells]
            if pending:
                values[0] = pending + " " + values[0]
                pending = ""
            if values[0].endswith(",") and not any(values[1:]):
                pending = values[0]
                continue
            output.extend(_pdf_row(values))
        if pending:
            raise ExtractionError("PDF incomplete table row requires manual review")
        return "\n".join(output), True, boundaries
    # Never convert whitespace gaps into guessed table cells.
    if any(re.search(r"\S {2,}\S", line) for line in page.extract_text(layout=True).splitlines()):
        raise ExtractionError("PDF columns lack reliable boundaries; manual review required")
    return page.extract_text() or "", False, None


def _pdf(content: bytes) -> list[ExtractedBlock]:
    if not content.startswith(b"%PDF-"):
        raise ExtractionError("PDF signature is missing")
    handler = _PdfWarnings()
    logger = logging.getLogger("pypdf")
    logger.addHandler(handler)
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted or not 0 < len(reader.pages) <= 500:
            raise ExtractionError("Encrypted, empty or oversized PDF is unsupported")
        blocks = []
        geometry = pdfplumber.open(BytesIO(content))
        boundaries = None
        for number, page in enumerate(reader.pages, 1):
            if page.get("/Resources", {}).get("/XObject"):
                raise ExtractionError("PDF images or embedded forms require manual review")
            stream = page.get_contents()
            if stream is None or len(stream.get_data()) > MAX_EXPANDED_BYTES:
                raise ExtractionError("PDF page is empty or exceeds extraction limits")
            text, is_table, boundaries = _pdf_geometry(geometry.pages[number - 1], page, boundaries)
            if not _clean(text):
                raise ExtractionError("Every PDF page must have readable digital text")
            blocks.append(
                ExtractedBlock(
                    text,
                    citation_anchor=f"page={number}",
                    table_context=f"PDF page {number}" if is_table else "",
                )
            )
        if handler.warned:
            raise ExtractionError("PDF parser reported unreliable content")
        return blocks
    finally:
        if "geometry" in locals():
            geometry.close()
        logger.removeHandler(handler)


def _xml_text(node: ET.Element) -> str:
    parts: list[str] = []
    for item in node.iter():
        if item.tag == W + "p" and parts:
            parts.append(" ")
        elif item.tag == W + "t":
            parts.append(item.text or "")
        elif item.tag in {W + "tab", W + "br", W + "cr"}:
            parts.append(" ")
    return _clean("".join(parts))


def _docx(content: bytes) -> list[ExtractedBlock]:
    with ZipFile(BytesIO(content)) as archive:
        names = archive.namelist()
        if (
            len(names) != len(set(names))
            or len(names) > 1000
            or sum(item.file_size for item in archive.infolist()) > MAX_EXPANDED_BYTES
        ):
            raise ExtractionError("DOCX archive exceeds extraction limits or repeats entries")
        xml = archive.read("word/document.xml")
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ExtractionError("DOCX XML declarations are unsupported")
    document = ET.fromstring(xml)
    body = document.find(W + "body")
    if document.tag != W + "document" or body is None:
        raise ExtractionError("DOCX document body is missing")
    # Do not silently drop tracked changes, fields, drawings, or embedded content.
    unsupported = {
        "drawing",
        "pict",
        "object",
        "altChunk",
        "del",
        "ins",
        "fldChar",
        "instrText",
        "footnoteReference",
        "endnoteReference",
        "sym",
    }
    if any(node.tag in {W + name for name in unsupported} for node in body.iter()):
        raise ExtractionError("DOCX has content requiring manual review")
    blocks = []
    headings: dict[int, str] = {}
    anchor = None
    for node in body:
        if node.tag == W + "sectPr":
            continue
        if node.tag not in {W + "p", W + "tbl"}:
            raise ExtractionError("Unsupported DOCX body structure")
        bookmarks = node.findall(".//" + W + "bookmarkStart")
        if bookmarks:
            anchor = bookmarks[0].get(W + "name")
        heading = " > ".join(headings.values())
        if node.tag == W + "tbl":
            if (
                node.findall(".//" + W + "tbl")
                or node.findall(".//" + W + "gridSpan")
                or (node.findall(".//" + W + "vMerge"))
            ):
                raise ExtractionError("Nested or merged DOCX tables require manual review")
            rows = [
                [_xml_text(cell) for cell in row.findall(W + "tc")]
                for row in node.findall(W + "tr")
            ]
            if (
                not rows
                or not rows[0]
                or any(len(row) != len(rows[0]) for row in rows)
                or not any(cell for row in rows for cell in row)
            ):
                raise ExtractionError("DOCX table is unreadable")
            blocks.append(
                ExtractedBlock("\n".join(" | ".join(row) for row in rows), heading, heading, anchor)
            )
        else:
            text = _xml_text(node)
            style = node.find(W + "pPr/" + W + "pStyle")
            match = (
                re.fullmatch(r"Heading([1-6])", style.get(W + "val", ""))
                if (style is not None)
                else None
            )
            if match:
                level = int(match[1])
                headings = {k: v for k, v in headings.items() if k < level}
                headings[level] = text
                heading = " > ".join(headings.values())
            if text:
                blocks.append(ExtractedBlock(text, heading, citation_anchor=anchor))
    return blocks


def extract_document(content: bytes, *, media_type: str) -> list[ExtractedBlock]:
    """Return deterministic immutable blocks or a safe error for the whole input.

    Supports UTF-8 HTML, unencrypted digital PDFs and simple WordprocessingML
    DOCX. Legacy Office, scans/OCR, merged tables and embedded documents require
    manual conversion/review. Callers retain governed source/revision metadata.
    """
    if not content or len(content) > MAX_BYTES:
        raise ExtractionError("Document is empty or exceeds the byte limit")
    extractors: dict[str, Callable[[bytes], list[ExtractedBlock]]] = {
        "text/html": _html,
        "application/pdf": _pdf,
        DOCX: _docx,
    }
    kind = media_type.split(";", 1)[0].strip().lower()
    if kind not in extractors:
        raise ExtractionError("Unsupported document media type")
    try:
        blocks: list[ExtractedBlock] = extractors[kind](content)
        if not blocks or not all(_clean(block.text) for block in blocks):
            raise ExtractionError("Document has no readable content")
        return blocks
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError("Document is malformed or cannot be reliably extracted") from None
