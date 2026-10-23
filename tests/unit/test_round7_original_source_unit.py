"""Independent SOURCE primitive controls with original corner ink and aliases.
The primitive receives a source-owned unit; it cannot invent a transcription,
printed note origin, or replace unknown content with an empty source image.
"""
import io
import copy
import re
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pytest
from PIL import Image, ImageDraw
from cps.services.reflow import unit_source

NS = {'x':'http://www.w3.org/1999/xhtml'}


def original():
    image = Image.new('RGB', (160, 240), 'white')
    draw = ImageDraw.Draw(image)
    for box in ((2,2,14,14),(145,2,157,14),(2,225,14,237),(145,225,157,237)):
        draw.rectangle(box, fill=(0,0,255))
    draw.rectangle((50,90,110,130), fill='black')
    stream = io.BytesIO();image.save(stream, 'JPEG', quality=90)
    return stream.getvalue()


class Display:
    rect = (0,0,80,120)
    provenance = {'original_frame':{'original_page_index0':37,'image_sha256':'synthetic-original-frame-pin'}}
    def __init__(self):self.calls=[]
    def roi_jpeg(self, box, **options):
        self.calls.append((tuple(box), options))
        return original()


class TrustedPackage:
    def __init__(self):self.images={};self.aliases={};self.source_calls=[]
    def image(self, *args):raise AssertionError('trusted source assets must not use ordinary re-encoded image path')
    def source_image(self, src, data):
        self.source_calls.append((src,data))
        actual=src[:-4]+'.png'
        stream=io.BytesIO();Image.open(io.BytesIO(data)).save(stream,'PNG')
        self.images[actual]=stream.getvalue();self.aliases[src]=actual


@pytest.mark.parametrize('role', ['page','body','caption','figure','table','footnote'])
def test_original_whole_extent_target_and_zero_live_credit(role):
    display,package,images=Display(),TrustedPackage(),{}
    fragment,asides,report=unit_source.source_unit(display,4,'suspect-unit',role,package,images,'Unread <ambiguous> glyph',bbox=[30,40,55,70])
    assert display.calls == [((0,0,80,120),{'scale':2,'quality':30,'optimize':True})], 'hint cannot truncate original source extent'
    assert report['source_image'] in package.images and report['source_image'] in images
    pixels=Image.open(io.BytesIO(package.images[report['source_image']])).convert('RGB')
    for x,y in ((6,6),(151,6),(6,231),(151,231)):
        red,green,blue=pixels.getpixel((x,y));assert blue>130 and red<100 and green<100, 'all original corners retained'
    assert report['source_bbox']==[0,0,80,120] and report['unit_bbox']==[30,40,55,70]
    assert report['status']=='DEGRADED_SOURCE_CONTEXT' and report['live_words']==report['words']==0
    assert report['original_frame']==display.provenance['original_frame'] and report['original_page_index0']==37
    block=report['blocks'][0]
    assert block['published_tokens']==[] and block['source_regions']==[]
    assert block['printed_head'] is None and block['printed_tail'] is None
    assert not block['continues_previous'] and not block['continues_next'] and not block['source_bound_art']
    root=ET.fromstring('<body xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'+fragment+''.join(asides)+'</body>')
    retained=root.findall('.//x:img',NS)
    assert len(retained)==1 and retained[0].get('src')==report['source_image']
    assert 'Unread <ambiguous> glyph' in ''.join(root.itertext()), 'reason must remain literal escaped text'
    ids={n.get('id') for n in root.iter() if n.get('id')}
    for anchor in root.findall('.//x:a',NS):
        assert anchor.get('href','').startswith('#') and anchor.get('href')[1:] in ids
    if role=='footnote':
        assert fragment=='' and len(asides)==1
        aside=root.find('x:aside',NS)
        assert aside.get('id')=='v4-note-4-suspect-unit' and aside[0].tag.endswith('}p')
        assert not root.findall('.//x:a',NS), 'unreferenced note cannot fabricate an origin or Return'
        assert report['source_reference_origin'] is None
    else:
        links=root.findall('.//x:a',NS)
        assert any(a.get('class')=='source-disclosure-ref' for a in links)
        assert any('Return'==''.join(a.itertext()) for a in links)


@pytest.mark.parametrize('failure', ['outside','render','store'])
def test_invalid_or_unavailable_source_is_never_replaced_with_blank(failure):
    display,package,images=Display(),TrustedPackage(),{}
    if failure=='render':
        def broken(*a,**k):raise ValueError('authoritative original render failed')
        display.roi_jpeg=broken
    if failure=='store':
        def broken(*a,**k):raise PermissionError('original source destination unavailable')
        package.source_image=broken
    with pytest.raises((ValueError,PermissionError)):
        unit_source.source_unit(display,4,'suspect-unit','body',package,images,'Retain source',bbox=[-1,0,55,70] if failure=='outside' else None)
    assert images=={} and package.images=={}, 'unavailable source cannot acquire fabricated image evidence'
