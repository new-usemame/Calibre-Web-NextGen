"""Regression tests for Kobo EPUB layout metadata caching."""

import os
import zipfile
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.unit


def _write_epub(path, layout=None, marker=""):
    layout_meta = "" if layout is None else (
        '<meta property="rendition:layout">{}</meta>'.format(layout)
    )
    container = b"""<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="content.opf"/></rootfiles>
</container>"""
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf">
  <metadata>{}{}</metadata>
</package>""".format(layout_meta, marker)
    with zipfile.ZipFile(path, "w") as epub:
        epub.writestr("META-INF/container.xml", container)
        epub.writestr("content.opf", opf)


def test_layout_cache_reuses_unchanged_epub_and_invalidates_replacement(tmp_path, monkeypatch):
    from cps import epub

    library = tmp_path / "library"
    book_dir = library / "Author" / "Book"
    book_dir.mkdir(parents=True)
    epub_path = book_dir / "book.epub"
    _write_epub(epub_path)

    book = SimpleNamespace(id=1, path="Author/Book")
    book_data = SimpleNamespace(name="book", format="EPUB")
    monkeypatch.setattr(epub.config, "get_book_path", lambda: str(library))
    clear_cache = getattr(epub, "_clear_epub_layout_cache", None)
    if clear_cache is not None:
        clear_cache()

    real_read = zipfile.ZipFile.read
    reads = []

    def counted_read(archive, name, *args, **kwargs):
        reads.append(name)
        return real_read(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "read", counted_read)

    assert epub.get_epub_layout(book, book_data) is None
    assert reads == ["META-INF/container.xml", "content.opf"]

    assert epub.get_epub_layout(book, book_data) is None
    assert reads == ["META-INF/container.xml", "content.opf"]

    old_stat = epub_path.stat()
    _write_epub(epub_path, layout="pre-paginated", marker="<!-- larger replacement -->")
    os.utime(epub_path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 1_000_000_000))

    assert epub.get_epub_layout(book, book_data) == "pre-paginated"
    assert reads == [
        "META-INF/container.xml", "content.opf",
        "META-INF/container.xml", "content.opf",
    ]


def test_layout_parse_failure_is_not_cached(tmp_path, monkeypatch):
    from cps import epub

    library = tmp_path / "library"
    book_dir = library / "Author" / "Broken"
    book_dir.mkdir(parents=True)
    epub_path = book_dir / "book.epub"
    _write_epub(epub_path)

    book = SimpleNamespace(id=2, path="Author/Broken")
    book_data = SimpleNamespace(name="book", format="EPUB")
    monkeypatch.setattr(epub.config, "get_book_path", lambda: str(library))
    clear_cache = getattr(epub, "_clear_epub_layout_cache", None)
    if clear_cache is not None:
        clear_cache()

    calls = 0

    def fail_to_read(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise OSError("temporary read failure")

    monkeypatch.setattr(epub, "get_content_opf", fail_to_read)

    assert epub.get_epub_layout(book, book_data) is None
    assert epub.get_epub_layout(book, book_data) is None
    assert calls == 2
