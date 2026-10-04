"""Complete mixed text/pixel tables are owned once, with inspectable source pixels."""
import copy
import json
import zipfile
from dataclasses import asdict

import pymupdf
import pytest
from cps.services.reflow import assemble, build_epub, enriched_source, extract, skeleton, source_inventory, visual_coverage, visual_objects

pytestmark = pytest.mark.unit

@pytest.fixture
def table(tmp_path):
    path = tmp_path / 'table.pdf'
    with pymupdf.open() as doc:
        p = doc.new_page(width=400, height=300)
        p.insert_text((60, 100), 'Fixed       Mutable')
        p.insert_text((60, 130), 'Leo         Virgo')
        p.draw_rect((50, 80, 250, 145))
        p.insert_text((60, 165), 'Table 1: Modes')
        p.insert_text((60, 210), 'Following prose.')
        doc.save(path)
    with pymupdf.open(path) as doc:
        def line(text, box): return extract.Line([extract.Span(text, 10, 'Native', 0, box)], box)
        lines = [line('Fixed Mutable', (60,90,230,102)), line('Leo Virgo', (60,120,230,132)),
                 line('Table 1: Modes', (60,154,220,168)), line('Following prose.', (60,199,240,213))]
        raw = extract.RawPage(0,400,300,blocks=[extract.Block(i,l.bbox,[l]) for i,l in enumerate(lines)])
        skel = skeleton.PageSkeleton(0,400,300,regions=[
            skeleton.Region('figure',bbox=(50,80,245,106),reason='ocr_uncertain_region'),
            skeleton.Region('artwork',lines=[lines[0]],bbox=lines[0].bbox,reason='ocr_uncertain_region'),
            skeleton.Region('body',lines=[lines[1]],bbox=lines[1].bbox),
            skeleton.Region('caption',lines=[lines[2]],bbox=lines[2].bbox),
            skeleton.Region('body',lines=[lines[3]],bbox=lines[3].bbox)])
        yield doc,raw,skel

def proposal(doc,raw):
    _, binding = visual_coverage.render_input(doc,raw,version='local-visual-regions-1')
    return dict(binding,detections=[dict(label='table',confidence=.97,bbox=[75,120,375,218]),
        dict(label='figure_title',confidence=.9,bbox=[90,231,330,252])])

def admit(doc,raw,skel):
    visual_objects.append_regions(raw,skel,visual_objects.prepare(doc,raw,proposal(doc,raw)))
    skel.regions.sort(key=skeleton._region_order)

@pytest.mark.parametrize('native',[False,True])
def test_whole_table_preserves_raw_ownership_caption_and_publication(table,tmp_path,native):
    doc,raw,skel=table
    before=assemble.assemble([skel],skeleton.book_style([raw]),[raw])
    old=enriched_source.prepare_source_page(before,0,{'layer':'native'})
    old_inventory=copy.deepcopy(before.source_inventory[0])
    admit(doc,raw,skel)
    book=assemble.assemble([skel],skeleton.book_style([raw]),[raw])
    source=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    with pytest.raises(ValueError,match='stale'):old.validate(book)
    inv=book.source_inventory[0];source_inventory.validate(inv,raw)
    assert inv['lines']==old_inventory['lines']
    assert [x['representation'] for x in inv['ownership']]==['protected_image','protected_image','text','text']
    assert len(inv['assets'])==1 and not inv['unresolved']
    assert visual_coverage._contains(inv['assets'][0]['bbox'], (50,80,250,145))
    assert book.figures[0]['visual_evidence']['pixel_boundary']['minimum_gray'] >= 200
    assert 'Leo Virgo' not in source.html and source.html.count('Table 1: Modes')==1
    assert 'Following prose.' in source.html and 'Original table' in source.html
    target=tmp_path/'table.epub'
    if native:
        from cps.services.reflow.native_ipc import NativeDocument
        with NativeDocument(doc.name,scratch_root=tmp_path/'native') as worker:
            build_epub.build(book,str(target),doc=worker,source_pages={0:source})
    else:
        build_epub.build(book,str(target),doc=doc,source_pages={0:source})
    assert not build_epub.validate(target)
    with zipfile.ZipFile(target) as z:
        html=b''.join(z.read(n) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')).decode()
        detail=z.read('OEBPS/original-p0000.xhtml').decode()
        assert html.count('Table 1: Modes')==1 and 'original-p0000.xhtml#figure_0' in html
        assert 'id="figure_0"' in detail and 'Original figure' in detail
        assert any('original_p0000_figure_0' in n for n in z.namelist())

@pytest.mark.parametrize('damage',['caption','partial_line','partial_region','duplicate','metadata','foreign_figure','raster','unissued','raw_changed'])
def test_table_admission_rejects_loss_or_stale_evidence_atomically(table,damage):
    doc,raw,skel=table
    record=proposal(doc,raw)
    if damage=='caption':record['detections'][0]['bbox'][3]=252
    elif damage=='partial_line':raw.blocks[1].lines[0].bbox=(60,120,270,132)
    elif damage=='partial_region':skel.regions[2].bbox=(60,120,270,132)
    elif damage=='duplicate':skel.regions.append(copy.copy(skel.regions[2]))
    elif damage=='metadata':skel.regions[2].display_group={'type':'title'}
    elif damage=='foreign_figure':skel.regions.append(skeleton.Region('figure',bbox=(249,140,280,190),reason='scan_figure_band'))
    elif damage=='raster':record['image_sha256']='0'*64
    before=asdict(skel)
    with pytest.raises(ValueError):
        obs=visual_objects.prepare(doc,raw,record)
        if damage=='unissued':obs=copy.deepcopy(obs)
        elif damage=='raw_changed':
            raw.blocks[1].lines[0].spans[0].text='Rewritten'
            before=asdict(skel)
        visual_objects.append_regions(raw,skel,obs)
    assert asdict(skel)==before

def test_uncertain_page_figures_have_specific_detail_targets(table,tmp_path):
    doc,raw,skel=table
    book=assemble.assemble([skel],skeleton.book_style([raw]),[raw])
    source=enriched_source.prepare_source_page(book,0,{'layer':'ocr'})
    target=tmp_path/'uncertain.epub'
    build_epub.build(book,str(target),doc=doc,source_pages={0:source})
    with zipfile.ZipFile(target) as z:
        detail=z.read('OEBPS/original-p0000.xhtml').decode()
        html=b''.join(z.read(n) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')).decode()
        assert 'id="figure_0"' in detail
        assert 'original-p0000.xhtml#figure_0' in html

@pytest.mark.parametrize('failure',['omitted','crop_failure','changed_bounds','missing_figure'])
def test_table_primary_pixels_are_mandatory(table,tmp_path,monkeypatch,failure):
    doc,raw,skel=table
    admit(doc,raw,skel)
    book=assemble.assemble([skel],skeleton.book_style([raw]),[raw])
    source=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    fragment=source.html
    if failure=='omitted':
        import re
        fragment=re.sub(r'<figure\b.*?</figure>','',fragment,flags=re.S)
    elif failure=='crop_failure':
        def fail(*args,**kwargs):raise ValueError('cannot crop')
        monkeypatch.setattr(extract,'crop_jpeg',fail)
    elif failure=='changed_bounds':book.figures[0]['bbox'][2]-=20
    else:book.figures.clear()
    target=tmp_path/'guard.epub'
    if failure=='omitted':
        # Direct untrusted markup is already repaired by the canonical gate.
        build_epub.build(book,str(target),doc=doc,page_html={0:fragment})
        with zipfile.ZipFile(target) as z:
            assert any(n.startswith('OEBPS/images/fig_') for n in z.namelist())
    else:
        with pytest.raises(ValueError):
            build_epub.build(book,str(target),doc=doc,page_html={0:fragment})

def test_table_boundary_refuses_continuing_source_ink(table):
    doc,raw,skel=table
    # A source line crossing the detector's edge cannot be closed within the
    # bounded margin. It must not be cut just because a detector said "table".
    doc[0].draw_line((220,135),(310,135),width=2)
    doc.saveIncr()
    with pymupdf.open(doc.name) as clean:
        with pytest.raises(ValueError,match='boundary crosses source ink'):
            visual_objects.prepare(clean,raw,proposal(clean,raw))
