"""Publication metrics and detail access keep source authority at written seams."""
import copy, json, zipfile
from dataclasses import asdict, replace
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import assemble, skeleton, enriched_source, build_epub
from tests.unit.test_reflow_glyph_presentation import clipped, X

pytestmark=pytest.mark.unit


def metric_source(fixture, origin=52):
    b,d,s,r,t,im=fixture
    for line in r.blocks[0].lines:
        line.spans=[replace(sp,origin_y=origin) for sp in line.spans]
    b=assemble.assemble([skeleton.PageSkeleton(0,200,120,[skeleton.Region('body',r.blocks[0].lines,bbox=r.blocks[0].bbox)])],skeleton.BookStyle(12),[r])
    from cps.services.reflow import extract
    b.source_fingerprint=extract.document_fingerprint(d)
    return b,d,enriched_source.prepare_source_page(b,0,{'layer':'native'}),r,t,im


def test_complete_native_crop_receives_source_metrics_without_ink_repair(clipped):
    """A complete crop must not fall back to the generic enlarged image height."""
    fixture=list(clipped);raw=fixture[3]
    line=raw.blocks[0].lines[0]
    line.spans[1]=replace(line.spans[1],bbox=(47,38,58,55))
    b,d,s,r,t,im=metric_source(fixture)
    before=(s.identity,s.html,b.conservation.to_dict())
    built=build_epub.build(b,str(t/'complete-metric.epub'),doc=d,source_pages={0:s},raw_pages={0:r})
    with zipfile.ZipFile(built.path) as archive:
        payload=json.loads(archive.read('META-INF/reflow.json'))
        audit=next(e['glyph_presentation'] for e in payload['source_evidence'] if 'glyph_presentation' in e)
        row=next(e for e in audit['entries'])
        assert row['displayed'] and row['presentation']['applied'],row
        assert row['proof']['native_seed_box']==row['proof']['native_display_box']
        assert row['presentation']['height_em']==pytest.approx(17/12)
        from PIL import Image
        import io
        image=Image.open(io.BytesIO(archive.read('OEBPS/'+row['display_resource']))).convert('L')
        assert sum(v==0 for v in image.tobytes())==80
        original=Image.open(io.BytesIO(archive.read('OEBPS/'+row['source_resource']))).convert('L')
        assert image.size==original.size and image.tobytes()==original.tobytes()
    assert before==(s.identity,s.html,b.conservation.to_dict())


@pytest.mark.parametrize('origin',[52,0,90])
def test_written_metric_projection_uses_retained_baseline_or_explicit_fallback(clipped,origin):
    b,d,s,r,t,im=metric_source(clipped,origin)
    before=(s.identity,s.html,copy.deepcopy(b.conservation.to_dict()))
    built=build_epub.build(b,str(t/'metric.epub'),doc=d,source_pages={0:s},raw_pages={0:r})
    assert build_epub.validate(built.path)==[]
    with zipfile.ZipFile(built.path) as z:
        payload=json.loads(z.read('META-INF/reflow.json'))
        audit=next(e['glyph_presentation'] for e in payload['source_evidence'] if 'glyph_presentation' in e)
        row=next(e for e in audit['entries'] if e['displayed'])
        metrics=row['presentation']
        if origin==52:
            assert metrics['applied']
            box=row['proof']['reading_bbox']
            assert metrics['height_em']==pytest.approx((box[3]-box[1])/12)
            assert metrics['baseline_offset_em']==pytest.approx((52-box[3])/12)
            assert metrics['source_baseline']==52 and metrics['source_size']==12
        else:
            assert not metrics['applied'] and metrics['reason']
        assert payload['conservation']==before[2]
        roots=[ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')]
        retained=next(n for root in roots for n in root.iter(X+'span') if n.get('class')=='source-glyph-original')
        canonical=ET.fromstring('<root>'+s.html+'</root>').find('.//a[@class="source-glyph"]')
        node=retained.find(X+'a')
        assert node.attrib==canonical.attrib
        attrs=canonical.find('img').attrib.copy()
        # The established package aliases lossless PNGs without changing pixels.
        attrs['src']=attrs['src'].replace('.jpg','.png')
        assert node.find(X+'img').attrib==attrs
    assert before==(s.identity,s.html,b.conservation.to_dict())


def test_detail_landing_offers_bound_larger_crops_before_raster_and_sequential_returns():
    """A figure jump must expose detail access before its unreadable full raster."""
    details=[dict(id='figure_0',label='Original figure',src='full.png',reading_bbox=[0,0,500,700],inspection_ids=['inspection_0','inspection_1'])]
    details += [dict(id='inspection_'+str(i),label='Original detail '+str(i+1)+' (row order)',src='tile'+str(i)+'.png',reading_bbox=[0,i*300,240,i*300+320]) for i in range(2)]
    record=dict(page=0,full='page.png',details=details)
    before=copy.deepcopy(record)
    doc=ET.fromstring(build_epub._original_document(record,'ch001.xhtml','en',return_target='ch001.xhtml#source_return_0000'))
    for detail in details:
        section=next(n for n in doc.iter(X+'section') if any(h.get('id')==detail['id'] for h in n.iter(X+'h2')))
        image=next(n for n in section if n.tag==X+'img');items=list(section)
        if detail['id']=='figure_0':
            controls=[n for n in section if n.tag==X+'nav']
            assert controls and items.index(controls[0])<items.index(image)
            assert {a.get('href') for a in controls[0].iter(X+'a')}=={'#inspection_0','#inspection_1'}
            assert all(d['reading_bbox'][2]-d['reading_bbox'][0]<500 for d in details[1:])
        else:
            neighbor='#inspection_'+str(1 if detail['id']=='inspection_0' else 0)
            assert any(a.get('href')==neighbor for a in section.iter(X+'a'))
        assert any(a.get('href')=='ch001.xhtml#source_return_0000' for a in section.iter(X+'a'))
        assert not any(list(a.iter(X+'a'))[1:] for a in section.iter(X+'a'))
        assert image.get('src')==detail['src']
    assert record==before
