"""Synthetic final-document coverage preserves assembled furniture ownership."""
from types import SimpleNamespace
import pymupdf
import pytest
from cps.services.reflow import assemble,extract,skeleton,source_inventory
from cps.services.reflow import occurrence_coverage as coverage,unit_source,vision_pipeline as vp
from tests.unit.test_reflow_vision_pilot import SyntheticReader

pytestmark=pytest.mark.unit


def _line(text,box):
    return extract.Line([extract.Span(text,12,'serif',0,box)],box)


def _case(monkeypatch,*,furniture='17',reason='folio',change=None,missing_body=False):
    body=_line('River lamps remain.',(10,40,130,52));foot=_line(furniture,(140,370,200,382))
    regions=[skeleton.Region('body',[body],bbox=body.bbox),skeleton.Region('furniture',[foot],bbox=foot.bbox,reason=reason)]
    raw=extract.RawPage(0,300,400,[extract.Block(0,(0,0,300,400),[body,foot])])
    if change=='unsupported_reason':regions[1].reason='generic_margin'
    if change=='shared_owner':regions.append(skeleton.Region('body',[foot],bbox=foot.bbox))
    inv=source_inventory.capture(source_inventory.catalog(raw),skeleton.PageSkeleton(0,300,400,regions))
    body_words=[dict(text=t,bbox=[10+i*40,40,42+i*40,52])for i,t in enumerate(body.text.split())]
    footer_words=[dict(text=t,bbox=[140+i*28,370,164+i*28,382])for i,t in enumerate(furniture.split())]
    frames={0:body_words+footer_words}
    if change=='stale_geometry':
        inv['source_geometry']=dict(space='reading',orientation=90,source_rotation=0,page_rect=[0,0,300,400])
        inv['sha256']=source_inventory.digest({k:v for k,v in inv.items()if k!='sha256'})
    if change=='wrong_page':inv['page']=1
    if change=='tampered':inv['regions'][1]['suggested_kind']='body'
    if change=='missing_owner':inv['ownership']=[r for r in inv['ownership']if r['line_id']!='l1']
    if change=='partial_line':frames[0]=body_words+footer_words[:1]
    if change=='clipped_geometry':frames[0][-1]['bbox'][3]+=2
    if change=='body_number':frames[0].append(dict(text='17',bbox=[10,120,25,132]))
    texts=body.text if not missing_body else 'River lamps'
    docs={'ch001.xhtml':'<html><body><p data-source-page="0" data-source-block="native-0">'+texts+'</p></body></html>'}
    records={0:dict(blocks=[dict(id='native-0',role='body',native_source_occurrences=body_words,published_tokens=body.text.split())])}
    if change=='live_conflict':
        docs['ch001.xhtml']=docs['ch001.xhtml'].replace('</body>','<p data-source-page="0" data-source-block="native-1">17</p></body>')
        records[0]['blocks'].append(dict(id='native-1',role='body',native_source_occurrences=footer_words,published_tokens=['17']))
    package=SimpleNamespace(images={},aliases={});images={};calls=[]
    def retain(display,pno,bid,role,writer,assets,reason):
        calls.append(pno);name='images/retained.jpg';writer.images[name]=24;assets[name]=dict(page=0,bbox=[0,0,300,400])
        return '<p><img src="'+name+'"/></p>',[],dict(image=name,source_reference='original-page')
    monkeypatch.setattr(unit_source,'source_unit',retain)
    result=coverage.final_publication(docs,records,frames,images,package,{0:None},{0:'ch001.xhtml'},source_inventory={0:inv})
    return result,calls


@pytest.mark.parametrize('reason',['folio','sequence_folio'])
def test_final_publication_uses_complete_owned_furniture_line(monkeypatch,reason):
    result,calls=_case(monkeypatch,furniture='17 Wharf',reason=reason)
    assert calls==[]
    assert result['totals']==dict(inventory=3,live=3,print=0,missing=0,excluded=2)
    assert [r['disposition']for r in result['pages'][0]['rows'][-2:]]==['excluded','excluded']
    assert {r['rule']for r in result['pages'][0]['rows'][-2:]}=={'source_inventory_furniture'}


@pytest.mark.parametrize('change',['shared_owner','wrong_page','tampered','missing_owner','partial_line','clipped_geometry','unsupported_reason','stale_geometry'])
def test_unproved_or_contradictory_inventory_keeps_original_print(monkeypatch,change):
    result,calls=_case(monkeypatch,furniture='17 Wharf',change=change)
    assert calls==[0]
    assert result['totals']['excluded']==0
    assert result['totals']['print']>=1
    assert result['totals']['missing']==0


def test_body_number_with_equal_footer_spelling_stays_in_reading_frame(monkeypatch):
    result,calls=_case(monkeypatch,change='body_number');rows=result['pages'][0]['rows']
    assert calls==[0]
    assert rows[-2]['disposition']=='excluded'
    assert rows[-1]['disposition']=='print'
    assert rows[-1]['source_bbox']==[10,120,25,132]
    assert result['totals']==dict(inventory=4,live=3,print=1,missing=0,excluded=1)


def test_missing_middle_body_term_retains_context_beside_excluded_folio(monkeypatch):
    result,calls=_case(monkeypatch,missing_body=True)
    assert calls==[0]
    assert result['totals']==dict(inventory=3,live=2,print=1,missing=0,excluded=1)
    assert result['pages'][0]['rows'][2]['disposition']=='print'


def test_native_conversion_carries_assembled_furniture_to_final_audit(tmp_path):
    with pymupdf.open()as doc:
        page=doc.new_page(width=300,height=400);page.insert_text((20,80),'Seven boats cross the quiet inlet.',fontsize=12);page.insert_text((140,380),'17',fontsize=12)
        raw=extract.read_pages(doc,[0])[0];lines=[line for block in raw.blocks if block.kind=='text'for line in block.lines]
        foot=next(line for line in lines if line.text=='17');prose=[line for line in lines if line is not foot]
        regions=[skeleton.Region('body',prose,bbox=prose[0].bbox),skeleton.Region('furniture',[foot],bbox=foot.bbox,reason='folio')]
        book=assemble.assemble([skeleton.PageSkeleton(0,300,400,regions)],skeleton.BookStyle(12),[raw])
        assert book.furniture==['17']
        assert all(e.text!='17'for e in book.pages[0])
        reader=SyntheticReader();out=tmp_path/'native-furniture.epub'
        result=vp.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
    assert reader.calls==0
    assert result['native_pages']==[0]
    audit=result['publication_coverage']
    assert audit['totals']==dict(inventory=6,live=6,print=0,missing=0,excluded=1)
    footer=next(r for r in audit['pages'][0]['rows']if r['source_text']=='17')
    assert footer['disposition']=='excluded'
    assert footer['rule']=='source_inventory_furniture'
    assert not any(m.get('block')=='publication-coverage'for m in result['images'].values())


def test_current_published_body_claim_outweighs_old_furniture_ownership(monkeypatch):
    result,calls=_case(monkeypatch,change='live_conflict')
    assert calls==[]
    assert result['totals']==dict(inventory=4,live=4,print=0,missing=0,excluded=0)
    assert result['pages'][0]['rows'][-1]['disposition']=='live'


@pytest.mark.parametrize('normalized',[False,True])
def test_rotated_native_inventory_matches_the_final_reading_frame(monkeypatch,normalized):
    from dataclasses import replace
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open()as doc:
        page=doc.new_page(width=300,height=400)
        page.insert_text((20,80),'Seven boats cross the quiet inlet.',fontsize=12)
        page.insert_text((140,380),'17',fontsize=12);page.set_rotation(90)
        display=SourceDisplay(doc,0);raw=extract.read_pages(doc,[0])[0]
        lines=[line for block in raw.blocks if block.kind=='text'for line in block.lines]
        if normalized:
            lines=[replace(line,bbox=tuple(display.reading_rect(line.bbox)),
                spans=[replace(span,bbox=tuple(display.reading_rect(span.bbox)))for span in line.spans])for line in lines]
            raw=extract.RawPage(0,400,300,[extract.Block(0,(0,0,400,300),lines)],source_geometry=dict(
                space='reading',orientation=display.angle,source_rotation=page.rotation,page_rect=list(page.rect)))
        foot=next(line for line in lines if line.text=='17');prose=[line for line in lines if line is not foot]
        regions=[skeleton.Region('body',prose,bbox=prose[0].bbox),skeleton.Region('furniture',[foot],bbox=foot.bbox,reason='folio')]
        book=assemble.assemble([skeleton.PageSkeleton(0,400,300,regions)],skeleton.BookStyle(12),[raw])
        frame=[dict(text=w[4],bbox=list(display.reading_rect(w[:4])))for w in page.get_text('words')]
        body=[word for word in frame if word['text']!='17']
        records={0:dict(blocks=[dict(id='native-0',role='body',native_source_occurrences=body,
            published_tokens=[word['text']for word in body])])}
        docs={'ch001.xhtml':'<html><body><p data-source-page="0" data-source-block="native-0">Seven boats cross the quiet inlet.</p></body></html>'}
        def unexpected(*args,**kwargs):raise AssertionError('Proved furniture requested a new source image')
        monkeypatch.setattr(unit_source,'source_unit',unexpected)
        result=coverage.final_publication(docs,records,{0:frame},{},SimpleNamespace(images={},aliases={}),
            {0:display},{0:'ch001.xhtml'},source_inventory=book.source_inventory)
    assert result['totals']==dict(inventory=6,live=6,print=0,missing=0,excluded=1)
    footer=next(row for row in result['pages'][0]['rows']if row['source_text']=='17')
    assert footer['disposition']=='excluded'
    assert footer['rule']=='source_inventory_furniture'


def test_generic_furniture_cannot_exclude_an_unpublished_body_clause(monkeypatch):
    result,calls=_case(monkeypatch,furniture='Ordinary body clause',change='unsupported_reason')
    assert calls==[0]
    assert result['totals']==dict(inventory=6,live=3,print=3,missing=0,excluded=0)
    assert all(row['disposition']=='print'for row in result['pages'][0]['rows'][-3:])
