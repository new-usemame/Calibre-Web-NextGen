"""Lossless repair of the Amazon leftover reported in #1528."""
import hashlib
import os
import zipfile
from xml.dom import minidom

import pytest

from tests.unit.test_kindle_epub_fixer_idempotency import (
    build_epub, epub_payload, fixer_module, backups_taken,
)

pytestmark = pytest.mark.unit


def replace_payload(book, payload):
    with zipfile.ZipFile(book, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in payload.items():
            z.writestr(name, data, compress_type=zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED)


@pytest.mark.parametrize("filename", ["chapter.xhtml", "chapter.html", "chapter.htm", "diagram.svg",
                                           "CHAPTER.XHTML", "CHAPTER.HTML", "Chapter.HtM", "Diagram.SvG"])
def test_process_removes_only_amazon_attribute_and_second_run_is_noop(fixer_module, tmp_path, filename):
    book = build_epub(tmp_path / "book.epub")
    payload = epub_payload(book)
    source = ('<?xml version="1.0" encoding="utf-8"?>\n'
              '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
              '<meta charset="utf-8"/></head><body><p class="c11">'
              '<span class="c13" data-AmznRemoved="mobi7">*<b>Keep — café</b></span>'
              '<span data-amznremoved=\'kf8\' title="Keep > too">second</span>'
              '</p></body></html>')
    payload["OEBPS/" + filename] = source.encode("utf-8")
    replace_payload(book, payload)
    result = fixer_module.EPUBFixer().process(str(book))
    output = epub_payload(book)
    expected = source.replace(' data-AmznRemoved="mobi7"', '').replace(" data-amznremoved='kf8'", '')
    assert output["OEBPS/" + filename] == expected.encode("utf-8")
    assert {k: v for k, v in output.items() if k != "OEBPS/" + filename} == {
        k: v for k, v in payload.items() if k != "OEBPS/" + filename}
    dom = minidom.parseString(output["OEBPS/" + filename])
    assert len(dom.getElementsByTagName("span")) == 2
    assert any("Amazon" in issue and "2" in issue for issue in result)
    assert backups_taken(fixer_module) == ["book.epub"]
    digest = hashlib.sha256(book.read_bytes()).hexdigest()
    os.utime(book, (1500000000, 1500000000))
    assert fixer_module.EPUBFixer().process(str(book)) == []
    assert hashlib.sha256(book.read_bytes()).hexdigest() == digest
    assert book.stat().st_mtime == 1500000000


def test_process_does_not_remove_marker_like_text(fixer_module, tmp_path):
    book = build_epub(tmp_path / "book.epub")
    source = ('<?xml version="1.0" encoding="utf-8"?>\n'
              '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
              '<!-- <span data-AmznRemoved=\'comment\'> -->'
              '<![CDATA[<span data-AmznRemoved=\'quoted\'>keep</span>]]>'
              '<p title=" data-AmznRemoved=\'attribute text\'" data-AmznRemoved-extra="keep" '
              'data-good="keep">data-AmznRemoved="prose"</p>'
              '</body></html>')
    payload = epub_payload(book)
    payload["OEBPS/text.xhtml"] = source.encode()
    replace_payload(book, payload)
    assert fixer_module.EPUBFixer().process(str(book)) == []
    assert epub_payload(book)["OEBPS/text.xhtml"] == source.encode()
    assert backups_taken(fixer_module) == []


def test_parser_handles_multiline_namespaced_and_quoted_attributes(fixer_module):
    source = ('<h:html xmlns:h="http://www.w3.org/1999/xhtml" xmlns:keep="urn:keep">'
              '<h:p title="quote &quot; > data-AmznRemoved=\'literal\'"\n'
              "  data-AmznRemoved = 'mobi7 > retained text' keep:data-AmznRemoved='stay' "
              'DATA-AMZNREMOVED="kf8">café</h:p></h:html>')
    expected = source.replace("\n  data-AmznRemoved = 'mobi7 > retained text'", '').replace(
        ' DATA-AMZNREMOVED="kf8"', '')
    output, count = fixer_module._remove_amazon_marker_attributes(source)
    assert output == expected
    assert count == 2
    minidom.parseString(output)


@pytest.mark.parametrize("source", [
    '<html><span data-AmznRemoved="mobi7">keep</span><unclosed></html>',
    '<html><span data-AmznRemoved="mobi7">keep</span><bad:p>unbound</bad:p></html>',
])
def test_malformed_xml_gets_no_partial_edit(fixer_module, capsys, source):
    fixer = fixer_module.EPUBFixer()
    fixer.files = {"chapter.xhtml": source}
    fixer.remove_amazon_marker_attributes()
    assert fixer.files["chapter.xhtml"] == source
    assert fixer.fixed_problems == []
    assert "malformed XML" in capsys.readouterr().out


def test_external_entity_is_never_substituted(fixer_module, tmp_path):
    sentinel = tmp_path / "private-text.txt"
    sentinel.write_text("do not insert local file data")
    source = (f'<!DOCTYPE html [<!ENTITY external SYSTEM "{sentinel.as_uri()}">]>'
              '<html><span data-AmznRemoved="mobi7">&external;</span></html>')
    output, count = fixer_module._remove_amazon_marker_attributes(source)
    assert count == 1
    assert output == source.replace(' data-AmznRemoved="mobi7"', '')
    assert "do not insert" not in output


def test_entity_definitions_are_not_rewritten(fixer_module):
    source = ('<!DOCTYPE html [<!ENTITY example "&lt;span data-AmznRemoved=\'literal\'&gt;">]>'
              '<html>&example;<span data-AmznRemoved="mobi7">keep</span></html>')
    output, count = fixer_module._remove_amazon_marker_attributes(source)
    assert count == 1
    assert output == source.replace(' data-AmznRemoved="mobi7"', '')


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_original_target_encoding_is_preserved(fixer_module, tmp_path, encoding):
    book = build_epub(tmp_path / "book.epub")
    source = (f'<?xml version="1.0" encoding="{encoding}"?>\n'
              '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
              '<p data-AmznRemoved="mobi7">café — keep</p></body></html>')
    payload = epub_payload(book)
    payload["OEBPS/text.xhtml"] = source.encode(encoding)
    replace_payload(book, payload)
    fixer_module.EPUBFixer().process(str(book))
    output = epub_payload(book)["OEBPS/text.xhtml"]
    assert output.decode(encoding).endswith(
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>café — keep</p></body></html>')
    minidom.parseString(output)


@pytest.mark.parametrize("tag", [
    '<meta data-note="Keep > this" charset = \'latin-1\' />',
    '<meta charset=latin-1 data-note="keep">',
    '<meta http-equiv="content-type" content="text/html; charset=latin-1"/>',
])
def test_charset_update_preserves_existing_meta_structure(fixer_module, tag):
    source = '<html><head>' + tag + '</head><body>keep</body></html>'
    output = fixer_module.EPUBFixer()._update_html_charset(source, "utf-8")
    assert output == source.replace("latin-1", "utf-8")


def test_missing_charset_insert_remains_xml(fixer_module):
    source = '<html><head><title>Keep</title></head><body>text</body></html>'
    output = fixer_module.EPUBFixer()._update_html_charset(source, "utf-8")
    assert '<meta charset="utf-8"/>' in output
    minidom.parseString(output)


def test_clean_bom_document_is_not_given_a_second_xml_declaration(fixer_module, tmp_path):
    book = build_epub(tmp_path / "book.epub")
    source = ('<?xml version="1.0" encoding="utf-16"?>\n'
              '<html xmlns="http://www.w3.org/1999/xhtml"><body>keep</body></html>')
    payload = epub_payload(book)
    payload["OEBPS/text.xhtml"] = source.encode("utf-16")
    replace_payload(book, payload)
    assert fixer_module.EPUBFixer().process(str(book)) == []
    assert epub_payload(book) == payload


@pytest.mark.parametrize("filename", ["chapter.xhtml", "CHAPTER.XHTML", "chapter.html", "CHAPTER.HTML",
                                      "chapter.htm", "CHAPTER.HTM", "diagram.svg", "DIAGRAM.SVG"])
def test_legacy_encoding_roundtrip_keeps_reader_text(fixer_module, tmp_path, filename):
    book = build_epub(tmp_path / "book.epub")
    source = ('<?xml version="1.0" encoding="iso-8859-1"?>'
              '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
              '<meta charset="iso-8859-1"/></head><body>'
              '<p data-AmznRemoved="mobi7">café</p></body></html>')
    payload = epub_payload(book)
    payload["OEBPS/" + filename] = source.encode("iso-8859-1")
    replace_payload(book, payload)
    result = fixer_module.EPUBFixer().process(str(book))
    repaired = epub_payload(book)["OEBPS/" + filename]
    dom = minidom.parseString(repaired)
    paragraph = dom.getElementsByTagName("p")[0]
    assert paragraph.firstChild.data == "café"
    assert not paragraph.hasAttribute("data-AmznRemoved")
    assert any("Amazon" in issue for issue in result)
    digest = hashlib.sha256(book.read_bytes()).hexdigest()
    assert fixer_module.EPUBFixer().process(str(book)) == []
    assert hashlib.sha256(book.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("filename,source", [
    ("UPPER.CSS", '@charset "iso-8859-1";\np.note:before { content: "café"; }'),
    ("UPPER.OPF", '<?xml version="1.0" encoding="iso-8859-1"?><package>café</package>'),
])
def test_case_expansion_is_limited_to_marker_markup(fixer_module, tmp_path, filename, source):
    book = build_epub(tmp_path / "book.epub")
    payload = epub_payload(book)
    payload["OEBPS/" + filename] = source.encode("iso-8859-1")
    replace_payload(book, payload)
    assert fixer_module.EPUBFixer().process(str(book)) == []
    assert epub_payload(book) == payload
    assert backups_taken(fixer_module) == []
