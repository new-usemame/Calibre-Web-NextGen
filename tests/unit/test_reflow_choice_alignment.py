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


def test_transcription_image_heading_is_an_atomic_source_bound_choice(tmp_path):
    from dataclasses import replace
    from cps.services.reflow import native_text,heading_evidence
    doc,raw,book=fixture('centered')
    line=next(l for b in raw.text_blocks for l in b.lines if 150<l.spans[0].origin_y<180)
    span=line.spans[0];line.spans[0]=replace(span,transcription_uncertain=True)
    element=book.pages[0][0]
    record=native_text.descriptor(0,span.bbox,span.size,span.font,reason='transcript')
    element.runs=[['glyph',span.text,record]]
    path=tmp_path/'source.pdf';doc.save(path);doc.close();doc=pymupdf.open(path)
    try:
        source=prepare_source_page(book,0,{'layer':'native'})
        proof=heading_evidence.heading_evidence(book,0,raw,'native')[0]
        assert proof['supported'],proof
        without=ops.prepare(book,doc,0,'test',{'layer':'native'},raw_page=raw)
        assert not without.candidates(),'image words need their current sealed source page'
        prepared=ops.prepare(book,doc,0,'test',{'layer':'native'},source_page=source,raw_page=raw)
        chosen=next(c for c in prepared.candidates() if c['kind']=='heading')
        plan=prepared.accept(book,doc,dict(protocol=ops.PROTOCOL,snapshot_id=prepared.snapshot_id,
            select=[chosen['candidate_id']]),source_page=source)
        fragment=source.render(book,plan.compile(book,doc,source_page=source))
        root=ET.fromstring('<root>'+fragment+'</root>')
        heading=root.find('h2')
        assert heading is not None
        canonical=ET.fromstring('<root>'+source.html+'</root>').find('.//a[@class="source-glyph"]')
        assert ET.tostring(heading.find('a'))==ET.tostring(canonical)
        record['reason']='encoding'
        assert not heading_evidence.heading_evidence(book,0,raw,'native')[0]['supported']
    finally:doc.close()


def test_centering_keeps_wider_body_reference_beside_inset_display(tmp_path):
    from cps.services.reflow import skeleton,heading_evidence
    doc=pymupdf.open();page=doc.new_page(width=500,height=700)
    text='Ordinary source body establishes the full column.'
    width=pymupdf.get_text_length(text,fontname='cour',fontsize=12)
    for y in (90,105,120):page.insert_text((50,y),text,fontname='cour',fontsize=12)
    title='A centered source title'
    page.insert_text((50+(width-pymupdf.get_text_length(title,fontname='cour',fontsize=12))/2,165),title,fontname='cour',fontsize=12)
    for y in (210,225,240):page.insert_text((80,y),text[:-5],fontname='cour',fontsize=12)
    raw=extract.read_page(doc,0)
    book=assemble.assemble([skeleton.page_skeleton(raw,skeleton.BookStyle(12))],skeleton.BookStyle(12),[raw])
    index=next(i for i,e in enumerate(book.pages[0]) if e.text==title)
    proof=heading_evidence.heading_evidence(book,0,raw,'native')[index]
    assert proof['supported'] and proof['alignment']=='center',proof
    doc.close()
