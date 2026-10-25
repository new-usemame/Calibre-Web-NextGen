"""Synthetic vector and scanned line previews preserve gray ink and descent."""
import io
import pymupdf
import pytest
from PIL import Image
from cps.services.reflow import inline_print
from cps.services.reflow.source_display import SourceDisplay

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('scanned', [False, True])
def test_inline_word_uses_printed_line_scale_instead_of_paper_box(scanned):
    with pymupdf.open() as doc, pymupdf.open() as scan:
        p=doc.new_page(width=300,height=150)
        p.insert_text((30,70),'The garden grows green.',fontsize=16)
        word=next(w for w in p.get_text('words')if w[4]=='garden')
        if scanned:
            page=scan.new_page(width=300,height=150)
            page.insert_image(page.rect,pixmap=p.get_pixmap(matrix=pymupdf.Matrix(3,3)))
        display=SourceDisplay(scan if scanned else doc,0)
        value=inline_print.preview(display,list(word[:4]))
    assert value is not None
    data,metric=value
    image=Image.open(io.BytesIO(data)).convert('L')
    assert data.startswith(b'\x89PNG')
    assert metric['vertical_align_em']<0
    assert metric['height_em']>1
    assert len(set(image.tobytes()))>2,'Anti-aliased strokes must not be thresholded away'
    assert metric['bbox'][1]<metric['baseline']<metric['bbox'][3]


def test_multiline_source_box_cannot_supply_single_line_preview_metrics():
    with pymupdf.open() as doc:
        page=doc.new_page(width=350,height=180)
        page.insert_text((30,60),'The garden grows green.',fontsize=16)
        page.insert_text((30,90),'The clear sample remains readable.',fontsize=16)
        words=page.get_text('words')
        box=[min(w[0] for w in words),min(w[1] for w in words),
             max(w[2] for w in words),max(w[3] for w in words)]
        assert inline_print.preview(SourceDisplay(doc,0),box) is None


@pytest.mark.parametrize('case',['refused','dense','sparse','sparse_split_territories','sparse_unplaced','mixed'])
def test_page_publication_uses_a_unit_print_instead_of_a_shrunken_inspection_asset(tmp_path,monkeypatch,case):
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from tests.unit.test_reflow_vision_pilot import answer,block
    printed='The clear garden grows beside the quiet green path in summer.'
    with pymupdf.open()as drawn,pymupdf.open()as scan:
        source=drawn.new_page(width=420,height=160)
        source.insert_text((20,60),printed,fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in source.get_text('words')]
        page=scan.new_page(width=420,height=160);page.insert_image(page.rect,pixmap=source.get_pixmap())
        monkeypatch.setattr(pa,'read_crop',lambda *a,**k:pa.CropWitness(words))
        original=pa.adjudicate
        def uncertain(*a,**kw):
            result=original(*a,**kw)
            indices=range(len(result['tokens']))if case=='dense'else[2,4]if case=='mixed'else[2]
            for i in indices:
                result['marked'].add(i)
                result.setdefault('word_notes',{})[i]=dict(status='UNRESOLVED',
                    source=[result['tokens'][i]],model=[result['tokens'][i]],chosen=[result['tokens'][i]],
                    method='unproved_example',source_range=[i,i+1],model_range=[i,i+1])
            if case=='sparse_unplaced':
                result['boxes'].pop(2,None)
                result['word_notes'][2].update(source=[],method='pixel_read_inconclusive')
            return result
        monkeypatch.setattr(pa,'adjudicate',uncertain)
        if case=='refused':monkeypatch.setattr(inline_print,'preview',lambda *a:None)
        if case=='mixed':
            normal=inline_print.preview
            def refuse_one(display,crop):
                return None if crop[0]<=words[2]['bbox'][0] and crop[2]>=words[2]['bbox'][2] else normal(display,crop)
            monkeypatch.setattr(inline_print,'preview',refuse_one)
        class Package:
            def __init__(self):self.images={}
            def image(self,name,data):self.images[name]=data
        package=Package();assets={}
        territories=[[15,40,410,75]]
        if case=='sparse_split_territories':
            split=(words[5]['bbox'][2]+words[6]['bbox'][0])/2
            territories=[[15,40,split,75],[split,40,410,75]]
        compiled,receipt=vp.compile_page(answer(block(printed)),words,SourceDisplay(scan,0),0,
            package,assets,pixel_words=words,regions={'b1':territories},body_owners={},
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    from lxml import etree
    root=etree.fromstring(('<div xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(compiled['body'])+'</div>').encode())
    flags=root.xpath('.//span[@class="reflow-flagged-reading"]')
    assert flags,'The plausible reading must stay visibly flagged'
    assert receipt['blocks'][0]['published_tokens']==printed.split()
    if case=='mixed':
        assert len(root.xpath('.//span[@class="reflow-flagged-reading"]//img'))==1, 'A refused word may not remove another word’s clean preview'
        assert len(root.xpath('.//span[@class="reflow-plausible"]/a'))==1
        assert len(root.xpath('.//div[@class="reflow-unit-print"]'))==1
        assert len(root.xpath('.//sup[text()="†"]'))==2
    elif case in ('refused','dense'):
        assert not root.xpath('.//span[@class="reflow-flagged-reading"]//img'), 'No broad inspection image may impersonate an inline word'
        unit=root.xpath('.//div[@class="reflow-unit-print"]')
        assert len(unit)==1,'Uncertain unit evidence is displayed once after the paragraph'
        assert root.index(unit[0])>0
        assert len(root.xpath('.//sup[text()="†"]'))==1
        image=unit[0].xpath('.//img')[0]
        assert 'height:1.4em'not in image.get('style','')
        assert image.get('src')in package.images
        complete=etree.fromstring(('<div xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(compiled['body']+compiled['asides'])+'</div>').encode())
        for flag in flags:
            links=flag.xpath('.//span[@class="reflow-plausible"]/a')
            assert len(links)==1 and links[0].get('title')=='Compare this reading with original print'
            target=links[0].get('href')[1:]
            detail=complete.xpath('//*[@id=$target]',target=target)
            assert len(detail)==1 and detail[0].xpath('.//img')
            origins=complete.xpath('//*[@id=$origin]',origin='r-'+target)
            assert len(origins)==1 and origins[0].get('href')=='#'+target
            assert detail[0].xpath('.//a[@href=$origin]',origin='#r-'+target)
        if case=='dense':assert not any(v.get('kind')=='inline_print_preview'for v in assets.values()), 'Unused word previews must not fill the archive after choosing a unit print'
    else:
        assert not root.xpath('.//div[@class="reflow-unit-print"]')
        assert len(root.xpath('.//span[@class="reflow-flagged-reading"]//img'))==1
        assert all(assets[e.get('src')]['kind']=='inline_print_preview'
                   for e in root.xpath('.//span[@class="reflow-flagged-reading"]//img'))


def test_disconnected_capital_crossbar_inside_word_box_survives_line_metrics():
    with pymupdf.open()as doc:
        page=doc.new_page(width=240,height=150)
        # Disconnected top ink is still part of the bounded printed capital.
        page.draw_rect(pymupdf.Rect(40,75,54,76),color=None,fill=(0,0,0))
        page.draw_rect(pymupdf.Rect(46,80,48,95),color=None,fill=(0,0,0))
        for x in range(70,150,10):
            page.draw_rect(pymupdf.Rect(x,84,x+6,95),color=None,fill=(0,0,0))
        value=inline_print.preview(SourceDisplay(doc,0),[39,74,55,96])
    assert value is not None
    image=Image.open(io.BytesIO(value[0])).convert('L')
    components=list(inline_print._components((v<128 for v in image.tobytes()),image.width,image.height))
    bounds=[(max(q%image.width for q in c)-min(q%image.width for q in c)+1,
             max(q//image.width for q in c)-min(q//image.width for q in c)+1)for c in components]
    assert any(width>=5*height for width,height in bounds), 'The disconnected printed crossbar must not be filtered away'


def test_faded_print_has_near_black_stroke_cores_without_erasing_gray_edges():
    with pymupdf.open()as doc:
        pg=doc.new_page(width=300,height=150)
        pg.insert_text((30,70),'The garden grows green.',fontsize=16,color=(.5,.5,.5))
        word=next(w for w in pg.get_text('words')if w[4]=='garden')
        value=inline_print.preview(SourceDisplay(doc,0),list(word[:4]))
    assert value is not None
    image=Image.open(io.BytesIO(value[0])).convert('L')
    ink=sorted(v for v in image.tobytes()if v<200)
    assert ink and ink[len(ink)//5]<60,'Faded core strokes need contrast that survives monochrome dithering'
    assert len(set(image.tobytes()))>2,'Gray edge samples remain instead of a binary redraw'


def test_shared_unit_print_waits_until_the_source_sentence_is_complete(tmp_path,monkeypatch):
    """Generated evidence must not split an independently proved page turn."""
    from tests.unit import test_round2_assembly as fixture
    from tests.unit.test_reflow_vision_pilot import assistance_live_text
    from cps.services.reflow import vision_pipeline as vp,vision_pixels,pixel_adjudication as pa
    from xml.etree import ElementTree as ET
    import zipfile
    left='The clear garden has a stone-'
    right='border beside the quiet stream.'
    pixels=[fixture.optical(left,20,280),fixture.optical(right,20,30)]
    layers=[[dict(w)for w in words]for words in pixels]
    monkeypatch.setattr(vision_pixels,'words',lambda display,cache:pixels[display.page.number])
    monkeypatch.setattr(vp,'layer_words',lambda doc,n,display:layers[n])
    # This is a publication/seam test, so supply one explicitly unsettled
    # word instead of depending on a recognizer version failing on the scan.
    monkeypatch.setattr(pa,'read_crop',lambda display,*args,**kwargs:
        pa.CropWitness([dict(w,confidence=99)for w in pixels[display.page.number]]))
    adjudicate=pa.adjudicate
    def one_unsettled_word(*args,**kwargs):
        result=adjudicate(*args,**kwargs)
        if 'garden'in result['tokens']:
            i=result['tokens'].index('garden')
            result['marked'].add(i)
            result.setdefault('word_notes',{})[i]=dict(status='UNRESOLVED',
                source=['garden'],model=['garden'],chosen=['garden'],
                method='synthetic_unsettled_word',source_range=[i,i+1],model_range=[i,i+1])
        return result
    monkeypatch.setattr(pa,'adjudicate',one_unsettled_word)
    from cps.services.reflow import inline_print
    monkeypatch.setattr(inline_print,'preview',lambda *args,**kwargs:None)
    reader=fixture.Reader([fixture.answer(fixture.block(left,next=True)),
                           fixture.answer(fixture.block(right,bbox=(20,50,480,350),previous=True))])
    path=tmp_path/'continuation.epub'
    with fixture.source(pixels)as doc:vp.convert(doc,path,reader)
    with zipfile.ZipFile(path)as archive:
        roots=[ET.fromstring(archive.read(n))for n in archive.namelist()if n.startswith('OEBPS/ch')and n.endswith('.xhtml')]
    paragraphs=[p for root in roots for p in root.iter()if p.tag.endswith('}p')and any(
        child.get('data-source-block')for child in p.iter())]
    assert [assistance_live_text(p)for p in paragraphs]==[left+right]
    prints=[node for root in roots for node in root.iter()if node.get('class')=='reflow-unit-print']
    assert prints,'The doubtful reading keeps its original printed evidence'
    for root in roots:
        parents={child:parent for parent in root.iter()for child in parent}
        for image in [n for n in root.iter()if n.get('class')=='reflow-unit-print']:
            parent=parents[image];position=list(parent).index(image)
            assert position>0 and parents.get(image)not in paragraphs
            assert list(parent)[position-1]in paragraphs,'The unit print follows the completed live paragraph'


def test_live_text_projection_keeps_named_reading_links_and_anchor_tails():
    from xml.etree import ElementTree as ET
    from tests.unit.test_round4_defects import plain
    node=ET.fromstring('<p xmlns="http://www.w3.org/1999/xhtml">The <span class="reflow-plausible"><a title="Compare this reading with original print" href="#detail">garden</a></span><a href="#detail"><sup>†</sup></a> grows.</p>')
    assert plain(node)=='The garden grows.'


@pytest.mark.parametrize('count',[1,2])
def test_refused_picture_only_words_publish_legible_unit_print_not_inline_inspection(tmp_path,monkeypatch,count):
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from tests.unit.test_reflow_vision_pilot import answer,block
    from lxml import etree
    printed='The clear garden grows beside the quiet green path in summer.'
    with pymupdf.open()as drawn,pymupdf.open()as scan:
        source=drawn.new_page(width=420,height=160)
        source.insert_text((20,60),printed,fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in source.get_text('words')]
        page=scan.new_page(width=420,height=160);page.insert_image(page.rect,pixmap=source.get_pixmap())
        monkeypatch.setattr(pa,'read_crop',lambda *a,**k:pa.CropWitness(words))
        original=pa.adjudicate
        def uncertain(*a,**kw):
            result=original(*a,**kw)
            for i in [2,4][:count]:
                result['marked'].add(i)
                result.setdefault('word_notes',{})[i]=dict(status='UNRESOLVED',
                    source=[result['tokens'][i]],model=[result['tokens'][i]],chosen=[result['tokens'][i]],
                    source_image_only=True,source_bbox=words[i]['bbox'],
                    method='unproved_complete_word_scope',source_range=[i,i+1],model_range=[i,i+1])
            return result
        monkeypatch.setattr(pa,'adjudicate',uncertain)
        monkeypatch.setattr(inline_print,'preview',lambda *a:None)
        class Package:
            def __init__(self):self.images={}
            def image(self,name,data):self.images[name]=data
        package=Package();assets={}
        compiled,receipt=vp.compile_page(answer(block(printed)),words,SourceDisplay(scan,0),0,
            package,assets,pixel_words=words,regions={'b1':[[15,40,410,75]]},body_owners={},
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    root=etree.fromstring(('<div xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(compiled['body'])+'</div>').encode())
    assert not root.xpath('.//p//a[@class="source-word-image"]/img'), 'A declined one-line print must never fall back to a tiny full inspection image'
    prints=root.xpath('.//div[@class="reflow-unit-print"]')
    assert len(prints)==1,'Picture-only evidence is shared once after the unit'
    assert len(prints[0].xpath('.//img'))==count, 'Each unresolved occurrence keeps its own original evidence'
    assert 'height:1.4em'not in prints[0].xpath('.//img')[0].get('style','')
    assert len(root.xpath('.//sup[text()="†"]'))==count
    # Match the actual combined EPUB stylesheet against this published DOM.
    # Moving a picture out of a paragraph does not defeat a more specific
    # inherited one-em word-image rule by itself.
    import tinycss2,cssselect2
    from cps.services.reflow import build_epub
    matcher=cssselect2.Matcher()
    for rule in tinycss2.parse_stylesheet(build_epub.STYLESHEET,skip_comments=True,skip_whitespace=True):
        if rule.type!='qualified-rule':continue
        values={d.lower_name:tinycss2.serialize(d.value).strip()for d in tinycss2.parse_declaration_list(rule.content)if d.type=='declaration'}
        for selector in cssselect2.compile_selector_list(tinycss2.serialize(rule.prelude)):
            matcher.add_selector(selector,values)
    image=prints[0].xpath('.//img')[0]
    wrapped=next(w for w in cssselect2.ElementWrapper.from_xml_root(root).iter_subtree()if w.etree_element is image)
    cascade={}
    for specificity,order,pseudo,values in matcher.match(wrapped):cascade.update(values)
    assert cascade.get('height')=='auto','Shared original print must escape inline word-height CSS'
    assert cascade.get('max-width')=='95%','The block print must stay inside the reading column'
    complete=etree.fromstring(('<div xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(compiled['body']+compiled['asides'])+'</div>').encode())
    for image in complete.xpath('.//img'):
        assert image.get('src')in package.images
    for control in complete.xpath('.//a[starts-with(@href,"#")]'):
        assert len(complete.xpath('//*[@id=$target]',target=control.get('href')[1:]))==1
    assert receipt['blocks'][0]['published_tokens']==printed.split()
    assert len(receipt['blocks'][0]['source_image_words'])==count
    assert not root.xpath('.//span[@class="reflow-flagged-reading"]')
    assert len(compiled['asides'])==count,'Moving a print adds no redundant source disclosure'


@pytest.mark.parametrize('tag',['em','strong'])
def test_formatted_opaque_word_cannot_hide_an_inline_inspection_fallback(tmp_path,monkeypatch,tag):
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa
    from lxml import etree
    with pymupdf.open()as doc:
        page=doc.new_page(width=300,height=140)
        page.insert_text((25,60),'Garden',fontsize=12)
        page.insert_text((80,60),'w',fontname='cour',fontsize=12)
        page.insert_text((105,60),'remains readable.',fontsize=12)
        font=next(f[0]for f in page.get_fonts()if f[3]=='Courier')
        doc.xref_set_key(font,'BaseFont','/CustomMarks');doc.reload_page(page)
        monkeypatch.setattr(pa,'read_crop',lambda *a,**k:[])
        class Package:
            def __init__(self):self.images={}
            def image(self,name,data):self.images[name]=data
        package=Package();assets={}
        output=dict(pno=0,body=[f'<p>Garden <{tag}>w</{tag}> remains readable.</p>'],asides=[])
        records=[dict(id='native-0',role='body',source_regions=[[20,40,280,80]])]
        vp._audit_native_pixels(doc,output,records,tmp_path,package,assets)
    root=etree.fromstring(('<div xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(output['body']+output['asides'])+'</div>').encode())
    assert not root.xpath('.//p//a[@class="source-word-image"]/img'), 'Closing word typography must not bypass refused-print presentation'
    assert len(root.xpath('.//div[@class="reflow-unit-print"]//img'))==1
    assert len(output['asides'])==1
    assert ' '.join(''.join(root.xpath('.//p')[0].itertext()).split())=='Garden remains readable.'
    assert ''.join(root.itertext()).count('†')==1
    for link in root.xpath('.//a[starts-with(@href,"#")]'):
        assert len(root.xpath('//*[@id=$target]',target=link.get('href')[1:]))==1


def _audit_opaque_native_note(tmp_path,monkeypatch,number,extra_container=False):
    from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa,assemble,build_epub
    with pymupdf.open()as doc:
        page=doc.new_page(width=300,height=140)
        page.insert_text((25,60),'Garden',fontsize=12)
        page.insert_text((80,60),'w',fontname='cour',fontsize=12)
        page.insert_text((105,60),'remains readable.',fontsize=12)
        font=next(f[0]for f in page.get_fonts()if f[3]=='Courier')
        doc.xref_set_key(font,'BaseFont','/CustomMarks');page=doc.reload_page(page)
        box=vp.vision_reading.union([w[:4]for w in page.get_text('words')])
        note=assemble.Note(num=number,text='Garden w remains readable.',pno=0,bbox=box)
        fragment=build_epub._scope_ids(build_epub._aside_html(note,{'1':'ref'}, {'1'}),0)
        output=dict(pno=0,body=[],asides=[fragment]);assets={}
        monkeypatch.setattr(pa,'read_crop',lambda *a,**k:[])
        class Package:
            def __init__(self):self.images={}
            def image(self,name,data):self.images[name]=data
        package=Package()
        if extra_container:
            audit=vp._audit_native_pixels
            def inject(*a,**kw):
                audit(*a,**kw)
                a[1]['body'][0]+='<div>Unattributed extra prose.</div>'
            monkeypatch.setattr(vp,'_audit_native_pixels',inject)
        records=vp._audit_native_note_pixels(doc,assemble.Book(notes=[note]),output,tmp_path,package,assets)
    return output,records,package


@pytest.mark.parametrize('number',[None,1])
def test_native_note_keeps_generated_print_inside_its_own_aside(tmp_path,monkeypatch,number):
    from lxml import etree
    output,records,package=_audit_opaque_native_note(tmp_path,monkeypatch,number)
    root=etree.fromstring(('<div xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(output['asides'])+'</div>').encode())
    note=root[0]
    assert note.tag=='aside' and note.get('class')=='footnote'
    assert len(note.xpath('./p'))==1
    assert len(note.xpath('./div[@class="reflow-unit-print"]//img'))==1
    assert not note.xpath('./p//img'),'Refused inline evidence belongs after the note text'
    assert ' '.join(''.join(note.xpath('./p')[0].itertext()).split())==('1 'if number else '')+'Garden remains readable.'
    assert len(records)==1 and records[0]['lane']=='asides' and records[0]['source_aside_index']==0
    assert records[0]['published_tokens']==['Garden','w','remains','readable.']
    assert len(records[0]['source_image_words'])==1
    assert ''.join(root.itertext()).count('†')==1
    for image in root.xpath('.//img'):assert image.get('src')in package.images
    for link in root.xpath('.//a[starts-with(@href,"#")]'):
        if link.get('class')=='note-return':
            assert link.get('href')=='#ref' and link.get('aria-label')=='Return to note reference'
        else:assert len(root.xpath('//*[@id=$target]',target=link.get('href')[1:]))==1


def test_native_note_still_rejects_unowned_extra_lexical_container(tmp_path,monkeypatch):
    with pytest.raises(ValueError,match='Native note audit changed its lexical container'):
        _audit_opaque_native_note(tmp_path,monkeypatch,1,extra_container=True)
