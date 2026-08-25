# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Synthetic-archive coverage for fragment-anchored EPUB TOCs."""

import logging
from types import SimpleNamespace
import zipfile

import pytest


CONTAINER_XML = b"""<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

NCX_WITH_FRAGMENTS = b"""<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap>
    <navPoint><content src="chapter.xhtml#one"/></navPoint>
    <navPoint><content src="chapter.xhtml"/></navPoint>
    <navPoint><content src="chapter.xhtml#two"/></navPoint>
  </navMap>
</ncx>
"""

NCX_WITHOUT_FRAGMENTS = NCX_WITH_FRAGMENTS.replace(
    b"chapter.xhtml#one", b"chapter-one.xhtml"
).replace(
    b"chapter.xhtml#two", b"chapter-two.xhtml"
)

NAV_WITH_FRAGMENTS = b"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="chapter.xhtml#one">One</a></li>
        <li><a href="chapter.xhtml">Whole document</a></li>
        <li><a href="#local-position">Local position</a></li>
      </ol>
    </nav>
    <nav epub:type="landmarks">
      <a href="chapter.xhtml#not-a-toc-entry">Body</a>
    </nav>
  </body>
</html>
"""


def _matching_dual_tocs(target_count):
    ncx_targets = "\n".join(
        '<navPoint><content src="chapters/chapter.xhtml#anchor-{0:03d}"/></navPoint>'.format(index)
        for index in range(target_count)
    )
    nav_targets = "\n".join(
        '<li><a href="../chapters/chapter.xhtml#anchor-{0:03d}">{0}</a></li>'.format(index)
        for index in range(target_count)
    )
    ncx = (
        '<?xml version="1.0"?>'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
        + ncx_targets
        + '</navMap></ncx>'
    ).encode()
    nav = (
        '<?xml version="1.0"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><body>'
        '<nav epub:type="toc"><ol>'
        + nav_targets
        + '</ol></nav></body></html>'
    ).encode()
    return ncx, nav


def _opf(*manifest_items, version="3.0", spine_toc="", spine_ids=()):
    items = "\n".join(manifest_items)
    toc_attribute = f' toc="{spine_toc}"' if spine_toc else ""
    itemrefs = "".join('<itemref idref="{}"/>'.format(item_id) for item_id in spine_ids)
    return f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}">
  <manifest>{items}</manifest>
  <spine{toc_attribute}>{itemrefs}</spine>
</package>
""".encode()


def _write_epub(path, opf, members=()):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OPS/book.opf", opf)
        for name, content in members:
            archive.writestr(name, content)
    return path


def _count(path):
    from cps.services.kepub_package_normalizer import count_fragment_anchored_toc_targets

    return count_fragment_anchored_toc_targets(path)


def _normalize(path):
    from cps.services.kepub_package_normalizer import normalize_kepub_package

    return normalize_kepub_package(path)


def _probe(path):
    from cps.services.kepub_package_normalizer import kepub_package_needs_normalization

    return kepub_package_needs_normalization(path)


def _ncx(nav_targets=(), page_targets=(), nav_list_targets=()):
    nav_points = "".join(
        '<navPoint id="n{0}"><content src="{1}"/></navPoint>'.format(index, target)
        for index, target in enumerate(nav_targets)
    )
    pages = "".join(
        '<pageTarget id="p{0}"><content src="{1}"/></pageTarget>'.format(index, target)
        for index, target in enumerate(page_targets)
    )
    nav_targets_outside_map = "".join(
        '<navTarget id="l{0}"><content src="{1}"/></navTarget>'.format(index, target)
        for index, target in enumerate(nav_list_targets)
    )
    return (
        '<?xml version="1.0"?>'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
        '<navMap>{}</navMap><pageList>{}</pageList><navList>{}</navList></ncx>'.format(
            nav_points, pages, nav_targets_outside_map)
    ).encode()


def _fragment_epub(
        tmp_path, name, nav_targets, chapters, page_targets=(), nav_list_targets=()):
    manifest = [
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    ]
    members = [("OPS/toc.ncx", _ncx(nav_targets, page_targets, nav_list_targets))]
    spine_ids = []
    for index, (chapter_path, chapter_bytes) in enumerate(chapters.items()):
        item_id = "chapter{}".format(index)
        spine_ids.append(item_id)
        manifest.append(
            '<item id="{}" href="{}" media-type="application/xhtml+xml"/>'.format(
                item_id, chapter_path.replace(" ", "%20")))
        members.append(("OPS/" + chapter_path, chapter_bytes))
    return _write_epub(
        tmp_path / name,
        _opf(*manifest, version="2.0", spine_toc="ncx", spine_ids=spine_ids),
        members,
    )


def _ncx_sources(path):
    from lxml import etree

    with zipfile.ZipFile(path) as archive:
        document = etree.fromstring(archive.read("OPS/toc.ncx"))
    return document.xpath("//*[local-name()='content']/@src")


@pytest.mark.unit
def test_ncx_only_toc_counts_fragment_anchored_targets(tmp_path):
    package = _write_epub(
        tmp_path / "ncx-fragments.kepub",
        _opf(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            version="2.0",
            spine_toc="ncx",
        ),
        [("OPS/toc.ncx", NCX_WITH_FRAGMENTS)],
    )

    assert _count(package) == 2


@pytest.mark.unit
def test_single_fragment_at_first_rendered_position_is_stripped_safely(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<section><h1 id="ch1">Chapter</h1></section>tail after the anchor ancestor'
        b'</body></html>'
    )
    package = _fragment_epub(
        tmp_path, "top-anchor.kepub", ["chapter.xhtml#ch1"],
        {"chapter.xhtml": chapter})
    with zipfile.ZipFile(package) as archive:
        chapter_before = archive.read("OPS/chapter.xhtml")

    assert _normalize(package) is True
    assert _ncx_sources(package) == ["chapter.xhtml"]
    with zipfile.ZipFile(package) as archive:
        assert archive.read("OPS/chapter.xhtml") == chapter_before

    first_rewrite = package.read_bytes()
    assert _normalize(package) is False
    assert package.read_bytes() == first_rewrite


@pytest.mark.unit
@pytest.mark.parametrize(
    "prefix",
    [
        b"<p>Rendered before the anchor</p>",
        b'<img src="cover.jpg" alt=""/>',
    ],
    ids=["preceding-text", "preceding-image"],
)
def test_rendered_content_before_anchor_prevents_stripping(tmp_path, prefix):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>' + prefix
        + b'<h1 id="ch1">Chapter</h1></body></html>'
    )
    package = _fragment_epub(
        tmp_path, "rendered-predecessor.kepub", ["chapter.xhtml#ch1"],
        {"chapter.xhtml": chapter})
    before = package.read_bytes()

    assert _normalize(package) is False
    assert package.read_bytes() == before
    assert _ncx_sources(package) == ["chapter.xhtml#ch1"]


@pytest.mark.unit
def test_two_distinct_fragments_into_one_document_are_both_preserved(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<h1 id="one">One</h1><h2 id="two">Two</h2></body></html>'
    )
    package = _fragment_epub(
        tmp_path, "multiple-fragments.kepub",
        ["chapter.xhtml#one", "chapter.xhtml#two"],
        {"chapter.xhtml": chapter})

    assert _normalize(package) is False
    assert _ncx_sources(package) == ["chapter.xhtml#one", "chapter.xhtml#two"]


@pytest.mark.unit
def test_missing_anchor_is_preserved(tmp_path):
    chapter = b'<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter</h1></body></html>'
    package = _fragment_epub(
        tmp_path, "missing-anchor.kepub", ["chapter.xhtml#absent"],
        {"chapter.xhtml": chapter})

    assert _normalize(package) is False
    assert _ncx_sources(package) == ["chapter.xhtml#absent"]


@pytest.mark.unit
def test_legacy_named_anchor_at_top_is_stripped(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<a name="legacy"></a><p>Chapter</p></body></html>'
    )
    package = _fragment_epub(
        tmp_path, "legacy-anchor.kepub", ["chapter.xhtml#legacy"],
        {"chapter.xhtml": chapter})

    assert _normalize(package) is True
    assert _ncx_sources(package) == ["chapter.xhtml"]


@pytest.mark.unit
def test_percent_encoded_and_spaced_targets_resolve_to_the_same_anchor(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<h1 id="ch 1">Chapter</h1></body></html>'
    )
    package = _fragment_epub(
        tmp_path, "encoded-spaces.kepub",
        ["Chapter%2001.xhtml#ch%201", "Chapter 01.xhtml#ch 1"],
        {"Chapter 01.xhtml": chapter})

    assert _normalize(package) is True
    assert _ncx_sources(package) == ["Chapter%2001.xhtml", "Chapter 01.xhtml"]


@pytest.mark.unit
def test_page_list_and_nav_target_fragments_are_ignored(tmp_path):
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<h1 id="chapter">Chapter</h1><a id="page1"></a><p>Page one</p></body></html>'
    )
    package = _fragment_epub(
        tmp_path, "page-list.kepub", ["chapter.xhtml#chapter"],
        {"chapter.xhtml": chapter},
        page_targets=["chapter.xhtml#page1"],
        nav_list_targets=["chapter.xhtml#page1"])

    assert _count(package) == 1
    assert _normalize(package) is True
    assert _ncx_sources(package) == [
        "chapter.xhtml", "chapter.xhtml#page1", "chapter.xhtml#page1"]


@pytest.mark.unit
def test_qualifying_targets_are_stripped_independently_within_a_book(tmp_path):
    package = _fragment_epub(
        tmp_path, "partial-safe-rewrite.kepub",
        ["top.xhtml#top", "middle.xhtml#middle"],
        {
            "top.xhtml": (
                b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                b'<h1 id="top">Top</h1></body></html>'),
            "middle.xhtml": (
                b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                b'<p>Earlier</p><h1 id="middle">Middle</h1></body></html>'),
        },
    )

    assert _normalize(package) is True
    assert _ncx_sources(package) == ["top.xhtml", "middle.xhtml#middle"]


@pytest.mark.unit
def test_epub3_doc_toc_is_rewritten_but_landmarks_are_not(tmp_path):
    nav = b"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
  <nav role="doc-toc"><a href="chapter.xhtml#top">Chapter</a></nav>
  <nav role="doc-landmarks"><a href="chapter.xhtml#top">Landmark</a></nav>
</body></html>"""
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<h1 id="top">Top</h1></body></html>'
    )
    package = _write_epub(
        tmp_path / "epub3-nav.kepub",
        _opf(
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
        ),
        [("OPS/nav.xhtml", nav), ("OPS/chapter.xhtml", chapter)],
    )

    assert _normalize(package) is True
    with zipfile.ZipFile(package) as archive:
        rewritten = archive.read("OPS/nav.xhtml")
    assert rewritten.count(b'href="chapter.xhtml"') == 1
    assert rewritten.count(b'href="chapter.xhtml#top"') == 1


@pytest.mark.unit
def test_non_zip_counter_and_normalizer_never_raise(tmp_path):
    package = tmp_path / "truncated.kepub"
    package.write_bytes(b"PK\x03\x04truncated")

    assert _count(package) == 0
    assert _normalize(package) is None


@pytest.mark.unit
def test_htmlish_target_parse_failure_is_skipped_without_retryable_probe(tmp_path):
    from cps.services import kepub_package_normalizer as normalizer

    malformed = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        b'<meta name="generator" content="HTML"></head>'
        b'<body><h1 id="top">Chapter</h1></body></html>'
    )
    package = _fragment_epub(
        tmp_path, "htmlish-target.kepub", ["chapter.xhtml#top"],
        {"chapter.xhtml": malformed})

    assert _normalize(package) is False
    assert _probe(package).status == normalizer.PROBE_CLEAN
    assert _ncx_sources(package) == ["chapter.xhtml#top"]


@pytest.mark.unit
def test_htmlish_target_does_not_block_escaping_toc_relocation(tmp_path):
    from cps.services import kepub_package_normalizer as normalizer

    malformed = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        b'<meta name="generator" content="HTML"></head>'
        b'<body><h1 id="top">Chapter</h1></body></html>'
    )
    package = _write_epub(
        tmp_path / "htmlish-with-relocation.kepub",
        _opf(
            '<item id="ncx" href="../toc.ncx" media-type="application/x-dtbncx+xml"/>',
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
            version="2.0", spine_toc="ncx", spine_ids=["chapter"],
        ),
        [("toc.ncx", _ncx(["OPS/chapter.xhtml#top"])),
         ("OPS/chapter.xhtml", malformed)],
    )

    assert _probe(package).status == normalizer.PROBE_NEEDS_NORMALIZATION
    assert _normalize(package) is True
    with zipfile.ZipFile(package) as archive:
        assert "toc.ncx" not in archive.namelist()
        assert "OPS/toc.ncx" in archive.namelist()
        rewritten = archive.read("OPS/toc.ncx")
    assert b'src="chapter.xhtml#top"' in rewritten
    assert _probe(package).status == normalizer.PROBE_CLEAN


@pytest.mark.unit
def test_htmlish_target_is_skipped_while_valid_target_is_stripped(tmp_path):
    malformed = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        b'<meta name="generator" content="HTML"></head>'
        b'<body><h1 id="bad">Bad</h1></body></html>'
    )
    valid = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<h1 id="good">Good</h1></body></html>'
    )
    package = _fragment_epub(
        tmp_path, "mixed-targets.kepub",
        ["bad.xhtml#bad", "good.xhtml#good"],
        {"bad.xhtml": malformed, "good.xhtml": valid})

    assert _normalize(package) is True
    assert _ncx_sources(package) == ["bad.xhtml#bad", "good.xhtml"]


@pytest.mark.unit
def test_absent_target_document_is_skipped_without_retryable_probe(tmp_path):
    from cps.services import kepub_package_normalizer as normalizer

    package = _fragment_epub(
        tmp_path, "absent-target.kepub", ["missing.xhtml#top"], {})

    assert _normalize(package) is False
    assert _probe(package).status == normalizer.PROBE_CLEAN
    assert _ncx_sources(package) == ["missing.xhtml#top"]


@pytest.mark.unit
def test_malformed_toc_does_not_block_escaping_href_relocation(tmp_path):
    from cps.services import kepub_package_normalizer as normalizer

    package = _write_epub(
        tmp_path / "malformed-toc-with-relocation.kepub",
        _opf(
            '<item id="ncx" href="../toc.ncx" media-type="application/x-dtbncx+xml"/>',
            version="2.0", spine_toc="ncx",
        ),
        [("toc.ncx", b"<ncx><navMap><navPoint>")],
    )

    assert _probe(package).status == normalizer.PROBE_NEEDS_NORMALIZATION
    assert _normalize(package) is True
    with zipfile.ZipFile(package) as archive:
        assert "toc.ncx" not in archive.namelist()
        assert archive.read("OPS/toc.ncx") == b"<ncx><navMap><navPoint>"
    assert _probe(package).status == normalizer.PROBE_CLEAN


def _assert_nonterminal_probe(package):
    from cps.services import kepub_package_normalizer as normalizer

    assert _probe(package).status in {
        normalizer.PROBE_CLEAN,
        normalizer.PROBE_NEEDS_NORMALIZATION,
    }


@pytest.mark.unit
def test_missing_manifest_declared_toc_skips_fragment_transform(tmp_path):
    package = _write_epub(
        tmp_path / "missing-declared-toc.kepub",
        _opf(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            version="2.0", spine_toc="ncx"),
    )

    _assert_nonterminal_probe(package)
    assert _normalize(package) is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "target",
    [r"sub\ch1.xhtml#top", "/ch1.xhtml#top", "../ch1.xhtml#top"],
    ids=["backslash", "absolute", "escaping"],
)
def test_uncontained_toc_target_skips_fragment_transform(tmp_path, target):
    package = _fragment_epub(
        tmp_path, "uncontained-target.kepub", [target],
        {"ch1.xhtml": (
            b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            b'<h1 id="top">Top</h1></body></html>')})

    _assert_nonterminal_probe(package)
    assert _normalize(package) is False
    assert _ncx_sources(package) == [target]


@pytest.mark.unit
def test_oversized_target_document_skips_fragment_transform(tmp_path):
    from cps.services import kepub_package_normalizer as normalizer

    oversized = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><h1 id="top">Top</h1>'
        + b"x" * normalizer.MAX_CONTENT_DOCUMENT_BYTES
        + b"</body></html>"
    )
    package = _fragment_epub(
        tmp_path, "oversized-content.kepub", ["chapter.xhtml#top"],
        {"chapter.xhtml": oversized})

    _assert_nonterminal_probe(package)
    assert _normalize(package) is False
    assert _ncx_sources(package) == ["chapter.xhtml#top"]


@pytest.mark.unit
def test_spine_nav_rewrite_changes_only_the_selected_href_bytes(tmp_path):
    nav = (
        b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<script src="s.js"></script><div class="x"></div>'
        b'<span class="koboSpan" id="kobo.1.1"></span>'
        b'<nav role="doc-toc"><a href="chapter.xhtml#top">Chapter</a></nav>'
        b'</body></html>'
    )
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<h1 id="top">Top</h1></body></html>'
    )
    package = _write_epub(
        tmp_path / "spine-nav.kepub",
        _opf(
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
            spine_ids=["nav", "chapter"]),
        [("OPS/nav.xhtml", nav), ("OPS/chapter.xhtml", chapter)],
    )

    assert _normalize(package) is True
    with zipfile.ZipFile(package) as archive:
        rewritten = archive.read("OPS/nav.xhtml")
        assert archive.read("OPS/chapter.xhtml") == chapter
    assert rewritten == nav.replace(
        b'href="chapter.xhtml#top"', b'href="chapter.xhtml"')
    assert rewritten.startswith(b"\xef\xbb\xbf<?xml")
    assert b'<script src="s.js"></script>' in rewritten
    assert b'<div class="x"></div>' in rewritten
    assert b'<span class="koboSpan" id="kobo.1.1"></span>' in rewritten


@pytest.mark.unit
def test_validation_rejects_planner_that_drops_a_toc_target(
        tmp_path, monkeypatch):
    from cps.services import kepub_package_normalizer as normalizer

    package = _fragment_epub(
        tmp_path, "planner-corruption.kepub", ["chapter.xhtml#top"],
        {"chapter.xhtml": (
            b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            b'<h1 id="top">Top</h1></body></html>')})
    original = package.read_bytes()
    real_plan = normalizer._plan_toc_fragment_rewrites

    def corrupt_plan(*args, **kwargs):
        rewrites, edits = real_plan(*args, **kwargs)
        rewrites["OPS/toc.ncx"] = rewrites["OPS/toc.ncx"].replace(
            b'<navPoint id="n0"><content src="chapter.xhtml"/></navPoint>', b"")
        return rewrites, edits

    monkeypatch.setattr(normalizer, "_plan_toc_fragment_rewrites", corrupt_plan)

    assert normalizer.normalize_kepub_package(package) is None
    assert package.read_bytes() == original


@pytest.mark.unit
def test_probe_reads_fragment_targets_with_the_planners_bounded_cache(
        tmp_path, monkeypatch):
    from cps.services import kepub_package_normalizer as normalizer

    package = _fragment_epub(
        tmp_path, "cheap-probe.kepub", ["chapter.xhtml#top"],
        {"chapter.xhtml": (
            b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            b'<h1 id="top">Top</h1></body></html>')})
    real_read = normalizer._read_bounded_member
    reads = []

    def recording_read(archive, name, limit, description):
        reads.append(name)
        return real_read(archive, name, limit, description)

    monkeypatch.setattr(normalizer, "_read_bounded_member", recording_read)

    assert _probe(package).status == normalizer.PROBE_NEEDS_NORMALIZATION
    assert reads.count("OPS/toc.ncx") == 1
    assert reads.count("OPS/chapter.xhtml") == 1


def _assert_probe_normalize_fixed_point(package):
    from cps.services import kepub_package_normalizer as normalizer

    rewrite_count = 0
    for _round in range(3):
        inspection = _probe(package)
        if inspection.status == normalizer.PROBE_CLEAN:
            assert rewrite_count <= 1
            return
        assert inspection.status == normalizer.PROBE_NEEDS_NORMALIZATION
        # A needs-normalization answer is a promise that the planner will
        # actually change the package, never a request for an endless rescan.
        assert _normalize(package) is True
        rewrite_count += 1
    pytest.fail("probe and normalizer did not reach a fixed point in three rounds")


@pytest.mark.unit
@pytest.mark.parametrize(
    "skip_reason",
    [
        "oversized-target",
        "absent-target",
        "unparseable-target",
        "unparseable-toc",
        "malformed-href",
        "multiple-distinct-fragments",
    ],
)
def test_probe_and_normalizer_converge_for_fragment_skip_reasons(
        tmp_path, monkeypatch, skip_reason):
    from cps.services import kepub_package_normalizer as normalizer

    valid_target = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<h1 id="top">Top</h1></body></html>'
    )
    if skip_reason == "oversized-target":
        monkeypatch.setattr(normalizer, "MAX_CONTENT_DOCUMENT_BYTES", 64)
        package = _fragment_epub(
            tmp_path, "oversized-target.kepub", ["chapter.xhtml#top"],
            {"chapter.xhtml": valid_target})
    elif skip_reason == "absent-target":
        package = _fragment_epub(
            tmp_path, "absent-target-convergence.kepub",
            ["missing.xhtml#top"], {})
    elif skip_reason == "unparseable-target":
        package = _fragment_epub(
            tmp_path, "unparseable-target.kepub", ["chapter.xhtml#top"],
            {"chapter.xhtml": b"<html><body><h1 id='top'>"})
    elif skip_reason == "unparseable-toc":
        package = _write_epub(
            tmp_path / "unparseable-toc.kepub",
            _opf(
                '<item id="ncx" href="toc.ncx" '
                'media-type="application/x-dtbncx+xml"/>',
                version="2.0", spine_toc="ncx"),
            [("OPS/toc.ncx", b"<ncx><navMap><navPoint>")],
        )
    elif skip_reason == "malformed-href":
        package = _fragment_epub(
            tmp_path, "malformed-href.kepub", [r"chapter\file.xhtml#top"],
            {"chapter.xhtml": valid_target})
    else:
        package = _fragment_epub(
            tmp_path, "multiple-fragments-convergence.kepub",
            ["chapter.xhtml#top", "chapter.xhtml#second"],
            {"chapter.xhtml": valid_target.replace(
                b"</body>", b'<h2 id="second">Second</h2></body>')})

    _assert_probe_normalize_fixed_point(package)


@pytest.mark.unit
@pytest.mark.parametrize("element", ["hr", "br", "input", "select", "button", "picture"])
def test_additional_rendered_elements_prevent_stripping(tmp_path, element):
    prefix = ("<{}></{}>".format(element, element)).encode()
    package = _fragment_epub(
        tmp_path, "rendered-element.kepub", ["chapter.xhtml#top"],
        {"chapter.xhtml": (
            b'<html xmlns="http://www.w3.org/1999/xhtml"><body>' + prefix
            + b'<h1 id="top">Top</h1></body></html>')})

    assert _normalize(package) is False
    assert _ncx_sources(package) == ["chapter.xhtml#top"]


@pytest.mark.unit
def test_comments_and_processing_instructions_do_not_prevent_stripping(tmp_path):
    package = _fragment_epub(
        tmp_path, "comment-before-anchor.kepub", ["chapter.xhtml#top"],
        {"chapter.xhtml": (
            b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            b'<!-- generated by calibre --><?generator calibre?>'
            b'<h1 id="top">Top</h1></body></html>')})

    assert _normalize(package) is True
    assert _ncx_sources(package) == ["chapter.xhtml"]


@pytest.mark.unit
def test_bom_prefixed_ncx_preserves_bom_and_all_non_target_bytes(tmp_path):
    ncx = b"\xef\xbb\xbf" + _ncx(["chapter.xhtml#top"])
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<h1 id="top">Top</h1></body></html>'
    )
    package = _write_epub(
        tmp_path / "bom-ncx.kepub",
        _opf(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
            version="2.0", spine_toc="ncx", spine_ids=["chapter"]),
        [("OPS/toc.ncx", ncx), ("OPS/chapter.xhtml", chapter)],
    )

    assert _normalize(package) is True
    with zipfile.ZipFile(package) as archive:
        rewritten = archive.read("OPS/toc.ncx")
    assert rewritten == ncx.replace(
        b'src="chapter.xhtml#top"', b'src="chapter.xhtml"')


@pytest.mark.unit
@pytest.mark.parametrize(
    "internal_subset_item",
    [
        b'<!-- ]> <stray src="decoy.xhtml#wrong"/> -->',
        b'<?trap ]> <stray src="decoy.xhtml#wrong"/> ?>',
    ],
    ids=["comment", "processing-instruction"],
)
def test_doctype_internal_subset_markup_cannot_desync_toc_edit(
        tmp_path, internal_subset_item):
    ncx = (
        b'<?xml version="1.0"?>\n<!DOCTYPE ncx [ '
        + internal_subset_item
        + b' ]>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
        b'<navPoint><navLabel><text src="SIBLING.xhtml#zzz">One</text></navLabel>'
        b'<content src="chapter.xhtml#top"/></navPoint></navMap></ncx>'
    )
    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<h1 id="top">Top</h1></body></html>'
    )
    package = _write_epub(
        tmp_path / "doctype-internal-subset.kepub",
        _opf(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
            version="2.0", spine_toc="ncx", spine_ids=["chapter"]),
        [("OPS/toc.ncx", ncx), ("OPS/chapter.xhtml", chapter)],
    )

    assert _probe(package).status == "needs_normalization"
    assert _normalize(package) is True
    with zipfile.ZipFile(package) as archive:
        rewritten = archive.read("OPS/toc.ncx")
    assert rewritten == ncx.replace(
        b'src="chapter.xhtml#top"', b'src="chapter.xhtml"')
    assert b'src="SIBLING.xhtml#zzz"' in rewritten
    assert _probe(package).status == "clean"
    assert _normalize(package) is False


@pytest.mark.unit
def test_same_toc_path_declared_under_two_kinds_applies_both_edits(tmp_path):
    dual = b"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
  <navMap><navPoint><content src="one.xhtml#one"/></navPoint></navMap>
  <nav role="doc-toc"><a href="two.xhtml#two">Two</a></nav>
</body></html>"""
    package = _write_epub(
        tmp_path / "dual-kind-one-path.kepub",
        _opf(
            '<item id="ncx" href="nav.xhtml" media-type="application/x-dtbncx+xml"/>',
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="one" href="one.xhtml" media-type="application/xhtml+xml"/>',
            '<item id="two" href="two.xhtml" media-type="application/xhtml+xml"/>',
            version="2.0", spine_toc="ncx", spine_ids=["one", "two"]),
        [
            ("OPS/nav.xhtml", dual),
            ("OPS/one.xhtml", b'<html><body><h1 id="one">One</h1></body></html>'),
            ("OPS/two.xhtml", b'<html><body><h1 id="two">Two</h1></body></html>'),
        ],
    )

    assert _normalize(package) is True
    with zipfile.ZipFile(package) as archive:
        rewritten = archive.read("OPS/nav.xhtml")
    assert b'src="one.xhtml"' in rewritten
    assert b'href="two.xhtml"' in rewritten
    assert b"#one" not in rewritten
    assert b"#two" not in rewritten
    assert _normalize(package) is False


@pytest.mark.unit
def test_toc_without_fragments_reports_zero(tmp_path):
    package = _write_epub(
        tmp_path / "ncx-clean.epub",
        _opf(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            version="2.0",
            spine_toc="ncx",
        ),
        [("OPS/toc.ncx", NCX_WITHOUT_FRAGMENTS)],
    )

    assert _count(package) == 0


@pytest.mark.unit
def test_nav_only_toc_counts_fragments_but_not_landmarks(tmp_path):
    package = _write_epub(
        tmp_path / "nav-fragments.epub",
        _opf('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'),
        [("OPS/nav.xhtml", NAV_WITH_FRAGMENTS)],
    )

    assert _count(package) == 2


@pytest.mark.unit
def test_matching_ncx_and_nav_targets_are_counted_once_per_package(tmp_path):
    ncx, nav = _matching_dual_tocs(42)
    package = _write_epub(
        tmp_path / "dual-toc-fragments.kepub",
        _opf(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            '<item id="nav" href="nav/toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            spine_toc="ncx",
        ),
        [("OPS/toc.ncx", ncx), ("OPS/nav/toc.xhtml", nav)],
    )

    assert _count(package) == 42


@pytest.mark.unit
@pytest.mark.parametrize("toc_state", ["absent", "malformed"])
def test_absent_or_malformed_toc_never_raises(tmp_path, toc_state):
    if toc_state == "absent":
        opf = _opf('<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>')
        members = []
    else:
        opf = _opf('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        members = [("OPS/nav.xhtml", b"<html><nav")]
    package = _write_epub(tmp_path / f"{toc_state}.epub", opf, members)

    assert _count(package) == 0


@pytest.mark.unit
def test_conversion_diagnostic_names_book_and_fragment_count(tmp_path, caplog, monkeypatch):
    import cps.helper  # noqa: F401 - establish the application's normal import order
    from cps.tasks import convert

    book_path = tmp_path / "affected"
    (tmp_path / "affected.epub").write_bytes(b"source")
    book = SimpleNamespace(
        id=42,
        title="Synthetic Fragment Book",
        path="Synthetic/Fragment Book",
        data=[SimpleNamespace(name="affected")],
    )

    class Query:
        def filter(self, *_args):
            return self

        def one_or_none(self):
            return None

    class Session:
        def query(self, *_args):
            return Query()

        def merge(self, _row):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class LocalDB:
        def __init__(self, **_kwargs):
            self.session = Session()

        def get_book(self, _book_id):
            return book

        def get_book_format(self, *_args):
            return None

    def convert_package(*_args):
        _write_epub(
            tmp_path / "affected.kepub",
            _opf('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'),
            [("OPS/nav.xhtml", NAV_WITH_FRAGMENTS)],
        )
        return 0, None

    monkeypatch.setattr(convert.db, "CalibreDB", LocalDB)
    monkeypatch.setattr(convert.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(convert.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(convert.helper, "mark_book_modified", lambda *_args, **_kwargs: None)
    task = convert.TaskConvert(
        str(book_path), 42, "convert",
        {"old_book_format": "EPUB", "new_book_format": "KEPUB"}, None,
    )
    monkeypatch.setattr(task, "_convert_kepubify", convert_package)
    monkeypatch.setattr(task, "_handleSuccess", lambda: None)

    with caplog.at_level(logging.WARNING):
        assert task._convert_ebook_format() == "affected.kepub"

    message = caplog.text
    assert "Synthetic Fragment Book" in message
    assert "42" in message
    assert "2 fragment-anchored TOC targets" in message
    assert "highlights" in message.lower()
