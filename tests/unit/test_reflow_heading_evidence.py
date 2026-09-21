"""Heading eligibility follows original source typography and geometry."""
import pymupdf
import pytest
from cps.services.reflow import extract,assemble,skeleton

pytestmark=pytest.mark.unit


def fixture(kind):
    doc=pymupdf.open()
    if kind=='continuation':
        previous_page=doc.new_page(width=500,height=700)
        previous_page.insert_text((50,600),'The source paragraph continues',fontsize=12)
    page=doc.new_page(width=500,height=700)
    body='Ordinary source prose carries the same body appearance.'
    center=50+pymupdf.get_text_length(body,fontname='cour',fontsize=12)/2
    for baseline in (70,85,100,205,220,235):page.insert_text((50,baseline),body,fontname='cour',fontsize=12)
    if kind=='body':
        for index,y in enumerate((150,165,180)):
            page.insert_text((62 if index==0 else 50,y),body,fontname='cour',fontsize=12)
    elif kind=='inline_bold':
        page.insert_text((50,150),'Emphasized lead-in',fontname='cobo',fontsize=12)
        for y in (165,180):page.insert_text((50,y),body,fontname='cour',fontsize=12)
    else:
        lines=['Source display title'] if kind!='centered_multiline' else ['Source display title','Continued title line']
        font='cobo' if kind in ('bold','figure_label','continuation') else 'cour'
        size=20 if kind=='large' else 12
        for index,text in enumerate(lines):
            if kind=='continuation':text='across the next page'
            x=center-pymupdf.get_text_length(text,fontname=font,fontsize=size)/2 if kind in ('centered','centered_multiline','margin') else 50
            page.insert_text((x,25 if kind=='margin' else 165+15*index),text,fontname=font,fontsize=size)
    raw=extract.read_page(doc,page.number)
    lines=[line for block in raw.text_blocks for line in block.lines if
           (line.bbox[1]<40 if kind=='margin' else 120<line.spans[0].origin_y<200)]
    bbox=(min(l.bbox[0] for l in lines),min(l.bbox[1] for l in lines),max(l.bbox[2] for l in lines),max(l.bbox[3] for l in lines))
    e=assemble.Element('p',runs=[['t',' '.join(l.text for l in lines)]],bbox=bbox,line_boxes=[l.bbox for l in lines])
    e.pno=raw.pno
    book=assemble.Book(elements=[e],pages={raw.pno:[e]},style=skeleton.BookStyle(body_size=12))
    if kind=='figure_label':book.figures=[{'pno':0,'bbox':(50,115,440,150)}]
    if kind=='continuation':
        previous=assemble.Element('p',runs=[['t','The source paragraph continues']])
        book.pages[0]=[previous]
    return doc,raw,book


@pytest.mark.parametrize('kind,accepted',[('body',False),('inline_bold',False),('bold',True),
    ('large',True),('centered',True),('centered_multiline',True),('margin',False),
    ('figure_label',False),('continuation',False)])
def test_source_role_admission(kind,accepted):
    from cps.services.reflow.heading_evidence import heading_evidence
    doc,raw,book=fixture(kind)
    proof=heading_evidence(book,raw.pno,raw,'native')[0]
    assert proof['supported'] is accepted,proof
    assert proof['reason'] and proof['source_line_boxes']
    doc.close()


def test_running_sidebar_line_is_not_a_display_heading(tmp_path):
    from cps.services.reflow.heading_evidence import heading_evidence
    doc=pymupdf.open();page=doc.new_page(width=700,height=700)
    for y in (140,155,170,185,200):
        page.insert_text((50,y),'Main column ordinary source paragraph.',fontsize=12)
        page.insert_text((430,y),'Sidebar body text.',fontsize=12)
    raw=extract.read_page(doc,0)
    line=next(l for b in raw.text_blocks for l in b.lines if l.bbox[0]>400 and 169<l.spans[0].origin_y<171)
    element=assemble.Element('p',runs=[['t',line.text]],bbox=line.bbox,line_boxes=[line.bbox])
    book=assemble.Book(elements=[element],pages={0:[element]},style=skeleton.BookStyle(body_size=12))
    assert not heading_evidence(book,0,raw,'native')[0]['supported']
    doc.close()


def test_geometry_precision_and_unknown_provenance_fail_closed():
    from cps.services.reflow.heading_evidence import heading_evidence
    doc,raw,book=fixture('bold')
    assert not heading_evidence(book,0,raw,'unidentified')[0]['supported']
    assert heading_evidence(book,0,raw,'native',90)[0]['reason']=='unsupported_source_coordinate_frame'
    assert heading_evidence(book,0,raw,'ocr',0,[700,500])[0]['reason']=='source_frame_mismatch'
    book.pages[0][0].line_boxes=[(float('nan'),0,20,20)]
    assert heading_evidence(book,0,raw,'native')[0]['reason']=='invalid_source_geometry'
    doc.close()


def test_complete_source_typography_has_no_word_count_shortcut(tmp_path):
    from cps.services.reflow import structural_ops as ops
    doc,raw,book=fixture('large')
    doc.close()
    doc=pymupdf.open();page=doc.new_page(width=900,height=1000)
    body='The source body reference uses ordinary text. ' * 3
    for y in (80,95,110,700,715,730):page.insert_text((50,y),body,fontsize=12)
    title=('A complete displayed title can span many lines while its source '
           'typography stays distinct and its exact word sequence remains intact '
           'without imposing an arbitrary number of words as a substitute for '
           'the actual visual evidence on the original printed page')
    page.insert_textbox((50,250,800,650),title,fontsize=20)
    path=tmp_path/'long-heading.pdf';doc.save(path);doc.close();doc=pymupdf.open(path)
    raw=extract.read_page(doc,0)
    lines=[l for b in raw.text_blocks for l in b.lines if 200<l.bbox[1]<650]
    element=assemble.Element('p',runs=[['t',' '.join(l.text for l in lines)]],
        bbox=(min(l.bbox[0] for l in lines),min(l.bbox[1] for l in lines),
              max(l.bbox[2] for l in lines),max(l.bbox[3] for l in lines)),line_boxes=[l.bbox for l in lines])
    book=assemble.Book(elements=[element],pages={0:[element]},style=skeleton.BookStyle(body_size=12))
    assert len(element.text.split())>40
    p=ops.prepare(book,doc,0,'test',{'layer':'native'},raw_page=raw)
    assert any(c['kind']=='heading' for c in p.candidates())
    doc.close()


def test_missing_or_partial_source_mapping_is_unsupported_without_guessing():
    from cps.services.reflow.heading_evidence import heading_evidence
    doc,raw,book=fixture('bold')
    assert not heading_evidence(book,0,None,'native')[0]['supported']
    book.pages[0][0].runs=[['t','Only a paraphrase of the printed title']]
    assert heading_evidence(book,raw.pno,raw,'native')[0]['reason']=='source_text_mismatch'
    doc.close()


def test_ocr_font_weight_is_not_native_typography_but_centered_geometry_can_be_supported():
    from cps.services.reflow.heading_evidence import heading_evidence
    doc,raw,book=fixture('bold')
    assert not heading_evidence(book,0,raw,'ocr')[0]['supported']
    doc.close()
    doc,raw,book=fixture('centered')
    assert heading_evidence(book,0,raw,'ocr')[0]['supported']
    doc.close()


def test_prepared_heading_requires_bound_source_and_rechecks_before_publication(tmp_path):
    from dataclasses import replace
    from cps.services.reflow import structural_ops as ops, build_epub
    doc,raw,book=fixture('bold')
    path=tmp_path/'source.pdf';doc.save(path);doc.close();doc=pymupdf.open(path)
    absent=ops.prepare(book,doc,0,'test',{'layer':'native'})
    assert absent.candidates() and all(c['kind']=='quote' for c in absent.candidates())
    p=ops.prepare(book,doc,0,'test',{'layer':'native'},raw_page=raw)
    cid=next(c['candidate_id'] for c in p.candidates() if c['kind']=='heading')
    response={'protocol':ops.PROTOCOL,'snapshot_id':p.snapshot_id,'select':[cid]}
    plan=p.accept(book,doc,response)
    target=tmp_path/'valid.epub'
    build_epub.build(book,str(target),doc=doc,operation_plans=[plan])
    assert build_epub.validate(str(target))==[]
    # A cache cannot turn missing geometry or a changed source body reference
    # into a previously admitted heading at the final builder seam.
    stale=replace(p,heading_source_json='{}')
    with pytest.raises(ops.ContractError):stale.accept(book,doc,response)
    book.style.body_size=24
    target=tmp_path/'stale.epub'
    with pytest.raises(ops.ContractError):build_epub.build(book,str(target),doc=doc,operation_plans=[plan])
    assert not target.exists()
    doc.close()
