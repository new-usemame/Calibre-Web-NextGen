"""ISBN extraction from a book's stored text format is bounded and read-only."""
from types import SimpleNamespace
import os
import sys
import zipfile

import pytest


def _isbn13(prefix12):
    checksum = sum(int(digit) * (1 if index % 2 == 0 else 3)
                   for index, digit in enumerate(prefix12))
    return prefix12 + str((10 - checksum % 10) % 10)


def _epub(path, chapters):
    manifest = []
    spine = []
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/", b"")
        archive.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OPS/package.opf"/></rootfiles></container>',
        )
        for index, text in enumerate(chapters):
            item_id = f"chapter-{index}"
            href = f"text/chapter-{index}.xhtml"
            manifest.append(f'<item id="{item_id}" href="{href}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{item_id}"/>')
            archive.writestr(
                f"OPS/{href}",
                f"<html xmlns='http://www.w3.org/1999/xhtml'><body><p>{text}</p></body></html>",
            )
        archive.writestr(
            "OPS/package.opf",
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            + "".join(manifest)
            + "</manifest><spine>" + "".join(spine) + "</spine></package>",
        )


@pytest.mark.unit
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="parser sandbox is Linux-only")
def test_epub_returns_only_checksum_valid_candidates_in_front_back_remaining_order(tmp_path, monkeypatch):
    from cps.services import isbn_extract

    identifiers = [_isbn13(f"978{i:09d}") for i in range(16)]
    # This 13-digit value has the right prefix and length but the wrong check digit.
    bad_isbn = identifiers[0][:-1] + str((int(identifiers[0][-1]) + 1) % 10)
    book_dir = tmp_path / "Author" / "A Book"
    book_dir.mkdir(parents=True)
    path = book_dir / "A Book.epub"
    # Invalid candidates are omitted without hiding a nearby valid one.
    chapter_texts = [f"Chapter {i}. " + (f"ISBN {bad_isbn}. " if i == 0 else "")
                     + f"ISBN {isbn}." for i, isbn in enumerate(identifiers)]
    _epub(path, chapter_texts)
    book = SimpleNamespace(
        id=42, path="Author/A Book", data=[SimpleNamespace(name="A Book", format="EPUB")],
        identifiers=[SimpleNamespace(type="asin", val="B000000000")],
    )
    monkeypatch.setattr(isbn_extract.config, "get_book_path", lambda: str(tmp_path))

    result = isbn_extract.extract_candidates(book)

    expected_indexes = list(range(10)) + [15, 14, 13, 12, 11, 10]
    assert [item["isbn"] for item in result["candidates"]] == [identifiers[i] for i in expected_indexes]
    assert all(item["format"] == "epub" and "Chapter" in item["context"]
               for item in result["candidates"])
    assert result["available"] is True
    assert result["scanned_formats"] == ["epub"]
    assert bad_isbn not in {item["isbn"] for item in result["candidates"]}
    assert [(item.type, item.val) for item in book.identifiers] == [("asin", "B000000000")]


@pytest.mark.unit
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="parser sandbox is Linux-only")
def test_txt_format_extracts_candidate_and_marks_unsupported_formats(tmp_path, monkeypatch):
    from cps.services import isbn_extract

    book_dir = tmp_path / "A" / "B"
    book_dir.mkdir(parents=True)
    (book_dir / "B.txt").write_text("Publisher data: ISBN-13 978-0-306-40615-7", encoding="utf-8")
    book = SimpleNamespace(
        path="A/B",
        data=[SimpleNamespace(name="B", format="TXT"), SimpleNamespace(name="B", format="MOBI")],
    )
    monkeypatch.setattr(isbn_extract.config, "get_book_path", lambda: str(tmp_path))

    result = isbn_extract.extract_candidates(book)

    assert result["available"] is True
    assert result["candidates"] == [{
        "isbn": "9780306406157", "context": "Text: Publisher data: ISBN-13 978-0-306-40615-7", "format": "txt",
    }]
    assert result["unsupported_formats"] == ["MOBI"]
    assert result["unavailable_formats"] == []


@pytest.mark.unit
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="parser sandbox is Linux-only")
def test_pdf_format_uses_bounded_child_parser(tmp_path, monkeypatch):
    from cps.services import isbn_extract

    fixture = os.path.join(os.path.dirname(__file__), "..", "fixtures", "sample_books",
                           "three_page_sample.pdf")
    library = tmp_path / "library"
    book_dir = library / "A" / "PDF"
    book_dir.mkdir(parents=True)
    with open(fixture, "rb") as source:
        (book_dir / "PDF.pdf").write_bytes(source.read())
    book = SimpleNamespace(path="A/PDF", data=[SimpleNamespace(name="PDF", format="PDF")])
    monkeypatch.setattr(isbn_extract.config, "get_book_path", lambda: str(library))

    result = isbn_extract.extract_candidates(book)

    assert result["available"] is True
    assert result["scanned_formats"] == ["pdf"]
    assert result["failed_formats"] == []
    assert result["candidates"] == []
    assert result["truncated"] is False


@pytest.mark.unit
def test_pdf_sampling_reaches_actual_last_page_after_page_cap(monkeypatch):
    import sys as runtime_sys
    from types import ModuleType
    from cps.services import isbn_extract_worker

    last_isbn = "9780306406157"

    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    fake_pypdf = ModuleType("pypdf")
    fake_pypdf.PdfReader = lambda _stream, strict=False: type(
        "Reader", (), {"pages": [Page("") for _ in range(252 - 1)] + [Page(last_isbn)]}
    )()
    monkeypatch.setitem(runtime_sys.modules, "pypdf", fake_pypdf)
    candidates = {}

    truncated = isbn_extract_worker._scan_pdf(object(), "PDF", candidates)

    assert truncated is True
    assert candidates[last_isbn]["context"].startswith("Page 252:")


@pytest.mark.unit
def test_epub_sampling_uses_actual_back_chapters_before_scan_cap(tmp_path):
    from cps.services import isbn_extract_worker

    isbn = "9780306406157"
    path = tmp_path / "Long.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml",
                         '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                         '<rootfiles><rootfile full-path="package.opf"/></rootfiles></container>')
        manifest = []
        spine = []
        for index in range(252):
            item_id = f"c{index}"
            href = f"c{index}.xhtml"
            manifest.append(f'<item id="{item_id}" href="{href}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{item_id}"/>')
            body = f"ISBN {isbn}" if index == 251 else "No identifier here"
            archive.writestr(href, f"<html><body><p>{body}</p></body></html>")
        archive.writestr("package.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                         + "".join(manifest) + "</manifest><spine>" + "".join(spine)
                         + "</spine></package>")
    candidates = {}

    with open(path, "rb") as stream:
        truncated = isbn_extract_worker._scan_epub(stream, "EPUB", candidates)

    assert truncated is True
    assert candidates[isbn]["context"].startswith("Chapter c251.xhtml:")


@pytest.mark.unit
def test_parser_child_fails_closed_when_resource_limits_cannot_be_installed(monkeypatch, capsys):
    import json
    import resource
    from cps.services import isbn_extract_worker

    def deny_limits(*_args):
        raise OSError("resource limit denied")

    monkeypatch.setattr(resource, "setrlimit", deny_limits)
    isbn_extract_worker.main()

    output = json.loads(capsys.readouterr().out)
    assert output["sandboxed"] is False
    assert output["scanned_formats"] == []
    assert output["candidates"] == []


@pytest.mark.unit
def test_local_symlink_escape_is_rejected_before_the_worker_runs(tmp_path, monkeypatch):
    from cps.services import isbn_extract

    library = tmp_path / "library"
    book_dir = library / "A" / "B"
    book_dir.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("ISBN 9780306406157", encoding="utf-8")
    link = book_dir / "B.txt"
    link.symlink_to(outside)
    book = SimpleNamespace(path="A/B", data=[SimpleNamespace(name="B", format="TXT")])
    monkeypatch.setattr(isbn_extract.config, "get_book_path", lambda: str(library))
    monkeypatch.setattr(isbn_extract.sys, "platform", "linux")

    with pytest.raises(isbn_extract.ISBNExtractionError, match="unsafe_book_path"):
        isbn_extract.extract_candidates(book)


@pytest.mark.unit
def test_oversized_local_format_is_not_opened_by_parser(tmp_path, monkeypatch):
    from cps.services import isbn_extract

    library = tmp_path / "library"
    book_dir = library / "A" / "B"
    book_dir.mkdir(parents=True)
    path = book_dir / "B.txt"
    path.write_bytes(b"x")
    monkeypatch.setattr(isbn_extract.config, "get_book_path", lambda: str(library))
    monkeypatch.setattr(isbn_extract, "MAX_INPUT_BYTES", 0)
    monkeypatch.setattr(isbn_extract.sys, "platform", "linux")
    book = SimpleNamespace(path="A/B", data=[SimpleNamespace(name="B", format="TXT")])

    result = isbn_extract.extract_candidates(book)

    assert result["available"] is False
    assert result["unavailable_formats"] == ["TXT"]
    assert result["candidates"] == []


@pytest.mark.unit
def test_google_drive_source_is_explicitly_unavailable(monkeypatch):
    from cps.services import isbn_extract

    monkeypatch.setattr(isbn_extract.config, "config_use_google_drive", True, raising=False)
    with pytest.raises(isbn_extract.ISBNExtractionError, match="unsupported_storage"):
        isbn_extract.extract_candidates(SimpleNamespace(data=[]))


@pytest.mark.unit
def test_candidate_api_returns_suggestions_without_applying_them(monkeypatch):
    from flask import Flask
    from cps.api import edit

    book = SimpleNamespace(id=42, identifiers=[SimpleNamespace(type="asin", val="B000000000")])
    payload = {
        "available": True,
        "candidates": [{"isbn": "9780306406157", "context": "Page 3: ISBN 9780306406157", "format": "pdf"}],
        "scanned_formats": ["pdf"], "unsupported_formats": [], "failed_formats": [], "truncated": False,
    }
    monkeypatch.setattr(edit, "_require_edit", lambda: None)
    monkeypatch.setattr(edit, "_editable_book", lambda _book_id: book)
    monkeypatch.setattr(edit.isbn_extract, "extract_candidates", lambda _book: payload)
    app = Flask(__name__)
    with app.test_request_context("/api/v1/books/42/isbn-candidates", method="POST"):
        response = edit.extract_isbn_candidates.__wrapped__(42)

    assert response.get_json() == payload
    assert [(item.type, item.val) for item in book.identifiers] == [("asin", "B000000000")]


@pytest.mark.unit
def test_candidate_api_checks_editor_and_book_visibility_before_reading(monkeypatch):
    from flask import Flask, jsonify
    from cps.api import edit

    app = Flask(__name__)
    with app.test_request_context("/api/v1/books/42/isbn-candidates", method="POST"):
        forbidden = (jsonify({"error": {"code": "forbidden"}}), 403)
        monkeypatch.setattr(edit, "_require_edit", lambda: forbidden)
        monkeypatch.setattr(edit.isbn_extract, "extract_candidates",
                            lambda _book: pytest.fail("must not read without metadata-edit permission"))
        response, status = edit.extract_isbn_candidates.__wrapped__(42)
        assert status == 403

        monkeypatch.setattr(edit, "_require_edit", lambda: None)
        monkeypatch.setattr(edit, "_editable_book", lambda _book_id: None)
        response, status = edit.extract_isbn_candidates.__wrapped__(42)
        assert status == 404
