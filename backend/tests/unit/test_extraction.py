"""T022 extraction behavior and all-or-nothing failures, independent of later tasks."""

from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fixtures.corpus import SOURCES
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.ingestion.extract import DOCX, ExtractionError, _pdf_cell_text, extract_document


def html(text):
    return extract_document(text.encode(), media_type="text/html; charset=utf-8")


def docx(body):
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:body>' + body + "</w:body></w:document>"
            ),
        )
    return stream.getvalue()


def paragraph(text, properties=""):
    return f"<w:p>{properties}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>"


def test_html_inline_wording_comments_sections_and_structural_anchors():
    blocks = html("""<main><h1>Rules</h1><!-- boilerplate -->
    <section id="first"><h2>Fall 2026</h2><p>No <strong>late</strong> forms.</p>
    <p>Keep CS 50000 &amp; CS 40000.</p></section>
    <h2 id="second">Spring 2027</h2><p>Ask your advisor.</p></main>""")
    assert [b.text for b in blocks] == [
        "Rules",
        "Fall 2026",
        "No late forms.",
        "Keep CS 50000 & CS 40000.",
        "Spring 2027",
        "Ask your advisor.",
    ]
    assert blocks[2].heading == "Rules > Fall 2026"
    assert blocks[2].citation_anchor == "first"
    assert blocks[-1].heading == "Rules > Spring 2027"
    assert blocks[-1].citation_anchor == "second"


def test_html_boilerplate_and_hidden_content_removed():
    blocks = html("""<div role="banner">Banner</div><nav>Menu</nav><main>
    <p>Actual policy.</p><p hidden>Hidden</p><p aria-hidden="true">Secret</p>
    <form>Search</form><style>CSS</style><script>JS</script></main><footer>Footer</footer>""")
    assert [b.text for b in blocks] == ["Actual policy."]


def test_real_html_snapshot_is_readable_without_site_menu():
    blocks = extract_document((SOURCES / "registrar.html").read_bytes(), media_type="text/html")
    text = "\n".join(b.text for b in blocks)
    assert "Registrar" in text
    assert "Skip to content" not in text
    assert "gtm.start" not in text


@pytest.mark.parametrize(
    "content",
    [
        b"<main><p>bad\xff</p></main>",
        b"<p>bad\x00</p>",
        b"not HTML",
        b"<main><p>&#xfffd;</p></main>",
    ],
)
def test_invalid_or_unreadable_html_rejected(content):
    with pytest.raises(ExtractionError):
        extract_document(content, media_type="text/html")


@pytest.mark.parametrize(
    "table",
    [
        '<tr><td colspan="2">A</td></tr>',
        "<tr><td>A</td><td>B</td></tr><tr><td>C</td></tr>",
        "<tr><td><table><tr><td>N</td></tr></table></td></tr>",
        "<tr></tr>",
        "",
    ],
)
def test_unreliable_html_table_rejects_entire_document(table):
    with pytest.raises(ExtractionError):
        html(f"<main><p>Valid text.</p><table>{table}</table></main>")


def test_html_table_keeps_empty_cells_and_row_boundaries():
    block = html(
        '<table id="dates"><caption>Deadlines</caption>'
        "<tr><th>Campus</th><th>Date</th></tr>"
        "<tr><td>Hammond</td><td></td></tr></table>"
    )[0]
    assert block.text == "Deadlines\nCampus | Date\nHammond | "
    assert block.table_context == "Deadlines"
    assert block.citation_anchor == "dates"


def test_docx_preserves_headings_bookmarks_lists_and_table():
    body = paragraph(
        "Course rules",
        '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:bookmarkStart w:id="1" w:name="course"/>',
    )
    body += paragraph(
        "Contact your advisor.", '<w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr>'
    )
    body += (
        "<w:tbl>"
        + "".join(
            "<w:tr>" + "".join("<w:tc>" + paragraph(cell) + "</w:tc>" for cell in row) + "</w:tr>"
            for row in [["Course", "Term"], ["CS 50000", "Fall 2026"]]
        )
        + "</w:tbl>"
    )
    blocks = extract_document(docx(body), media_type=DOCX)
    assert [b.text for b in blocks] == [
        "Course rules",
        "Contact your advisor.",
        "Course | Term\nCS 50000 | Fall 2026",
    ]
    assert all(b.heading == "Course rules" and b.citation_anchor == "course" for b in blocks)
    assert blocks[-1].table_context == "Course rules"
    assert extract_document(docx(body), media_type=DOCX) == blocks


@pytest.mark.parametrize(
    "bad",
    [
        "<w:p><w:r><w:drawing/></w:r></w:p>",
        "<w:altChunk/>",
        "<w:p><w:del/></w:p>",
        "<w:p><w:r><w:fldChar/></w:r></w:p>",
        '<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'
        "<w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc></w:tr></w:tbl>",
    ],
)
def test_docx_unsupported_content_rejects_otherwise_readable_document(bad):
    with pytest.raises(ExtractionError):
        extract_document(docx(paragraph("Valid policy.") + bad), media_type=DOCX)


def test_docx_entities_rejected():
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", '<!DOCTYPE foo [<!ENTITY x "fake">]><foo/>')
    with pytest.raises(ExtractionError):
        extract_document(stream.getvalue(), media_type=DOCX)


def test_all_real_pdf_pages_have_text_and_page_locators():
    blocks = extract_document(
        (SOURCES / "academic-calendar.pdf").read_bytes(), media_type="application/pdf"
    )
    assert len(blocks) == 5
    assert [b.citation_anchor for b in blocks] == [f"page={n}" for n in range(1, 6)]
    assert "2026" in "\n".join(b.text for b in blocks)
    assert any(b.table_context and " | " in b.text for b in blocks)


def test_pdf_blank_page_rejects_entire_document():
    writer = PdfWriter()
    writer.add_page(PdfReader(SOURCES / "academic-calendar.pdf").pages[0])
    writer.add_blank_page(width=600, height=800)
    stream = BytesIO()
    writer.write(stream)
    with pytest.raises(ExtractionError):
        extract_document(stream.getvalue(), media_type="application/pdf")


def test_pdf_encryption_rejected_even_with_empty_password():
    writer = PdfWriter()
    writer.add_page(PdfReader(SOURCES / "academic-calendar.pdf").pages[0])
    writer.encrypt("")
    stream = BytesIO()
    writer.write(stream)
    with pytest.raises(ExtractionError):
        extract_document(stream.getvalue(), media_type="application/pdf")


def test_pdf_image_page_with_readable_header_rejected():
    writer = PdfWriter()
    page = writer.add_page(PdfReader(SOURCES / "academic-calendar.pdf").pages[0])
    image = DecodedStreamObject()
    image.set_data(b"\x00")
    image.update(
        {NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image")}
    )
    page["/Resources"][NameObject("/XObject")] = DictionaryObject(
        {NameObject("/Scan"): writer._add_object(image)}
    )
    stream = BytesIO()
    writer.write(stream)
    with pytest.raises(ExtractionError):
        extract_document(stream.getvalue(), media_type="application/pdf")


def test_byte_limit(monkeypatch):
    monkeypatch.setattr("app.ingestion.extract.MAX_BYTES", 5)
    with pytest.raises(ExtractionError):
        html("<p>Too large.</p>")


def test_nested_main_does_not_duplicate_content_and_inline_breaks_keep_words():
    blocks = html(
        '<main><div role="main"><p><span>Submit<br>before</span> September 30.</p></div></main>'
    )
    assert [b.text for b in blocks] == ["Submit before September 30."]


def test_html_table_cell_paragraphs_remain_separate_words():
    block = html("<table><tr><td><p>First</p><p>Second</p></td></tr></table>")[0]
    assert block.text == "First Second"


def test_docx_table_cell_paragraphs_remain_separate_words():
    body = "<w:tbl><w:tr><w:tc>" + paragraph("First") + paragraph("Second")
    body += "</w:tc></w:tr></w:tbl>"
    assert extract_document(docx(body), media_type=DOCX)[0].text == "First Second"


def test_docx_archive_expansion_limit(monkeypatch):
    monkeypatch.setattr("app.ingestion.extract.MAX_EXPANDED_BYTES", 10)
    with pytest.raises(ExtractionError):
        extract_document(docx(paragraph("Readable policy.")), media_type=DOCX)


def test_empty_html_table_rejects_entire_document():
    with pytest.raises(ExtractionError):
        html("<p>Valid policy.</p><table><tr><td></td><td></td></tr></table>")


def test_empty_docx_table_rejects_entire_document():
    with pytest.raises(ExtractionError):
        extract_document(
            docx(paragraph("Valid policy.") + "<w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>"),
            media_type=DOCX,
        )


@pytest.mark.parametrize(
    "expected",
    [
        "First day of class | January 10 | January 9 | January 8 | January 13 | January 12",
        "Martin Luther King Day | January 17 | January 16 | January 15 | January 20 | January 19",
        "Spring Break (Sun-Sat) | March 14-19 | March 13-18 | March 11-16 "
        "| March 17-22 | March 16-21",
        "First day of class: 12 wk, 1st 6wk, & 1st 4 wk modules "
        "| May 16 | May 15 | May 13 | May 19 | May 18",
    ],
)
def test_calendar_exact_spring_dates_and_summer_label(expected):
    blocks = extract_document(
        (SOURCES / "academic-calendar.pdf").read_bytes(), media_type="application/pdf"
    )
    assert expected in blocks[0].text.splitlines()
    assert len(expected.split(" | ")) == 6


def test_calendar_continued_and_wrapped_module_rows_keep_columns():
    blocks = extract_document(
        (SOURCES / "academic-calendar.pdf").read_bytes(), media_type="application/pdf"
    )
    assert "3rd 4 wk module begins | July 11 | July 10 | July 8 | July 14 | July 13" in (
        blocks[1].text.splitlines()
    )
    assert "3rd 4 wk module begins | July 12 | July 10 | July 9 | July 8 | July 14" in (
        blocks[2].text.splitlines()
    )
    assert "3rd 4 wk module begins |  | July 14 |  |  | " in blocks[4].text.splitlines()
    assert (
        extract_document(
            (SOURCES / "academic-calendar.pdf").read_bytes(), media_type="application/pdf"
        )
        == blocks
    )


@pytest.mark.parametrize("page", range(5))
def test_calendar_section_headings_are_separate_from_event_rows(page):
    blocks = extract_document(
        (SOURCES / "academic-calendar.pdf").read_bytes(), media_type="application/pdf"
    )
    lines = blocks[page].text.splitlines()
    headings = ["Fall Semester", "Spring Semester", "Summer"]
    expected = {
        0: headings, 1: headings, 2: headings[:2], 3: ["Summer", "Fall Semester"],
        4: headings[1:],
    }
    for heading in expected[page]:
        assert heading in lines
        assert not any(line.startswith(heading + " ") for line in lines)
        if heading != "Fall Semester":
            assert lines[lines.index(heading) + 1].startswith("First day of class")
            assert len(lines[lines.index(heading) + 1].split(" | ")) == 6
    if "Fall Semester" in expected[page]:
        # Approval/reference values stay in their original year columns.
        assert len(lines[lines.index("Fall Semester") + 1].split(" | ")) == 6


@pytest.mark.parametrize("page, date", [(1, "November 20-23"), (2, "November 22-25")])
def test_calendar_wrapped_date_ranges_preserve_original_value(page, date):
    blocks = extract_document(
        (SOURCES / "academic-calendar.pdf").read_bytes(), media_type="application/pdf"
    )
    row = next(line for line in blocks[page].text.splitlines()
               if line.startswith("Thanksgiving holiday |"))
    assert len(row.split(" | ")) == 6
    assert row.split(" | ")[-1] == date


@pytest.mark.parametrize("text", ["November 20-\nreview", "policy-\n23", "November 20- 23"])
def test_pdf_date_repair_does_not_guess_ambiguous_hyphen_continuations(monkeypatch, text):
    monkeypatch.setattr("app.ingestion.extract.geometry_text", lambda chars: text)
    assert _pdf_cell_text([]) == text


def changed_calendar_page(index, extra):
    writer = PdfWriter()
    writer.add_page(PdfReader(SOURCES / "academic-calendar.pdf").pages[0])
    page = writer.add_page(PdfReader(SOURCES / "academic-calendar.pdf").pages[index])
    stream = DecodedStreamObject()
    stream.set_data(page.get_contents().get_data() + extra)
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_ambiguous_overlapping_cells_reject_even_after_valid_page():
    # Add a second cell covering the same text: never choose an arbitrary cell.
    content = changed_calendar_page(2, b"\nq 19 524 m 163 524 l 163 554 l 19 554 l h W n Q\n")
    with pytest.raises(ExtractionError, match="manual review"):
        extract_document(content, media_type="application/pdf")


def test_pdf_text_crossing_inferred_column_rejects_entire_document():
    # Reuse an existing mapped glyph and place it across a year-column gutter.
    content = changed_calendar_page(0, b"\nBT /TT0 1 Tf 40 0 0 12 274 250 Tm (0) Tj ET\n")
    with pytest.raises(ExtractionError, match="manual review"):
        extract_document(content, media_type="application/pdf")
