"""A source role choice retains the converter's observed source alignment."""
import json
import zipfile
from xml.etree import ElementTree as ET
import pymupdf
import pytest
from cps.services.reflow import assemble,build_epub,extract,structural_ops as ops,typed_model
from cps.services.reflow.enriched_source import prepare_source_page
from tests.unit.test_reflow_heading_evidence import fixture

pytestmark=pytest.mark.unit
X='{http://www.w3.org/1999/xhtml}'


def test_centered_heading_choice_retains_source_alignment_and_words(tmp_path):
    doc,raw,book=fixture('centered')
    path=tmp_path/'source.pdf';doc.save(path);doc.close();doc=pymupdf.open(path)
    try:
        source=prepare_source_page(book,0,{'layer':'native'})
        prepared=ops.prepare(book,doc,0,typed_model.SOURCE_REVISION,{'layer':'native'},
                             source_page=source,raw_page=raw)
        candidate=next(c for c in prepared.candidates() if c['kind']=='heading')
        plan=prepared.accept(book,doc,dict(protocol=ops.PROTOCOL,snapshot_id=prepared.snapshot_id,
                                          select=[candidate['candidate_id']]),source_page=source)
        built=build_epub.build(book,tmp_path/'center.epub',doc=doc,source_pages={0:source},operation_plans=[plan])
        with zipfile.ZipFile(built.path) as archive:
            heads=[h for n in archive.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')
                   for h in ET.fromstring(archive.read(n)).iter(X+'h2')]
        assert len(heads)==1 and ''.join(heads[0].itertext())=='Source display title'
        assert 'source-heading-centered' in heads[0].get('class','').split()
    finally:doc.close()


def test_top_centered_unit_needs_current_canonical_body_reference():
    from cps.services.reflow.heading_evidence import heading_evidence
    doc,raw,book=fixture('margin')
    try:
        assert not heading_evidence(book,0,raw,'native')[0]['supported']
        line=next(l for b in raw.text_blocks for l in b.lines if 60<l.spans[0].origin_y<80)
        body=assemble.Element('p',runs=[['t',line.text]],bbox=line.bbox,line_boxes=[line.bbox],pno=0)
        book.pages[0].append(body);book.elements.append(body)
        proof=heading_evidence(book,0,raw,'native')[0]
        assert proof['supported'],proof
    finally:doc.close()
