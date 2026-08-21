# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Focused coverage for fragment-addressed KEPUB spine splitting."""

from collections import Counter
import re
import zipfile

from lxml import etree
import pytest


CONTAINER = b"""<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _chapter(span_ids=("kobo.1.1", "kobo.1.2", "kobo.2.1", "kobo.2.2")):
    return (
        '<?xml version="1.0"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head><body>'
        '<div id="book-columns"><div id="book-inner">'
        '<p class="preface">Before chapter one</p>'
        '<section id="ch1"><h1>One</h1>'
        '<p><span class="koboSpan" id="{}">First</span>'
        '<a href="#ch2"><span class="koboSpan" id="{}">next</span></a></p></section>'
        '<section id="ch2"><h1>Two</h1>'
        '<p><span id="{}" class="other koboSpan tail">Second</span>'
        '<span class="koboSpan" id="{}">end</span></p></section>'
        '</div></div></body></html>'
    ).format(*span_ids).encode()


def _nested_anchor_chapter():
    """The measured Gutenberg shape: an outer TOC div owns nested chapters."""
    return (
        b'<?xml version="1.0"?>\n'
        b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head>'
        b'<body class="calibre"><div id="book-columns"><div id="book-inner">\n'
        b'<div id="ch1" class="outer-chapter">'
        b'<h2><span  data-kept="outer" class="lead koboSpan" '
        b'id="kobo.10.1">Outer introduction</span></h2>\n'
        b'<h3 id="ch2"><span class="koboSpan" id="kobo.11.1">Nested one</span></h3>'
        b'<p><a href="#ch1"><span class="koboSpan" id="kobo.11.2">outer</span></a></p>\n'
        b'<h3 id="ch3"><span id="kobo.12.1" class="tail koboSpan">Nested two</span>'
        b'</h3><p>Last nested chapter.</p>'
        b'</div>\n'
        b'</div></div></body></html>'
    )


def _ncx(targets):
    points = "".join(
        '<navPoint id="n{}"><navLabel><text>{}</text></navLabel>'
        '<content src="{}"/></navPoint>'.format(index, index, target)
        for index, target in enumerate(targets)
    )
    return (
        '<?xml version="1.0"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
        + points + '</navMap></ncx>'
    ).encode()


def _opf(properties=' properties="scripted"', guide=True):
    guide_xml = (
        '<guide><reference type="text" title="Second" '
        'href="chapter.xhtml#ch2"/></guide>' if guide else ""
    )
    return (
        '<?xml version="1.0"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<manifest>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        '<item id="chapter" href="chapter.xhtml" '
        'media-type="application/xhtml+xml"{}/>'
        '</manifest><spine toc="ncx"><itemref idref="chapter"/></spine>{}'
        '</package>'
    ).format(properties, guide_xml).encode()


def _book(tmp_path, *, targets=("chapter.xhtml#ch1", "chapter.xhtml#ch2"),
          chapter=None, ncx=None, extra_members=(), properties=' properties="scripted"'):
    path = tmp_path / "book.kepub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OPS/book.opf", _opf(properties=properties))
        archive.writestr("OPS/toc.ncx", _ncx(targets) if ncx is None else ncx)
        archive.writestr("OPS/chapter.xhtml", _chapter() if chapter is None else chapter)
        for name, content in extra_members:
            archive.writestr(name, content)
    return path


def _add_spine_document(book, member, item_id, document_content):
    with zipfile.ZipFile(book) as archive:
        members = [(info, archive.read(info)) for info in archive.infolist()]
    package_index = next(
        index for index, (info, _content) in enumerate(members)
        if info.filename == "OPS/book.opf")
    package_info, package = members[package_index]
    package = package.replace(
        b"</manifest>",
        ('<item id="{}" href="{}" media-type="application/xhtml+xml"/>'
         '</manifest>').format(item_id, member).encode(),
    ).replace(
        b"</spine>", '<itemref idref="{}"/></spine>'.format(item_id).encode())
    members[package_index] = package_info, package
    with zipfile.ZipFile(book, "w") as archive:
        for info, member_content in members:
            archive.writestr(info, member_content)
        archive.writestr("OPS/" + member, document_content)


def _add_following_document(book, following):
    _add_spine_document(book, "following.xhtml", "following", following)


def _split(path):
    from cps.services.kepub_spine_splitter import split_multichapter_documents

    return split_multichapter_documents(path)


def _span_ids(contents):
    return Counter(re.findall(
        rb'<span\b(?=[^>]*\bclass=["\'][^"\']*\bkoboSpan\b)[^>]*'
        rb'\bid=["\']([^"\']+)["\']',
        b"".join(contents),
    ))


def _kobo_span_lexemes(contents):
    return Counter(re.findall(
        rb'<span\b(?=[^>]*\bclass=["\'][^"\']*\bkoboSpan\b)[^>]*>'
        rb'[^<]*</span\s*>',
        b"".join(contents),
    ))


def _package_state(path):
    with zipfile.ZipFile(path) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    package = etree.fromstring(contents["OPS/book.opf"])
    manifest = {
        item.get("id"): (item.get("href"), item.get("properties"))
        for item in package.xpath("//*[local-name()='manifest']/*[local-name()='item']")
    }
    spine = package.xpath("//*[local-name()='spine']/*[local-name()='itemref']/@idref")
    toc = etree.fromstring(contents["OPS/toc.ncx"])
    targets = toc.xpath("//*[local-name()='navMap']//*[local-name()='content']/@src")
    return contents, manifest, spine, targets


@pytest.mark.unit
def test_two_chapters_split_in_spine_order_and_toc_fragments_are_removed(tmp_path):
    book = _book(tmp_path)
    untouched_before = b"unchanged auxiliary bytes\x00\xff"
    with zipfile.ZipFile(book, "a") as archive:
        archive.writestr("OPS/untouched.bin", untouched_before)

    assert _split(book) is True

    contents, manifest, spine, targets = _package_state(book)
    assert "OPS/chapter.xhtml" not in contents
    assert spine == ["chapter", "chapter-split"]
    assert manifest["chapter"] == ("chapter-split-1.xhtml", "scripted")
    assert manifest["chapter-split"] == ("chapter-split-2.xhtml", "scripted")
    assert targets == ["chapter-split-1.xhtml", "chapter-split-2.xhtml"]
    assert contents["OPS/untouched.bin"] == untouched_before
    assert b"Before chapter one" in contents["OPS/chapter-split-1.xhtml"]
    assert b'id="ch2"' not in contents["OPS/chapter-split-1.xhtml"]
    assert contents["OPS/chapter-split-2.xhtml"].count(b'id="ch2"') == 1


@pytest.mark.unit
def test_kobo_span_id_multiset_is_preserved_exactly(tmp_path):
    book = _book(tmp_path)
    with zipfile.ZipFile(book) as archive:
        before = _span_ids([archive.read(name) for name in archive.namelist()])

    assert _split(book) is True

    with zipfile.ZipFile(book) as archive:
        after = _span_ids([archive.read(name) for name in archive.namelist()])
    assert sum(before.values()) == 4
    assert sum(after.values()) == 4
    assert after == before


@pytest.mark.unit
def test_real_kepub_shape_preserves_every_kobo_span_lexeme_byte_exact(tmp_path):
    chapter = _chapter().replace(
        b'<span class="koboSpan" id="kobo.1.1">First</span>',
        b"<span  data-extra='kept' class='lead koboSpan' id='kobo.1.1'>First</span>",
    )
    book = _book(tmp_path, chapter=chapter)
    with zipfile.ZipFile(book) as archive:
        before = _kobo_span_lexemes([archive.read(name) for name in archive.namelist()])

    assert _split(book) is True

    with zipfile.ZipFile(book) as archive:
        after = _kobo_span_lexemes([archive.read(name) for name in archive.namelist()])
    assert len(before) == 4
    assert after == before


@pytest.mark.unit
def test_shared_cut_element_keeps_anchors_together_without_aborting_other_splits(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body class="calibre">'
        b'<div id="book-columns"><div id="book-inner">'
        b'<div class="chapter-shell"><h1 id="ch1">One</h1>'
        b'<p><span class="koboSpan" id="kobo.1.1">one</span></p>'
        b'<h2 id="ch1b">One, continued</h2>'
        b'<p><span class="koboSpan" id="kobo.1.2">continued</span></p></div>'
        b'<div class="chapter-shell"><h1 id="ch2">Two</h1>'
        b'<p><span class="koboSpan" id="kobo.2.1">two</span></p></div>'
        b'</div></div></body></html>'
    )
    book = _book(
        tmp_path,
        targets=(
            "chapter.xhtml#ch1",
            "chapter.xhtml#ch1b",
            "chapter.xhtml#ch2",
        ),
        chapter=chapter,
    )

    assert _split(book) is True

    contents, _manifest, spine, targets = _package_state(book)
    assert spine == ["chapter", "chapter-split"]
    assert targets == [
        "chapter-split-1.xhtml",
        "chapter-split-1.xhtml",
        "chapter-split-2.xhtml",
    ]
    first = contents["OPS/chapter-split-1.xhtml"]
    second = contents["OPS/chapter-split-2.xhtml"]
    assert b'id="ch1"' in first and b'id="ch1b"' in first
    assert b'id="ch2"' not in first
    assert b'id="ch2"' in second


@pytest.mark.unit
def test_descends_once_when_an_outer_toc_anchor_owns_nested_chapters(tmp_path):
    book = _book(
        tmp_path,
        targets=("chapter.xhtml#ch1", "chapter.xhtml#ch2", "chapter.xhtml#ch3"),
        chapter=_nested_anchor_chapter(),
    )

    assert _split(book) is True

    contents, _manifest, spine, targets = _package_state(book)
    assert spine == ["chapter", "chapter-split", "chapter-split-1"]
    assert targets == [
        "chapter-split-1.xhtml",
        "chapter-split-2.xhtml",
        "chapter-split-3.xhtml",
    ]
    pieces = [contents["OPS/chapter-split-{}.xhtml".format(index)] for index in range(1, 4)]
    assert all(piece.count(b'id="ch1"') == 1 for piece in pieces)
    assert b"Outer introduction" in pieces[0]
    assert b'id="ch2"' not in pieces[0]
    assert b'id="ch2"' in pieces[1] and b'id="ch3"' not in pieces[1]
    assert b'id="ch3"' in pieces[2]


@pytest.mark.unit
def test_descended_outer_anchor_explicitly_keeps_piece_one(tmp_path):
    book = _book(
        tmp_path,
        targets=("chapter.xhtml#ch1", "chapter.xhtml#ch2", "chapter.xhtml#ch3"),
        chapter=_nested_anchor_chapter(),
    )
    following = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<a href="chapter.xhtml#ch1">Outer chapter</a></body></html>')
    _add_following_document(book, following)

    assert _split(book) is True

    contents, _manifest, _spine, targets = _package_state(book)
    assert targets[0] == "chapter-split-1.xhtml"
    assert b'href="chapter-split-1.xhtml#ch1"' in contents["OPS/following.xhtml"]
    assert b'href="chapter-split-1.xhtml#ch1"' in contents["OPS/chapter-split-2.xhtml"]


@pytest.mark.unit
def test_explicit_anchor_mapping_overrides_a_repeated_shell_identity():
    from cps.services.kepub_spine_splitter import _prefer_explicit_anchor_pieces

    # Simulate generic discovery seeing the copied outer shell in the last
    # piece.  The explicit TOC target's original position must win afterward.
    mapping = {"ch1": "chapter-split-3.xhtml", "ch3": "chapter-split-3.xhtml"}
    result = _prefer_explicit_anchor_pieces(
        mapping,
        {"ch1": 10, "ch2": 110, "ch3": 210},
        [50, 100, 200],
        [
            "chapter-split-1.xhtml",
            "chapter-split-2.xhtml",
            "chapter-split-3.xhtml",
        ],
    )

    assert result == {
        "ch1": "chapter-split-1.xhtml",
        "ch2": "chapter-split-2.xhtml",
        "ch3": "chapter-split-3.xhtml",
    }


@pytest.mark.unit
def test_multiple_outer_targets_do_not_descend_but_other_boundary_still_splits(tmp_path):
    outer = (
        b'<div id="ch1" name="ch1-alias"><h2>Outer</h2>'
        b'<h3 id="ch2"><span class="koboSpan" id="kobo.2.1">nested</span></h3>'
        b'</div>')
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><div id="book-columns">'
        b'<div id="book-inner">' + outer +
        b'<section id="ch4"><span class="koboSpan" id="kobo.4.1">real split</span>'
        b'</section></div></div></body></html>')
    book = _book(
        tmp_path,
        targets=(
            "chapter.xhtml#ch1",
            "chapter.xhtml#ch1-alias",
            "chapter.xhtml#ch2",
            "chapter.xhtml#ch4",
        ),
        chapter=chapter,
    )

    assert _split(book) is True

    contents, _manifest, spine, targets = _package_state(book)
    assert spine == ["chapter", "chapter-split"]
    assert targets == [
        "chapter-split-1.xhtml",
        "chapter-split-1.xhtml",
        "chapter-split-1.xhtml",
        "chapter-split-2.xhtml",
    ]
    assert outer in contents["OPS/chapter-split-1.xhtml"]
    assert b'id="ch4"' in contents["OPS/chapter-split-2.xhtml"]


@pytest.mark.unit
def test_meaningful_content_outside_descent_container_is_not_duplicated(tmp_path):
    nested = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><div id="book-columns">'
        b'<div id="book-inner"><p>Meaningful sibling content</p>'
        b'<div id="outer"><h2>Outer</h2>'
        b'<h3 id="nested-1"><span class="koboSpan" id="kobo.8.1">one</span></h3>'
        b'<h3 id="nested-2"><span class="koboSpan" id="kobo.8.2">two</span></h3>'
        b'</div></div></div></body></html>')
    book = _book(
        tmp_path,
        targets=(
            "chapter.xhtml#ch1",
            "chapter.xhtml#ch2",
            "nested.xhtml#outer",
            "nested.xhtml#nested-1",
            "nested.xhtml#nested-2",
        ),
    )
    _add_spine_document(book, "nested.xhtml", "nested", nested)

    # The ordinary chapter proves planning and writing actually proceed; the
    # unsafe nested candidate must remain byte-exact instead of being copied
    # around each descended piece.
    assert _split(book) is True

    contents, _manifest, spine, targets = _package_state(book)
    assert spine == ["chapter", "chapter-split", "nested"]
    assert contents["OPS/nested.xhtml"] == nested
    assert contents["OPS/nested.xhtml"].count(b"Meaningful sibling content") == 1
    assert targets == [
        "chapter-split-1.xhtml",
        "chapter-split-2.xhtml",
        "nested.xhtml#outer",
        "nested.xhtml#nested-1",
        "nested.xhtml#nested-2",
    ]


@pytest.mark.unit
def test_descent_preserves_every_kobo_span_lexeme_byte_exact(tmp_path):
    book = _book(
        tmp_path,
        targets=("chapter.xhtml#ch1", "chapter.xhtml#ch2", "chapter.xhtml#ch3"),
        chapter=_nested_anchor_chapter(),
    )
    with zipfile.ZipFile(book) as archive:
        before = _kobo_span_lexemes([archive.read(name) for name in archive.namelist()])

    assert _split(book) is True

    with zipfile.ZipFile(book) as archive:
        after = _kobo_span_lexemes([archive.read(name) for name in archive.namelist()])
    assert len(before) == 4
    assert after == before


@pytest.mark.unit
def test_second_call_after_descent_is_a_byte_identical_noop(tmp_path):
    book = _book(
        tmp_path,
        targets=("chapter.xhtml#ch1", "chapter.xhtml#ch2", "chapter.xhtml#ch3"),
        chapter=_nested_anchor_chapter(),
    )
    assert _split(book) is True
    after_first = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == after_first


@pytest.mark.unit
def test_non_one_based_kobo_span_ids_remain_byte_exact(tmp_path):
    ids = ("kobo.0.0", "kobo.0.7", "kobo.8.0", "kobo.8.19")
    book = _book(tmp_path, chapter=_chapter(ids))

    assert _split(book) is True

    with zipfile.ZipFile(book) as archive:
        after = _span_ids([archive.read(name) for name in archive.namelist()])
    assert after == Counter(span_id.encode() for span_id in ids)


@pytest.mark.unit
def test_single_fragment_document_is_a_byte_identical_noop(tmp_path):
    book = _book(tmp_path, targets=("chapter.xhtml#ch1",))
    before = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == before


@pytest.mark.unit
def test_missing_target_anchor_is_a_byte_identical_noop(tmp_path):
    book = _book(
        tmp_path, targets=("chapter.xhtml#ch1", "chapter.xhtml#missing"))
    before = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == before


@pytest.mark.unit
def test_unparseable_toc_is_left_untouched(tmp_path):
    book = _book(tmp_path, ncx=b"<ncx><navMap><broken></ncx>")
    before = book.read_bytes()

    assert _split(book) in (False, None)
    assert book.read_bytes() == before


@pytest.mark.unit
def test_second_call_is_a_byte_identical_noop(tmp_path):
    book = _book(tmp_path)
    assert _split(book) is True
    after_first = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == after_first


@pytest.mark.unit
def test_normalizer_default_does_not_split_multichapter_documents(tmp_path):
    import inspect

    from cps.services.kepub_package_normalizer import normalize_kepub_package

    book = _book(tmp_path)
    before = book.read_bytes()

    split_parameter = inspect.signature(normalize_kepub_package).parameters["split_chapters"]
    assert split_parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert split_parameter.default is False
    assert normalize_kepub_package(book) is False
    assert book.read_bytes() == before


@pytest.mark.unit
def test_normalizer_explicit_split_opt_in_splits_multichapter_documents(tmp_path):
    from cps.services.kepub_package_normalizer import normalize_kepub_package

    book = _book(tmp_path)

    assert normalize_kepub_package(book, split_chapters=True) is True

    _contents, _manifest, spine, targets = _package_state(book)
    assert spine == ["chapter", "chapter-split"]
    assert targets == ["chapter-split-1.xhtml", "chapter-split-2.xhtml"]


@pytest.mark.unit
def test_opted_in_split_failure_is_a_byte_identical_nonfatal_result(
        tmp_path, monkeypatch):
    from cps.services import kepub_spine_splitter
    from cps.services.kepub_package_normalizer import normalize_kepub_package

    book = _book(tmp_path)
    before = book.read_bytes()
    monkeypatch.setattr(
        kepub_spine_splitter,
        "split_multichapter_documents",
        lambda _path: None,
    )

    assert normalize_kepub_package(book, split_chapters=True) is None
    assert book.read_bytes() == before


@pytest.mark.unit
def test_guide_and_cross_piece_internal_links_continue_to_resolve(tmp_path):
    book = _book(tmp_path)
    following = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<a href="chapter.xhtml#ch2">Back to two</a></body></html>')
    _add_following_document(book, following)

    assert _split(book) is True

    contents, _manifest, _spine, _targets = _package_state(book)
    assert b'href="chapter-split-2.xhtml#ch2"' in contents["OPS/chapter-split-1.xhtml"]
    assert b'href="chapter-split-2.xhtml#ch2"' in contents["OPS/following.xhtml"]
    package = etree.fromstring(contents["OPS/book.opf"])
    assert package.xpath("string(//*[local-name()='guide']/*/@href)") == (
        "chapter-split-2.xhtml#ch2")


@pytest.mark.unit
def test_css_url_reference_to_split_fragment_continues_to_resolve(tmp_path):
    stylesheet = b".chapter-link{background:url('chapter.xhtml#ch2')}"
    book = _book(
        tmp_path,
        extra_members=(("OPS/book.css", stylesheet),),
    )

    assert _split(book) is True

    contents, _manifest, _spine, _targets = _package_state(book)
    assert contents["OPS/book.css"] == (
        b".chapter-link{background:url('chapter-split-2.xhtml#ch2')}")


@pytest.mark.unit
def test_unsupported_reference_attribute_causes_a_byte_identical_noop(tmp_path):
    book = _book(tmp_path)
    following = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<video poster="chapter.xhtml#ch2"/></body></html>')
    _add_following_document(book, following)
    before = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == before


@pytest.mark.unit
def test_nested_boundary_that_cannot_form_valid_documents_is_left_untouched(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><div id="ch1">'
        b'<span class="koboSpan" id="kobo.1.1">one</span>'
        b'<section id="ch2"><span class="koboSpan" id="kobo.2.1">two</span>'
        b'</section></div></body></html>'
    )
    book = _book(tmp_path, chapter=chapter)
    before = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == before


@pytest.mark.unit
def test_existing_piece_name_collision_is_avoided(tmp_path):
    book = _book(
        tmp_path, extra_members=(("OPS/chapter-split-1.xhtml", b"occupied"),))

    assert _split(book) is True

    contents, _manifest, _spine, targets = _package_state(book)
    assert contents["OPS/chapter-split-1.xhtml"] == b"occupied"
    assert targets == ["chapter-split-2.xhtml", "chapter-split-3.xhtml"]


# ---------------------------------------------------------------------------
# The post-write validator's own guards.
#
# WHY THESE EXIST. A mutation campaign over this suite found that the splitter's
# BEHAVIOUR was well covered -- reverting the cut container to <body> failed 16
# of 17 tests, mapping every fragment to the first piece failed 6, reversing
# piece order failed 3 -- while EVERY guard inside `_validate_split_archive`
# could be deleted with the whole suite still green:
#
#   deleted guard                              suite result
#   KoboSpan id multiset changed                17 passed
#   spine reading order changed                 17 passed
#   TOC target retains a fragment               17 passed
#   split archive differs from the rewrite plan 17 passed
#   fragment identity occurs in multiple pieces 17 passed
#
# Those guards are the last thing standing between a planning or writing bug and
# a corrupted book on someone's device, and none of them was exercised. Tests
# that only drive the correct path cannot see them, because on the correct path
# they never fire.
#
# So each test below INJECTS the fault the guard exists to catch, and asserts the
# whole rewrite is abandoned: `split_multichapter_documents` returns None, the
# source archive is byte-identical, and no temporary file is left beside it.
# `os.replace` runs only after validation, so "byte-identical" is the real
# user-visible contract, not an implementation detail.
#
# Faults are injected at the two points where they can actually originate:
# `_build_entries` (a bad PLAN) and `_write_archive` (a bad WRITE). Nothing in
# the production module is modified.
#
# After these tests, deleting any one of those four guards fails exactly one test
# each. A FIFTH guard is deliberately not covered here:
#
#     raise ValueError("non-touched ZIP member changed: " + name)
#
# It cannot fire, and no test can make it. `touched` is defined as every name
# whose planned content differs from its source content, so for any name outside
# `touched`, expected == source by construction; the earlier
# `actual != expected_contents` guard has already proven actual == expected, and
# therefore actual == source. It is subsumed, not independent. Left in place
# because it states the intent cheaply, recorded here so nobody spends an
# afternoon trying to write the test that would cover it.
# ---------------------------------------------------------------------------


def _splitter_module():
    from cps.services import kepub_spine_splitter

    return kepub_spine_splitter


def _leftover_temporaries(book):
    return sorted(
        child.name for child in book.parent.iterdir()
        if child.name != book.name and ".spine-split.tmp" in child.name
    )


def _assert_refused(book, before, result):
    """The rewrite was abandoned and the user's file is exactly as it was."""
    assert result is None
    assert book.read_bytes() == before
    assert _leftover_temporaries(book) == []


def _corrupt_entries(monkeypatch, corrupt):
    """Let a test damage the rewrite plan just before it is written."""
    module = _splitter_module()
    original = module._build_entries

    def patched(*args, **kwargs):
        return corrupt(list(original(*args, **kwargs)))

    monkeypatch.setattr(module, "_build_entries", patched)


def _corrupt_written_archive(monkeypatch, corrupt):
    """Let a test damage the bytes actually written, leaving the plan intact."""
    module = _splitter_module()
    original = module._write_archive

    def patched(path, entries, comment):
        return original(path, corrupt(list(entries)), comment)

    monkeypatch.setattr(module, "_write_archive", patched)


def _edit(entries, name, replace):
    found = False
    edited = []
    for info, content in entries:
        if info.filename == name:
            content = replace(content)
            found = True
        edited.append((info, content))
    assert found, "fixture no longer contains {!r}".format(name)
    return edited


@pytest.mark.unit
def test_validator_refuses_a_plan_that_loses_a_kobo_span(tmp_path, monkeypatch):
    """The single most important invariant: a highlight anchors to a KoboSpan id.

    A plan that drops one is self-consistent -- the archive matches the plan
    exactly -- so only the multiset check can catch it.
    """
    book = _book(tmp_path)
    before = book.read_bytes()

    def drop_a_span(entries):
        return _edit(
            entries, "OPS/chapter-split-1.xhtml",
            lambda content: re.sub(
                rb'<span class="koboSpan" id="kobo\.1\.1">First</span>', b"First", content,
                count=1))

    _corrupt_entries(monkeypatch, drop_a_span)
    _assert_refused(book, before, _split(book))


@pytest.mark.unit
def test_validator_refuses_a_plan_that_reorders_the_spine(tmp_path, monkeypatch):
    """Chapters delivered out of order is a silently wrong book, not a crash."""
    book = _book(tmp_path)
    before = book.read_bytes()

    def reverse_spine(entries):
        return _edit(
            entries, "OPS/book.opf",
            lambda content: content.replace(
                b'<itemref idref="chapter"/><itemref idref="chapter-split"/>',
                b'<itemref idref="chapter-split"/><itemref idref="chapter"/>'))

    _corrupt_entries(monkeypatch, reverse_spine)
    _assert_refused(book, before, _split(book))


@pytest.mark.unit
def test_validator_refuses_a_plan_that_leaves_a_fragment_on_a_split_target(
        tmp_path, monkeypatch):
    """A retained #fragment is the whole defect #1657 exists to remove.

    Shipping a split whose TOC still points at an anchor would produce exactly
    the unreachable-chapter identity the split was performed to fix.
    """
    book = _book(tmp_path)
    before = book.read_bytes()

    def restore_a_fragment(entries):
        return _edit(
            entries, "OPS/toc.ncx",
            lambda content: content.replace(
                b'<content src="chapter-split-2.xhtml"/>',
                b'<content src="chapter-split-2.xhtml#ch2"/>'))

    _corrupt_entries(monkeypatch, restore_a_fragment)
    _assert_refused(book, before, _split(book))


@pytest.mark.unit
def test_validator_refuses_bytes_that_do_not_match_the_rewrite_plan(
        tmp_path, monkeypatch):
    """Guards the WRITE rather than the plan: what landed must be what was planned."""
    book = _book(tmp_path)
    before = book.read_bytes()

    def smuggle_bytes_past_the_plan(entries):
        return _edit(
            entries, "OPS/chapter-split-2.xhtml",
            lambda content: content.replace(b"Second", b"Smuggled"))

    _corrupt_written_archive(monkeypatch, smuggle_bytes_past_the_plan)
    _assert_refused(book, before, _split(book))


@pytest.mark.unit
def test_validator_guards_run_on_the_temporary_file_before_the_original_moves(
        tmp_path, monkeypatch):
    """Vacuity guard for the four tests above.

    Each asserts the source is unchanged after a refusal. That assertion would
    also hold if the splitter had simply declined to split this fixture for an
    unrelated reason, which would make all four pass while testing nothing. Pin
    that the same fixture DOES split when no fault is injected, so the byte
    identity above is caused by the refusal and by nothing else.
    """
    book = _book(tmp_path)
    before = book.read_bytes()

    assert _split(book) is True
    assert book.read_bytes() != before


# ---------------------------------------------------------------------------
# Hostile packages.
#
# A KEPUB reaching the splitter can be one a user uploaded, so the archive's own
# paths are untrusted input. The splitter never extracts to disk -- it reads
# members in memory, writes a temporary archive beside the target and os.replace()s
# it -- so classic zip-slip does not apply. What remains is whether a crafted OPF
# or TOC can make it address something outside the archive, or overwrite a member
# it was not asked to touch.
#
# These were run as one-off probes first and are kept because the answers are the
# security posture, not a detail of today's implementation.
# ---------------------------------------------------------------------------


def _book_with_toc_targets(tmp_path, targets, extra=None, name="hostile.kepub"):
    """A minimal splittable package whose TOC points wherever the caller says."""
    book = _book(tmp_path)
    replacement = _ncx(targets)
    with zipfile.ZipFile(book) as archive:
        contents = {info.filename: archive.read(info) for info in archive.infolist()}
        infos = archive.infolist()
    hostile = tmp_path / name
    with zipfile.ZipFile(hostile, "w") as archive:
        for info in infos:
            data = contents[info.filename]
            if info.filename.endswith("toc.ncx"):
                data = replacement
            archive.writestr(info, data)
        for member, data in (extra or {}).items():
            archive.writestr(member, data)
    return hostile


#: A hostile target paired with two REAL fragments on a real member. Without the
#: pairing these cases do not discriminate: if the containment check is removed
#: the hostile target is simply skipped, the document stops being a split
#: candidate, and `_split` returns False for a completely different reason —
#: which is what the first version of these tests actually measured. With a
#: genuine candidate present the split can proceed, so the assertions below see
#: what containment is really preventing.
_REAL_FRAGMENTS = ["chapter.xhtml#ch1", "chapter.xhtml#ch2"]


@pytest.mark.unit
@pytest.mark.parametrize("hostile, why", [
    ("../../../../etc/passwd#ch1", "relative traversal out of the archive"),
    ("/etc/passwd#ch1", "absolute path"),
    ("chapter.xhtml/../../../../etc/passwd#ch1", "traversal behind a real member name"),
    ("file:///etc/passwd#ch1", "absolute file: URL"),
    ("//evil.example/passwd#ch1", "protocol-relative URL"),
])
def test_a_hostile_toc_target_never_escapes_the_archive(tmp_path, hostile, why):
    """The security property, asserted directly rather than via the mechanism.

    Whether the splitter REFUSES the package or ignores the hostile entry, the
    invariant that matters is the same: nothing is addressed or written outside
    the archive, and a refusal leaves the user's file exactly as it was.
    """
    book = _book_with_toc_targets(tmp_path, _REAL_FRAGMENTS + [hostile])
    before = book.read_bytes()
    siblings_before = sorted(child.name for child in tmp_path.iterdir())

    result = _split(book)
    assert result in (True, False, None), why

    with zipfile.ZipFile(book) as archive:
        names = archive.namelist()
    escaping = [
        name for name in names
        if name.startswith("/") or ".." in name.split("/") or ":" in name.split("/")[0]
    ]
    assert escaping == [], (why, escaping)

    if result is not True:
        assert book.read_bytes() == before, why
    assert _leftover_temporaries(book) == []
    assert sorted(child.name for child in tmp_path.iterdir()) == siblings_before, (
        "the splitter created something beside the book: %s" % why)


@pytest.mark.unit
@pytest.mark.parametrize("hostile", [
    "../../../../etc/passwd#ch1",
    "/etc/passwd#ch1",
])
def test_a_hostile_target_beside_real_ones_refuses_the_whole_package(tmp_path, hostile):
    """And it refuses rather than splitting around the hostile entry.

    This is the assertion that dies if the containment check is swallowed: with
    two real fragments present the package IS a split candidate, so dropping the
    hostile target silently would produce a rewritten archive. Refusing the
    whole package is the documented contract -- anything not provably
    reference-safe leaves the file untouched.
    """
    book = _book_with_toc_targets(tmp_path, _REAL_FRAGMENTS + [hostile])
    before = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == before


@pytest.mark.unit
def test_no_member_can_be_written_outside_the_archive(tmp_path):
    """Whatever the TOC claims, every member name stays inside the package."""
    book = _book_with_toc_targets(
        tmp_path, ["chapter.xhtml#ch1", "chapter.xhtml#ch2"])
    assert _split(book) is True

    with zipfile.ZipFile(book) as archive:
        names = archive.namelist()
    assert names, "the archive lost every member"
    escaping = [
        name for name in names
        if name.startswith("/") or ".." in name.split("/") or ":" in name.split("/")[0]
    ]
    assert escaping == [], escaping


@pytest.mark.unit
def test_a_piece_name_collision_never_overwrites_the_existing_member(tmp_path):
    """The obvious way to lose data here is to name a piece over something real.

    A package can already contain `chapter-split-1.xhtml` -- most easily because
    it was split once before by an older build. Its bytes must survive, and the
    new pieces must take the next free names.
    """
    book = _book_with_toc_targets(
        tmp_path, ["chapter.xhtml#ch1", "chapter.xhtml#ch2"],
        extra={"OPS/chapter-split-1.xhtml": b"PRE-EXISTING"})

    assert _split(book) is True

    with zipfile.ZipFile(book) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    assert contents["OPS/chapter-split-1.xhtml"] == b"PRE-EXISTING", (
        "the splitter overwrote a member it was not asked to touch")
    assert "OPS/chapter-split-2.xhtml" in contents
    assert "OPS/chapter-split-3.xhtml" in contents


@pytest.mark.unit
def test_a_hostile_package_leaves_no_temporary_file_behind(tmp_path):
    """A refusal must not litter the library directory with .spine-split.tmp files."""
    book = _book_with_toc_targets(
        tmp_path, ["/etc/passwd#ch1", "/etc/passwd#ch2"])
    assert _split(book) is False
    assert _leftover_temporaries(book) == []
    assert sorted(child.name for child in tmp_path.iterdir()) == sorted(
        {book.name, "book.kepub"}), sorted(child.name for child in tmp_path.iterdir())


@pytest.mark.unit
def test_package_was_split_by_us_recognises_our_own_output(tmp_path):
    """The signal the replacement rule (F-bbd10e) turns on.

    False before, True after — the detector has to change its answer for the
    rule to mean anything.
    """
    from cps.services.kepub_spine_splitter import package_was_split_by_us

    book = _book(tmp_path)
    assert package_was_split_by_us(book) is False
    assert _split(book) is True
    assert package_was_split_by_us(book) is True


@pytest.mark.unit
def test_a_split_looking_member_outside_the_spine_does_not_count(tmp_path):
    """Name-sniffing alone would be a false positive, and a false positive here
    re-introduces exactly the harm the caller's guard prevents: it would split an
    annotated book that was never split. Only the SPINE counts."""
    from cps.services.kepub_spine_splitter import package_was_split_by_us

    book = _book(tmp_path)
    with zipfile.ZipFile(book, "a") as archive:
        archive.writestr("OPS/decoy-split-1.xhtml", b"<html/>")

    assert package_was_split_by_us(book) is False


@pytest.mark.unit
def test_an_unreadable_package_answers_the_conservative_way(tmp_path):
    """False means "do not split an annotated book", which is the safe side."""
    from cps.services.kepub_spine_splitter import package_was_split_by_us

    broken = tmp_path / "broken.kepub"
    broken.write_bytes(b"not a zip at all")
    assert package_was_split_by_us(broken) is False
    assert package_was_split_by_us(tmp_path / "does-not-exist.kepub") is False


# ---------------------------------------------------------------------------
# Split fan-out bounds.
#
# The byte bounds this module inherits from the normalizer cap the INPUT, and
# they were written for a REWRITE — their own docstring says they exist because
# "this implementation temporarily holds the source members, rewritten members,
# output ZIP and validation members at the same time". A SPLIT is a FAN-OUT, so
# an input-side bound stops bounding the peak: every boundary carries its own
# copy of the document shell.
#
# MEASURED before the cap existed: a 14.9 KB upload with 1000 anchors and a
# 180 KB shell reached 348 MiB of allocation — past the 256 MiB the module
# declares — and 8000 anchors burned 185 seconds of CPU. Both run inline in the
# Flask request handler, on a gevent server where one busy greenlet blocks every
# other request, and the auto-ingest path opts in unconditionally for anything
# dropped in a watched folder.
#
# After the cap, the same inputs are refused in well under a second and a few
# MiB. The largest fan-out in the 41-book reference library is ELEVEN fragments
# in one document, so a 512-piece cap has ~46x headroom over the worst real book
# while stopping the attack.
# ---------------------------------------------------------------------------


def _fanout_book(tmp_path, anchors, head_bytes=100):
    """A package whose TOC points `anchors` times into one small document."""
    filler = "<!--" + ("x" * max(0, head_bytes)) + "-->"
    paragraphs = "".join(
        '<p id="p{i}"><span class="koboSpan" id="kobo.{i}.1">t</span></p>'.format(i=i)
        for i in range(anchors))
    chapter = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title>t</title>' + filler + '</head><body>'
        '<div id="book-columns"><div id="book-inner">' + paragraphs +
        '</div></div></body></html>').encode()
    points = "".join(
        '<navPoint id="n{i}"><navLabel><text>{i}</text></navLabel>'
        '<content src="chapter.xhtml#p{i}"/></navPoint>'.format(i=i)
        for i in range(anchors))
    # `co_varnames` includes LOCALS, not just parameters, so a cleverer probe for
    # an optional `name=` argument silently passed a kwarg _book does not take.
    # Each caller gets its own tmp_path, so one fixed name is enough.
    book = _book(tmp_path)
    with zipfile.ZipFile(book) as archive:
        infos = archive.infolist()
        contents = {info.filename: archive.read(info) for info in infos}
    with zipfile.ZipFile(book, "w") as archive:
        for info in infos:
            data = contents[info.filename]
            if info.filename.endswith("chapter.xhtml"):
                data = chapter
            elif info.filename.endswith("toc.ncx"):
                data = ('<?xml version="1.0"?>'
                        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
                        + points + '</navMap></ncx>').encode()
            archive.writestr(info, data)
    return book


@pytest.mark.unit
def test_an_absurd_fragment_count_is_refused_not_split(tmp_path):
    from cps.services.kepub_spine_splitter import MAX_SPLIT_PIECES

    book = _fanout_book(tmp_path, anchors=MAX_SPLIT_PIECES + 50)
    before = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == before
    assert _leftover_temporaries(book) == []


@pytest.mark.unit
def test_a_large_shell_times_many_pieces_is_refused_before_allocating(tmp_path):
    """The piece COUNT alone is not the danger — it is count x shell.

    This case is comfortably under the piece cap and still over the byte budget,
    so it is the test that fails if only the count check exists.
    """
    from cps.services.kepub_spine_splitter import (
        MAX_SPLIT_PEAK_BYTES, MAX_SPLIT_PIECES,
    )

    anchors = 400
    assert anchors < MAX_SPLIT_PIECES, "this case must not be caught by the count cap"
    head = (MAX_SPLIT_PEAK_BYTES // anchors) + 4096

    book = _fanout_book(tmp_path, anchors=anchors, head_bytes=head)
    before = book.read_bytes()

    assert _split(book) is False
    assert book.read_bytes() == before


@pytest.mark.unit
def test_the_refusal_is_cheap(tmp_path):
    """Refusing must not cost what doing it would have.

    A guard that still allocates the pieces before rejecting them fixes nothing;
    the whole point is that the budget is checked BEFORE the first copy.
    """
    import tracemalloc

    from cps.services.kepub_spine_splitter import MAX_SPLIT_PEAK_BYTES

    book = _fanout_book(tmp_path, anchors=400, head_bytes=(MAX_SPLIT_PEAK_BYTES // 400) + 4096)

    tracemalloc.start()
    try:
        assert _split(book) is False
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert peak < MAX_SPLIT_PEAK_BYTES // 2, (
        "refusing allocated {:.1f} MiB — the budget is being checked after the "
        "pieces are built, not before".format(peak / 1048576))


@pytest.mark.unit
def test_a_normal_book_is_nowhere_near_the_cap(tmp_path):
    """Vacuity guard, and the regression guard for real books.

    The three tests above would all pass if the cap were set to 1 and nothing
    ever split. The largest fan-out in the reference library is 11 fragments in
    one document; this pins that an ordinary book still splits.
    """
    from cps.services.kepub_spine_splitter import MAX_SPLIT_PIECES

    assert MAX_SPLIT_PIECES >= 128, "the cap has been set below any plausible book"
    book = _book(tmp_path)
    assert _split(book) is True


@pytest.mark.unit
def test_one_hostile_document_does_not_deny_the_split_to_the_rest(tmp_path):
    """Dropping the candidate, not failing the package.

    A book could carry one pathological document beside good ones. Refusing the
    whole package would let a single bad document deny the fix to every chapter
    in the book.
    """
    from cps.services import kepub_spine_splitter as module

    targets = [
        {"fragment": "f{}".format(i), "resolved": "OPS/huge.xhtml"}
        for i in range(module.MAX_SPLIT_PIECES + 10)
    ] + [
        {"fragment": "a", "resolved": "OPS/fine.xhtml"},
        {"fragment": "b", "resolved": "OPS/fine.xhtml"},
    ]
    candidates = module._split_candidates(targets)

    assert "OPS/huge.xhtml" not in candidates
    assert "OPS/fine.xhtml" in candidates


@pytest.mark.unit
def test_unique_id_does_not_restart_its_search_each_time(tmp_path):
    """The O(N^2) that made 2000 pieces cost two million str.format calls.

    Asserted through the cursor rather than by timing, so it cannot flake on a
    loaded machine.
    """
    from cps.services.kepub_spine_splitter import _unique_id

    occupied, cursor = {"id"}, {}
    first = _unique_id("id", occupied, cursor)
    assert first == "id-1"
    after_first = cursor["id"]

    for _ in range(50):
        _unique_id("id", occupied, cursor)

    assert cursor["id"] > after_first, "the cursor never advanced"
    assert cursor["id"] <= 60, "the search is restarting from 1 each call"


@pytest.mark.unit
def test_the_id_cursor_does_not_leak_between_packages(tmp_path):
    """It is passed in, not module-global.

    A global would grow one entry per distinct id for the life of the process and
    let one book's ids influence the next.
    """
    from cps.services import kepub_spine_splitter as module

    assert not hasattr(module, "_UNIQUE_ID_CURSOR"), (
        "the id cursor is module-global again; it must be scoped to one package")
