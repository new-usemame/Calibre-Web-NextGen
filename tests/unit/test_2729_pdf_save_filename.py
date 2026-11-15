# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2729: the PDF reader's Save button wrote ``document.pdf`` for every book.

pdf.js names the saved file from the response's Content-Disposition, else from
the URL. /show/<id>/pdf carries neither, so it fell back to ``document.pdf``.
The inline response now names the file after the book, the way Download does.
"""
import re
from urllib.parse import unquote

import pytest

from cps.utils.filename_sanitizer import build_content_disposition


def _parse(value):
    disposition = value.split(";", 1)[0]
    plain = re.search(r'filename="([^"]*)"', value)
    star = re.search(r"filename\*=UTF-8''(\S+)$", value)
    assert plain and star, "no filename in Content-Disposition: %r" % value
    return disposition, plain.group(1), unquote(star.group(1))


@pytest.mark.unit
def test_names_inline_pdf_after_the_book():
    disposition, plain, star = _parse(build_content_disposition("inline", "Dune - Frank Herbert.pdf"))
    assert (disposition, plain, star) == ("inline", "Dune - Frank Herbert.pdf", "Dune - Frank Herbert.pdf")


@pytest.mark.unit
def test_non_ascii_title_keeps_utf8_name_and_gets_ascii_fallback():
    _, plain, star = _parse(build_content_disposition("inline", "Les Misérables - Hugo.pdf"))
    assert star == "Les Misérables - Hugo.pdf"
    assert plain.isascii() and plain.endswith(".pdf")


@pytest.mark.unit
def test_fallback_survives_a_title_with_no_ascii_at_all(monkeypatch):
    import cps.utils.filename_sanitizer as fs
    monkeypatch.setattr(fs, "unidecode", None)
    _, plain, star = _parse(build_content_disposition("inline", "日本語.pdf"))
    assert plain == "book.pdf"
    assert star == "日本語.pdf"


@pytest.mark.unit
def test_quotes_backslashes_and_line_breaks_cannot_break_the_header():
    value = build_content_disposition("inline", 'A "quoted"\r\nX-Evil: 1 \\ title.pdf')
    assert "\r" not in value and "\n" not in value
    _, plain, _ = _parse(value)
    assert '"' not in plain and "\\" not in plain


@pytest.mark.unit
def test_inline_pdf_response_is_named_like_the_download(monkeypatch):
    from types import SimpleNamespace
    from werkzeug.datastructures import Headers
    from cps import helper

    monkeypatch.setattr(helper, "config", SimpleNamespace(config_unicode_filename=False))
    book = SimpleNamespace(id=7, title="Dune: Messiah / Part 1",
                           authors=[SimpleNamespace(name="Frank Herbert")])
    headers = Headers()
    helper.set_inline_filename(headers, book, "PDF")

    disposition, _, star = _parse(headers["Content-Disposition"])
    assert disposition == "inline"
    # Same name the Download link gives, with the characters file systems dislike swapped out.
    assert star == helper.get_download_file_name(book) + ".pdf" == "Dune_ Messiah _ Part 1 - Frank Herbert.pdf"


@pytest.mark.unit
def test_naming_failure_leaves_the_plain_inline_header(monkeypatch):
    from werkzeug.datastructures import Headers
    from cps import helper

    headers = Headers([("Content-Disposition", "inline")])
    helper.set_inline_filename(headers, object(), "pdf")  # no title/authors: must not raise
    assert headers["Content-Disposition"] == "inline"


# The real /show/ route, through serve_book and the viewer guard. The tests above
# exercise the helpers directly; this one fails if serve_book stops calling them
# (or overwrites the named header with the bare "inline" afterwards).
from tests.unit.test_shared_book_continuation import shared_books  # noqa: E402,F401  (fixture)


@pytest.mark.unit
def test_show_route_names_the_pdf_it_serves_inline(shared_books):
    from cps import db
    env = shared_books
    env.session.add(ub_exposure(env))
    book = env.session.get(db.Books, 1)
    book.authors.append(db.Authors('Ursula K. Le Guin', 'Le Guin, Ursula K.'))
    env.session.commit()
    response = env.client.get('/show/1/pdf')
    assert response.status_code == 200
    assert response.data == b'%PDF-shared-reader-bytes'
    disposition, plain, star = _parse(response.headers['Content-Disposition'])
    assert disposition == 'inline'
    # The Download link's "Title - Author" name, not pdf.js's document.pdf.
    assert star == plain == 'Book 1 - Ursula K. Le Guin.pdf'


def ub_exposure(env):
    from cps import ub
    return ub.OpdsShelfExposure(user_id=env.viewer.id, shelf_id=env.public.id)


@pytest.mark.unit
def test_percent_in_a_title_stays_out_of_the_plain_fallback():
    _, plain, star = _parse(build_content_disposition("inline", "100% Cotton - A. Author.pdf"))
    assert star == "100% Cotton - A. Author.pdf"
    assert "%" not in plain
