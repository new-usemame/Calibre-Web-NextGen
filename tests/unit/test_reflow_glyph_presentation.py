"""Publication restores clipped connected source ink, without rewriting source."""
import copy,io,json,zipfile
from dataclasses import asdict
from xml.etree import ElementTree as ET
import pymupdf
import pytest
from PIL import Image,ImageDraw
from cps.services.reflow import assemble,extract,skeleton,enriched_source,build_epub,native_text

pytestmark=pytest.mark.unit
X='{http://www.w3.org/1999/xhtml}'

@pytest.fixture
def clipped(tmp_path):
    # Real opaque source bitmap: the left stroke is outside the native text box.
    im=Image.new('1',(200,120),1);draw=ImageDraw.Draw(im)
    draw.rectangle((48,42,55,51),fill=0);draw.rectangle((30,42,37,51),fill=0);draw.rectangle((80,42,87,51),fill=0)
    png=io.BytesIO();im.save(png,format='PNG')
    path=tmp_path/'ink.pdf'
    with pymupdf.open() as d:
        p=d.new_page(width=200,height=120);p.insert_image(p.rect,stream=png.getvalue());d.save(path)
    with pymupdf.open(path) as d:
        raw=extract.RawPage(0,200,120,[extract.Block(0,(30,38,90,55),[extract.Line([
            extract.Span('left ',12,'Times-Roman',0,(30,38,45,55)),
            extract.Span('a',12,'Times-Roman',0,(52,38,58,55),transcription_uncertain=True),
            extract.Span(' right',12,'Times-Roman',0,(70,38,90,55))],(30,38,90,55))])])
        b=assemble.assemble([skeleton.PageSkeleton(0,200,120,[skeleton.Region('body',raw.blocks[0].lines,bbox=raw.blocks[0].bbox)])],skeleton.BookStyle(12),[raw])
        b.source_fingerprint=extract.document_fingerprint(d)
        s=enriched_source.prepare_source_page(b,0,{'layer':'native'})
        yield b,d,s,raw,tmp_path,im


def test_written_main_glyph_contains_complete_source_ink_and_preserves_locked_resource(clipped):
    b,d,s,r,t,im=clipped;before=(s.identity,s.html,asdict(r),copy.deepcopy(b.conservation.to_dict()))
    built=build_epub.build(b,str(t/'ink.epub'),doc=d,source_pages={0:s},raw_pages={0:r})
    assert build_epub.validate(built.path)==[]
    with zipfile.ZipFile(built.path) as z:
        roots=[ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')]
        display=[n for root in roots for n in root.iter(X+'span') if n.get('class')=='source-glyph-display']
        visible=(display[0].find('.//'+X+'img') if display else next(n for root in roots for n in root.iter(X+'a') if n.get('class')=='source-glyph').find(X+'img'))
        src=visible.get('src');published=Image.open(io.BytesIO(z.read('OEBPS/'+src))).convert('L')
        # Count known actual ink, not a producer's bbox or source string.
        assert sum(v==0 for v in published.tobytes())==80,'all printed stroke pixels must survive'
        assert len(display)==1,'main glyph must have a source-bound complete-ink display'
        run=next(x for e in b.pages[0] for x in e.runs if x[0]=='glyph');old=native_text.image_name(run[2])
        original=Image.open(io.BytesIO(z.read('OEBPS/'+old.replace('.jpg','.png')))).convert('L')
        assert sum(v==0 for v in original.tobytes())==40,'locked clipped resource must remain unchanged'
        report=json.loads(z.read('META-INF/reflow.json'));assert report['conservation']==before[3]
        assert report['source_enrichment']['0']['identity']==s.identity
    assert (s.identity,s.html,asdict(r),b.conservation.to_dict())==before


@pytest.mark.parametrize('damage',['neighbor','owner','raw','nonbitonal','pdf_binding'])
def test_unsupported_source_ink_ownership_stays_original_with_reason(clipped,damage):
    from cps.services.reflow import glyph_presentation as gp
    b,d,s,r,t,im=clipped
    class Sink:
        def __init__(self):self.images={}
        def image(self,name,data):self.images[name]=data
    sink=Sink()
    if damage=='raw':
        changed=copy.deepcopy(r);changed.blocks[0].lines[0].spans[1].text='changed'
        with pytest.raises(ValueError):gp.render(b,d,s,changed,s.html,sink)
        return
    if damage=='neighbor':r.blocks[0].lines[0].spans[0].bbox=(30,38,54,55)
    elif damage=='pdf_binding':
        b.source_fingerprint='0'*64;s=enriched_source.prepare_source_page(b,0,{'layer':'native'})
    elif damage=='nonbitonal':
        d[0].draw_rect(pymupdf.Rect(47,41,57,53),color=(1,0,0))
        path=t/'colored.pdf';d.save(path)
        with pymupdf.open(path) as colored:
            b.source_fingerprint=extract.document_fingerprint(colored)
            s=enriched_source.prepare_source_page(b,0,{'layer':'native'})
            out,audit=gp.render(b,colored,s,r,s.html,sink)
        assert out==s.html and not sink.images
        assert all(x['reason']=='native_bitonal_visible_page_unproved' for x in audit['entries'])
        return
    if damage in ('neighbor','owner'):
        regions=[skeleton.Region('body',r.blocks[0].lines,bbox=r.blocks[0].bbox)]
        if damage=='owner':regions.append(skeleton.Region('body',r.blocks[0].lines,bbox=r.blocks[0].bbox))
        b=assemble.assemble([skeleton.PageSkeleton(0,200,120,regions)],skeleton.BookStyle(12),[r]);b.source_fingerprint=extract.document_fingerprint(d);s=enriched_source.prepare_source_page(b,0,{'layer':'native'})
    out,audit=gp.render(b,d,s,r,s.html,sink)
    assert not sink.images
    assert out==s.html and audit['entries'] and not any(x['displayed'] for x in audit['entries'])
    assert all(x.get('reason') for x in audit['entries'])


def test_unrepresentable_canonical_characters_refuse_projection_without_aborting_publication(clipped):
    """Existing control-codepoint evidence must survive an optional glyph refusal.

    Breaks if the projection parses canonical source unconditionally or cleans
    source bytes to manufacture projection authority. Corpus trigger: canonical
    Book568:169 retains VT/FF, while ordinary publication has explicit evidence.
    """
    b,d,s,r,t,im=clipped
    r.blocks[0].lines[0].spans[0].text='left \x0b-\x0c '
    b=assemble.assemble([skeleton.PageSkeleton(0,200,120,[skeleton.Region('body',r.blocks[0].lines,bbox=r.blocks[0].bbox)])],skeleton.BookStyle(12),[r])
    b.source_fingerprint=extract.document_fingerprint(d)
    s=enriched_source.prepare_source_page(b,0,{'layer':'native'})
    before=(s.identity,s.html,asdict(r),copy.deepcopy(b.conservation.to_dict()))
    built=build_epub.build(b,str(t/'unrepresentable.epub'),doc=d,source_pages={0:s},raw_pages={0:r})
    assert build_epub.validate(built.path)==[]
    with zipfile.ZipFile(built.path) as z:
        report=json.loads(z.read('META-INF/reflow.json'))
        audit=next(v['glyph_presentation'] for v in report['source_evidence'])
        assert audit['entries'] and all(not e['displayed'] and e['reason']=='canonical_source_markup_unparseable' for e in audit['entries'])
        assert report['unrepresentable_characters'],'existing source-codepoint disclosures must remain'
        roots=[ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')]
        assert not any(e.get('class')=='source-glyph-display' for root in roots for e in root.iter())
        run=next(x for e in b.pages[0] for x in e.runs if x[0]=='glyph')
        old=native_text.image_name(run[2]).replace('.jpg','.png')
        original=Image.open(io.BytesIO(z.read('OEBPS/'+old))).convert('L')
        assert sum(v==0 for v in original.tobytes())==40
    assert (s.identity,s.html,asdict(r),b.conservation.to_dict())==before


@pytest.fixture
def detached_punctuation(tmp_path):
    """A colored scanned page whose native word box ends before its full stop."""
    im=Image.new('RGB',(200,120),(212,206,198));draw=ImageDraw.Draw(im)
    draw.rectangle((52,42,55,51),fill=(80,76,70))
    draw.rectangle((60,49,61,50),fill=(80,76,70))
    draw.rectangle((30,42,37,51),fill=(80,76,70))
    draw.rectangle((80,42,87,51),fill=(80,76,70))
    png=io.BytesIO();im.save(png,format='PNG');path=tmp_path/'punctuation.pdf'
    with pymupdf.open() as d:
        p=d.new_page(width=200,height=120);p.insert_image(p.rect,stream=png.getvalue());d.save(path)
    with pymupdf.open(path) as d:
        raw=extract.RawPage(0,200,120,[extract.Block(0,(30,38,90,55),[extract.Line([
            extract.Span('left ',12,'Times-Roman',0,(30,38,45,55),origin_y=52),
            extract.Span('word.',12,'Times-Roman',0,(52,38,58,55),origin_y=52,transcription_uncertain=True),
            extract.Span(' right',12,'Times-Roman',0,(70,38,90,55),origin_y=52)],(30,38,90,55))])])
        raw.images=[extract.Image((0,0,200,120),1.0)]
        b=assemble.assemble([skeleton.PageSkeleton(0,200,120,[skeleton.Region('body',raw.blocks[0].lines,bbox=raw.blocks[0].bbox)])],skeleton.BookStyle(12),[raw])
        b.source_fingerprint=extract.document_fingerprint(d);s=enriched_source.prepare_source_page(b,0,{'layer':'native'})
        yield b,d,s,raw,tmp_path


def _dark_components(image):
    pixels=image.convert('L');pending={(x,y) for y in range(pixels.height) for x in range(pixels.width) if pixels.getpixel((x,y))<128};groups=[]
    while pending:
        first=pending.pop();group={first};stack=[first]
        while stack:
            x,y=stack.pop()
            for point in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if point in pending:pending.remove(point);group.add(point);stack.append(point)
        groups.append(group)
    return groups


def test_written_uncertain_scan_word_retains_detached_terminal_punctuation(detached_punctuation):
    b,d,s,r,t=detached_punctuation;before=(s.identity,s.html,asdict(r),copy.deepcopy(b.conservation.to_dict()))
    built=build_epub.build(b,str(t/'punctuation.epub'),doc=d,source_pages={0:s},raw_pages={0:r})
    assert build_epub.validate(built.path)==[]
    with zipfile.ZipFile(built.path) as z:
        roots=[ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')]
        display=[n for root in roots for n in root.iter(X+'span') if n.get('class')=='source-glyph-display']
        visible=(display[0].find('.//'+X+'img') if display else next(n for root in roots for n in root.iter(X+'a') if n.get('class')=='source-glyph').find(X+'img'))
        main=Image.open(io.BytesIO(z.read('OEBPS/'+visible.get('src'))))
        assert len(_dark_components(main))==2,'both the word and its detached printed period must remain visible'
        assert len(display)==1
        assert main.format=='PNG','the display must preserve the rendered color samples losslessly'
        run=next(x for e in b.pages[0] for x in e.runs if x[0]=='glyph');old=native_text.image_name(run[2])
        original=Image.open(io.BytesIO(z.read('OEBPS/'+old)))
        assert len(_dark_components(original))==1,'the sealed clipped witness stays unchanged'
        report=json.loads(z.read('META-INF/reflow.json'));assert report['conservation']==before[3]
    assert (s.identity,s.html,asdict(r),b.conservation.to_dict())==before


@pytest.mark.parametrize('damage',['no_period','punctuation_uncertain','missing_neighbor','neighbor','above',
                                  'rotation','non_scan','source_geometry','binding','dirty','absent_dot','edge_ink'])
def test_ambiguous_scan_word_gap_withholds_supplemental_punctuation(detached_punctuation,damage):
    from cps.services.reflow import glyph_presentation as gp
    b,d,s,r,t=detached_punctuation
    spans=r.blocks[0].lines[0].spans
    if damage=='no_period':spans[1].text='word'
    elif damage=='punctuation_uncertain':spans[1].punctuation_uncertain=True
    elif damage=='missing_neighbor':spans[2].text=' '
    elif damage=='neighbor':spans[2].bbox=(59,38,90,55)
    elif damage=='above':
        r.blocks[0].lines.append(extract.Line([extract.Span('above',12,'Times-Roman',0,(50,30,60,38))],(50,30,60,38)))
    elif damage=='rotation':d[0].set_rotation(90)
    elif damage=='non_scan':r.images=[]
    elif damage=='source_geometry':r.source_geometry={'uncertain':True}
    elif damage=='binding':b.source_fingerprint='0'*64
    elif damage=='dirty':d[0].draw_line((2,2),(8,2))
    elif damage in ('absent_dot','edge_ink'):
        # Replace the actual scan, not its retained transcription, to test
        # missing source ink and unrelated ink reaching the word-gap boundary.
        im=Image.new('RGB',(200,120),(212,206,198));draw=ImageDraw.Draw(im)
        draw.rectangle((52,42,55,51),fill=(80,76,70))
        if damage=='edge_ink':draw.rectangle((60,49,66,50),fill=(80,76,70))
        png=io.BytesIO();im.save(png,format='PNG');path=t/(damage+'.pdf')
        with pymupdf.open() as replacement:
            page=replacement.new_page(width=200,height=120);page.insert_image(page.rect,stream=png.getvalue());replacement.save(path)
        d=pymupdf.open(path);b.source_fingerprint=extract.document_fingerprint(d)
    if damage in ('no_period','punctuation_uncertain','missing_neighbor','neighbor','above','non_scan','source_geometry'):
        b=assemble.assemble([skeleton.PageSkeleton(0,200,120,[skeleton.Region('body',r.blocks[0].lines,bbox=r.blocks[0].bbox)])],skeleton.BookStyle(12),[r]);b.source_fingerprint=extract.document_fingerprint(d)
    s=enriched_source.prepare_source_page(b,0,{'layer':'native'})
    before=(s.identity,s.html,asdict(r),copy.deepcopy(b.conservation.to_dict()))
    class Sink:
        def __init__(self):self.images={}
        def image(self,name,data):self.images[name]=data
    sink=Sink();html,audit=gp.render(b,d,s,r,s.html,sink)
    assert html==s.html and not sink.images
    assert audit['entries'] and all(not entry['displayed'] and entry.get('reason') for entry in audit['entries'])
    assert (s.identity,s.html,asdict(r),b.conservation.to_dict())==before
    if damage in ('absent_dot','edge_ink'):d.close()
