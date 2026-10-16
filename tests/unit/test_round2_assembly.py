"""Independent final-publication controls using synthetic text written for these tests.

Recognition is deterministic fixture evidence; assembly, alignment, packaging,
XML and link validation use production code. Assertions inspect reading XHTML,
not converter source or self-reported word counts.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import io
import posixpath
import zipfile
from xml.etree import ElementTree as ET

import pymupdf
import pytest
import shutil
from PIL import Image, ImageDraw
from cps.services.reflow import build_epub, vision_pipeline, vision_pixels

# Needs the local OCR engine the image ships; without it pages degrade to
# their retained source image.
real_ocr = pytest.mark.skipif(not shutil.which('tesseract'), reason='needs installed OCR engine')

NS = {'x': 'http://www.w3.org/1999/xhtml'}
LEFT = 'Quiet travelers approach the old bridge-'
RIGHT = 'side path before the first light.'
OTHER = 'Several separate sentences discuss ordinary tools gardens roads and buildings today.'


def block(text, ident='b1', role='body', bbox=(20, 650, 480, 950), previous=False, next=False, marker='', low=()):
    return dict(id=ident, role=role, text=text, marker=marker, bbox=list(bbox),
                low_confidence=list(low), continues_previous=previous, continues_next=next)


def answer(*blocks):
    return {'blocks': list(blocks)}


class Reader:
    def __init__(self, readings):
        self._dir = TemporaryDirectory(prefix='synthetic-round2-')
        self.cache = Path(self._dir.name) / 'cache'
        self.readings = iter(readings)
        self.calls = 0
    def read(self, image):
        self.calls += 1
        return dict(answer=next(self.readings), cached=True, new_cost_usd=0)


def optical(text, x, y, *, columns=4, step=50):
    return [dict(text=t, bbox=[x + i % columns * step, y + i // columns * 15,
                              x + i % columns * step + 43, y + i // columns * 15 + 10],
                 line=(int(x), int(y + i // columns * 15)), ordinal=i % columns)
            for i,t in enumerate(text.split())]


def source(pages, width=600, height=400):
    doc = pymupdf.open()
    for words in pages:
        im = Image.new('RGB', (width, height), 'white');draw = ImageDraw.Draw(im)
        for word in words:draw.text(word['bbox'][:2], word['text'], fill='black')
        out=io.BytesIO();im.save(out,format='PNG')
        page=doc.new_page(width=width,height=height);page.insert_image(page.rect,stream=out.getvalue())
    return doc


def docs(path):
    with zipfile.ZipFile(path) as z:
        return {name:ET.fromstring(z.read(name)) for name in z.namelist() if name.endswith('.xhtml')}


def links_resolve(documents):
    for name,root in documents.items():
        for a in root.findall('.//x:a',NS):
            href=a.get('href','')
            if '#' not in href:continue
            file,ident=href.split('#',1)
            target=posixpath.normpath(posixpath.join(posixpath.dirname(name),file)) if file else name
            assert target in documents and any(n.get('id')==ident for n in documents[target].iter()),href


def live_paragraphs(documents):
    rows=[]
    for name,root in documents.items():
        if not Path(name).name.startswith('ch'):continue
        excluded={id(n) for a in root.findall('.//x:aside',NS) for n in a.iter()}
        for p in root.findall('.//x:p',NS):
            if id(p) in excluded or 'source-reading-annotation' in p.get('class',''):continue
            # References are UI, not a source word or paragraph boundary.
            clone=ET.fromstring(ET.tostring(p))
            for parent in clone.iter():
                for child in list(parent):
                    if child.tag.endswith('a') and child.get('class') in ('noteref','source-disclosure-ref'):
                        tail=child.tail or '';idx=list(parent).index(child)
                        if idx:parent[idx-1].tail=(parent[idx-1].tail or '')+tail
                        else:parent.text=(parent.text or '')+tail
                        parent.remove(child)
            rows.append(''.join(clone.itertext()).strip())
            assert not p.findall('.//x:img',NS),'Live body text cannot become an inline crop'
    return rows


def convert(tmp_path, monkeypatch, readings, pixels, layers=None, page_numbers=None):
    monkeypatch.setattr(vision_pixels,'words',lambda display,cache:pixels[display.page.number])
    monkeypatch.setattr(vision_pipeline,'layer_words',lambda doc,n,display:(layers or pixels)[n])
    document=source(pixels);out=tmp_path/'synthetic.epub';reader=Reader(readings)
    try:result=vision_pipeline.convert(document,str(out),reader,page_numbers=page_numbers)
    finally:document.close()
    assert build_epub.validate(str(out))==[]
    documents=docs(out);links_resolve(documents)
    assert reader.calls==len(readings) and result['new_cost_usd']==0
    return live_paragraphs(documents),documents,result


@pytest.mark.parametrize('hyphen',[True,False])
@pytest.mark.parametrize('layout',['leaves','columns'])
def test_adjacent_printed_leaf_or_column_fragments_publish_one_paragraph(tmp_path,monkeypatch,hyphen,layout):
    """Continuation survives two source regions within one PDF page."""
    left=LEFT if hyphen else 'Quiet travelers approach the old'
    right=RIGHT if hyphen else 'Northern Bridge before the first light.'
    y_left=280 if layout=='leaves' else 220
    pixels=optical(left,20,y_left)+optical(right,320,30)
    readings=[answer(block(left,next=True),block(right,ident='b2',bbox=(520,50,980,350),previous=True))]
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels])
    expected=left+right if hyphen else left+' '+right
    assert body==[expected],f'One continuing source paragraph was broken: {body!r}'


@pytest.mark.parametrize('barrier',['heading','caption','footnote','figure'])
def test_same_page_structural_block_is_not_crossed_by_continuation_flags(tmp_path,monkeypatch,barrier):
    pixels=optical(LEFT,20,280)+optical('Separate structural content.',220,180)+optical(RIGHT,320,30)
    middle=block('Separate structural content.',ident='barrier',role=barrier,bbox=(350,430,650,560),marker='2' if barrier=='footnote' else '')
    readings=[answer(block(LEFT,next=True),middle,block(RIGHT,ident='b2',bbox=(520,50,980,350),previous=True))]
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels])
    assert LEFT in body and RIGHT in body,'Continuation cannot move across unrelated printed structure'


@pytest.mark.parametrize('adjacent',[True,False])
def test_pdf_page_continuation_never_crosses_an_unselected_source_page(tmp_path,monkeypatch,adjacent):
    pixels=[optical(LEFT,20,280),optical('An omitted source page.',20,100),optical(RIGHT,20,30)]
    numbers=[0,1] if adjacent else [0,2]
    if adjacent:pixels[1]=optical(RIGHT,20,30)
    readings=[answer(block(LEFT,next=True)),answer(block(RIGHT,bbox=(20,50,480,350),previous=True))]
    body,_,_=convert(tmp_path,monkeypatch,readings,pixels,page_numbers=numbers)
    expected=[LEFT+RIGHT] if adjacent else [LEFT,RIGHT]
    assert body==expected


@pytest.mark.parametrize('damaged_layer',[True,False])
def test_valid_overbroad_bbox_cannot_replace_corroborated_model_with_neighbor_words(tmp_path,monkeypatch,damaged_layer):
    """Numeric-valid geometry is not permission to absorb another column."""
    text='Amber boats carry quiet travelers past the northern village after sunset tonight.'
    own=optical(text,20,80);neighbor=optical(OTHER,320,80);pixels=own+neighbor
    layer=[dict(w,text='broken'+str(i)) for i,w in enumerate(own)] + neighbor if damaged_layer else pixels
    readings=[answer(block(text,bbox=(0,0,1000,1000)),block(OTHER,ident='b2',bbox=(520,180,980,450)))]
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels],[layer])
    assert body==[text,OTHER],f'Ownership contamination duplicated or replaced prose: {body!r}'


@real_ocr
def test_explicit_reference_binds_misclassified_separate_printed_note_outside_body(tmp_path,monkeypatch):
    """Explicit source note identity plus margin territory outranks mistaken body role."""
    text='Quiet travelers<n:n1> reach the northern village.';note='A short printed explanation.'
    pixels=optical('Quiet travelers reach the northern village.',20,80)+optical('1 '+note,20,340)
    readings=[answer(block(text,bbox=(20,180,480,400)),block(note,ident='n1',role='body',marker='1',bbox=(20,820,480,960)))]
    body,documents,_=convert(tmp_path,monkeypatch,readings,[pixels])
    assert body==['Quiet travelers reach the northern village.'],'Printed note prose must leave body flow'
    references=[(name,a) for name,root in documents.items() for a in root.findall('.//x:a',NS) if a.get('class')=='noteref']
    assert len(references)==1
    name,ref=references[0];file,ident=ref.get('href').split('#',1)
    target=posixpath.normpath(posixpath.join(posixpath.dirname(name),file)) if file else name
    dest=next(n for n in documents[target].iter() if n.get('id')==ident)
    assert note in ''.join(dest.itertext())
    returns=[a for a in dest.findall('.//x:a',NS) if ''.join(a.itertext())=='Return']
    assert returns and any(a.get('href','').endswith('#'+ref.get('id')) for a in returns)


def test_bottom_body_paragraph_without_reference_is_not_guessed_into_notes(tmp_path,monkeypatch):
    """Low-page geometry and a numeral alone cannot erase an ordinary paragraph."""
    text='Seven separate observations complete the ordinary chapter.'
    pixels=optical(text,20,340)
    readings=[answer(block(text,role='body',marker='1',bbox=(20,820,480,960)))]
    body,documents,_=convert(tmp_path,monkeypatch,readings,[pixels])
    assert body==[text]
    assert not any(a.get('class')=='noteref' for root in documents.values() for a in root.findall('.//x:a',NS))


@real_ocr
def test_correctly_labeled_printed_note_resolves_and_returns_without_body_note_prose(tmp_path,monkeypatch):
    text='Quiet travelers<n:n1> reach the northern village.';note='A short printed explanation.'
    pixels=optical('Quiet travelers reach the northern village.',20,80)+optical('1 '+note,20,340)
    readings=[answer(block(text,bbox=(20,180,480,400)),block(note,ident='n1',role='footnote',marker='1',bbox=(20,820,480,960)))]
    body,documents,_=convert(tmp_path,monkeypatch,readings,[pixels])
    assert body==['Quiet travelers reach the northern village.']
    refs=[a for root in documents.values() for a in root.findall('.//x:a',NS) if a.get('class')=='noteref']
    assert len(refs)==1 and ''.join(refs[0].itertext())=='1'
    assert any(note in ''.join(a.itertext()) and any(''.join(l.itertext())=='Return' for l in a.findall('.//x:a',NS)) for root in documents.values() for a in root.findall('.//x:aside',NS))


def test_same_column_separate_paragraphs_are_not_joined_by_edge_flags(tmp_path,monkeypatch):
    """Cross-edge flags do not authorize merging paragraphs away from source edges."""
    first='Quiet travelers rest beside the northern bridge.'
    second='Another ordinary paragraph describes the southern village.'
    pixels=optical(first,20,80)+optical(second,20,200)
    readings=[answer(block(first,bbox=(20,180,480,400),next=True),block(second,ident='b2',bbox=(20,480,480,700),previous=True))]
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels])
    assert body==[first,second]


def test_identical_paragraphs_in_distinct_valid_source_regions_publish_exactly_twice(tmp_path,monkeypatch):
    """Repeated lexical content cannot move both owners onto the first occurrence."""
    text='Amber boats carry quiet travelers past the northern village after sunset tonight.'
    pixels=optical(text,20,80)+optical(text,320,80)
    readings=[answer(block(text,bbox=(20,180,480,400)),block(text,ident='b2',bbox=(520,180,980,400)))]
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels])
    assert body==[text,text],f'Two independently printed paragraphs must not become {len(body)}: {body!r}'


def test_joined_pdf_page_paragraph_retains_both_actual_source_page_runs(tmp_path,monkeypatch):
    pixels=[optical(LEFT,20,280),optical(RIGHT,20,30)]
    readings=[answer(block(LEFT,next=True)),answer(block(RIGHT,bbox=(20,50,480,350),previous=True))]
    body,documents,_=convert(tmp_path,monkeypatch,readings,pixels)
    assert body==[LEFT+RIGHT]
    p=next(p for name,root in documents.items() if Path(name).name.startswith('ch') for p in root.findall('.//x:p',NS) if LEFT[:10] in ''.join(p.itertext()))
    runs=[(n.get('data-source-page'),n.get('data-source-block'),''.join(n.itertext())) for n in p.findall('.//x:span',NS) if n.get('data-source-page') is not None]
    assert [(page,ident) for page,ident,text in runs]==[('0','b1'),('1','b1')]
    assert runs[0][2]==LEFT and runs[1][2]==RIGHT


def test_clean_native_text_keeps_exact_body_and_actual_source_page_provenance(tmp_path):
    text='Amber birds follow the quiet river.';document=pymupdf.open();p=document.new_page(width=600,height=800);p.insert_text((60,100),text)
    reader=Reader([]);out=tmp_path/'native.epub'
    try:result=vision_pipeline.convert(document,str(out),reader)
    finally:document.close()
    assert result['native_pages']==[0] and reader.calls==0
    documents=docs(out);links_resolve(documents);assert live_paragraphs(documents)==[text]
    spans=[n for name,root in documents.items() if Path(name).name.startswith('ch') for n in root.findall('.//x:span',NS) if n.get('data-source-page') is not None]
    assert any(n.get('data-source-page')=='0' and ''.join(n.itertext())==text for n in spans)


@pytest.mark.parametrize('role',['running_head','folio'])
def test_leaf_continuation_ignores_only_recognized_page_furniture(tmp_path,monkeypatch,role):
    furniture='37' if role=='folio' else 'SYNTHETIC RUNNING HEAD'
    pixels=optical(LEFT,20,280)+optical(furniture,320,10)+optical(RIGHT,320,60)
    readings=[answer(block(LEFT,next=True),block(furniture,ident='furniture',role=role,bbox=(520,0,980,80)),block(RIGHT,ident='b2',bbox=(520,120,980,350),previous=True))]
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels])
    assert body==[LEFT+RIGHT],'Recognized running heads/folios cannot break a source body continuation'


@real_ocr
def test_actual_margin_note_below_left_tail_does_not_break_leaf_sentence_or_leak_note_prose(tmp_path,monkeypatch):
    note='A short printed explanation.'
    pixels=optical(LEFT,20,280)+optical('1 '+note,20,340)+optical(RIGHT,320,30)
    readings=[answer(block(LEFT+'<n:n1>',bbox=(20,650,480,810),next=True),
                     block(note,ident='n1',role='footnote',marker='1',bbox=(20,820,480,980)),
                     block(RIGHT,ident='b2',bbox=(520,50,980,350),previous=True))]
    body,documents,_=convert(tmp_path,monkeypatch,readings,[pixels])
    assert body==[LEFT+RIGHT],'Below-tail margin note stays outside the continuing body paragraph'
    refs=[a for name,root in documents.items() for a in root.findall('.//x:a',NS) if a.get('class')=='noteref']
    assert len(refs)==1
    assert any(note in ''.join(a.itertext()) and a.findall('.//x:a',NS) for root in documents.values() for a in root.findall('.//x:aside',NS))


@real_ocr
def test_whole_block_source_fallback_cannot_absorb_an_already_owned_printed_note(tmp_path,monkeypatch):
    """A hallucinated broad body block falls back within body source ownership only."""
    body_source='The genuine printed paragraph follows quiet rivers past ordinary villages before sunset.'
    invented='Imaginary violet machines<n:n1> announce impossible discoveries beneath a fictional palace every evening.'
    note='A separate printed note explains ordinary symbols used beside the local river.'
    pixels=optical(body_source,20,80)+optical('1 '+note,20,340)
    readings=[answer(block(invented,bbox=(0,0,1000,1000)),block(note,ident='n1',role='footnote',marker='1',bbox=(20,820,480,990)))]
    body,documents,_=convert(tmp_path,monkeypatch,readings,[pixels])
    assert body==[body_source],f'Fallback must not consume already owned footnote words: {body!r}'
    refs=[a for root in documents.values() for a in root.findall('.//x:a',NS) if a.get('class')=='noteref']
    assert len(refs)==1 and any(note in ''.join(a.itertext()) for root in documents.values() for a in root.findall('.//x:aside',NS))


@pytest.mark.parametrize('hyphen',['-','\u00ad','\u2010','\u2011'])
@pytest.mark.parametrize('boundary',['leaf','pdf-page'])
@pytest.mark.parametrize('disclosure',[False,True])
def test_all_printed_hyphen_characters_survive_source_continuation_without_space(tmp_path,monkeypatch,hyphen,boundary,disclosure):
    """Preserve the actual source character, including a trailing source control."""
    left=LEFT[:-1]+hyphen;right=RIGHT
    first=optical(left,20,280);second=optical(right,320 if boundary=='leaf' else 20,30)
    pixels=[first+second] if boundary=='leaf' else [first,second]
    layers=[[dict(w) for w in page] for page in pixels]
    if disclosure:layers[0][len(first)-1]=dict(layers[0][len(first)-1],text='damaged-source-token')
    if boundary=='leaf':readings=[answer(block(left,next=True),block(right,ident='b2',bbox=(520,50,980,350),previous=True))]
    else:readings=[answer(block(left,next=True)),answer(block(right,bbox=(20,50,480,350),previous=True))]
    body,documents,_=convert(tmp_path,monkeypatch,readings,pixels,layers)
    assert body==[left+right],f'Literal hyphen U+{ord(hyphen):04X} or its word boundary changed: {body!r}'
    refs=[a for name,root in documents.items() if Path(name).name.startswith('ch') for a in root.findall('.//x:a',NS) if a.get('class')=='source-disclosure-ref']
    assert len(refs)==int(disclosure),'Source-control suffix must survive exact hyphen joining'


def test_damaged_marginal_header_cannot_rebind_to_matching_body_phrase(tmp_path,monkeypatch):
    """Unreadable furniture stays in its source margin; repeats cannot steal prose."""
    header='NORTHERN SHORE TRAVEL MAPS AND ORDINARY TOOLS FOR READERS'
    damaged='N0R TH SH0 RE TRV MP OR TL RD'
    body='A careful reader studies '+header+' near the village before sunset.'
    pixels=optical(damaged,20,8)+optical(body,20,120)
    readings=[answer(block(header,ident='head',role='running_head',bbox=(20,0,480,140)),
                     block(body,bbox=(20,260,480,650)))]
    actual,_,_=convert(tmp_path,monkeypatch,readings,[pixels])
    assert actual==[body],f'Marginal furniture leaked into live prose: {actual!r}'


def test_valid_body_rectangle_can_expand_to_its_independently_matched_pixels(tmp_path,monkeypatch):
    """Furniture restraint cannot disable the existing general body recovery."""
    text='Quiet travelers carry ordinary tools through the village after sunset today.'
    pixels=optical(text,20,180)
    readings=[answer(block(text,bbox=(20,40,480,160)))]
    actual,documents,_=convert(tmp_path,monkeypatch,readings,[pixels])
    assert actual==[text]
    owners=[n for root in documents.values() for n in root.iter() if n.get('data-source-block')=='b1']
    import json
    assert owners and all(json.loads(n.get('data-source-regions'))[0][1]>=170 for n in owners)


def test_omitted_adjacent_body_tail_recovers_inside_its_paragraph_across_pdf_edge(tmp_path,monkeypatch):
    prefix='Quiet travelers carry ordinary tools through narrow roads toward the old'
    tail='northern village beside quiet gardens roads and buildings'
    head='before sunset tonight.'
    pixels=[optical(prefix,20,260)+optical(tail,20,305),optical(head,20,30)]
    readings=[answer(block(prefix,bbox=(20,620,480,750),next=True)),
              answer(block(head,bbox=(20,50,480,220),previous=True))]
    actual,_,_=convert(tmp_path,monkeypatch,readings,pixels)
    assert actual==[prefix+' '+tail+' '+head],f'Recovered body tail broke its actual paragraph: {actual!r}'


def test_adjacent_source_tail_recovery_does_not_duplicate_next_owned_body_head(tmp_path,monkeypatch):
    prefix='Quiet travelers carry ordinary tools through narrow roads toward the old'
    tail='northern village beside quiet gardens roads and buildings'
    head='Bright stars guide careful travelers home before sunrise.'
    pixels=optical(prefix,20,260)+optical(tail,20,305)+optical(head,320,30)
    readings=[answer(block(prefix+' '+head,bbox=(20,620,480,750),next=False),
                     block(head,ident='b2',bbox=(520,50,980,230),previous=True))]
    actual,_,_=convert(tmp_path,monkeypatch,readings,[pixels])
    assert ' '.join(actual)==prefix+' '+tail+' '+head,f'Owned head duplicated or omitted tail lost: {actual!r}'


def test_repeated_next_head_printed_in_both_columns_remains_twice_after_tail_recovery(tmp_path,monkeypatch):
    """Tail recovery cannot deduplicate a phrase independently printed twice."""
    prefix='Quiet travelers carry ordinary tools through narrow roads toward the old'
    head='Bright stars guide careful travelers home before sunrise.'
    tail='northern village beside quiet gardens roads and buildings'
    printed_left=prefix+' '+head
    pixels=optical(printed_left,20,260)+optical(tail,20,335)+optical(head,320,30)
    readings=[answer(block(printed_left,bbox=(20,620,480,840),next=False),
                     block(head,ident='b2',bbox=(520,50,980,230),previous=True))]
    actual,_,_=convert(tmp_path,monkeypatch,readings,[pixels])
    assert ' '.join(actual)==printed_left+' '+tail+' '+head,f'Genuinely repeated source phrase was deduplicated: {actual!r}'


@pytest.mark.parametrize('role',['caption','footnote'])
def test_adjacent_owned_caption_or_note_cannot_be_absorbed_as_missing_body_tail(tmp_path,monkeypatch,role):
    prefix='Quiet travelers carry ordinary tools through narrow roads toward the old'
    separate='Seven separate printed words describe this independent structural explanation.'
    head='before sunset tonight.'
    text=prefix+'<n:n1>' if role=='footnote' else prefix
    printed='7 '+separate if role=='footnote' else separate
    pixels=[optical(prefix,20,260)+optical(printed,20,305),optical(head,20,30)]
    readings=[answer(block(text,bbox=(20,620,480,750),next=True),
                     block(separate,ident='n1',role=role,marker='7' if role=='footnote' else '',bbox=(20,760,480,940))),
              answer(block(head,bbox=(20,50,480,220),previous=True))]
    actual,documents,_=convert(tmp_path,monkeypatch,readings,pixels)
    if role=='caption':assert prefix in actual and head in actual and separate in actual
    else:
        assert all(separate not in p for p in actual)
        assert any(separate in ''.join(a.itertext()) for root in documents.values() for a in root.findall('.//x:aside',NS))


@pytest.mark.parametrize('case',['unflagged_continuation','real_indent','terminal_punctuation','heading'])
def test_source_leaf_edge_continuation_uses_geometry_and_lexical_evidence_when_flags_absent(tmp_path,monkeypatch,case):
    left='Quiet travelers approach the old' if case!='terminal_punctuation' else 'Quiet travelers approach the old bridge.'
    right='northern bridge before the first light.'
    own=optical(left,20,280);following=optical(right,320,30)
    if case=='real_indent':following[0]['bbox'][0]+=30;following[0]['bbox'][2]+=30
    readings=[answer(block(left,next=False),block(right,ident='b2',role='heading' if case=='heading' else 'body',bbox=(520,50,980,350),previous=False))]
    actual,_,_=convert(tmp_path,monkeypatch,readings,[own+following])
    if case=='unflagged_continuation':assert actual==[left+' '+right],f'Pixel-observed unflagged leaf continuation broke: {actual!r}'
    elif case=='heading':assert actual==[left]
    else:assert actual==[left,right],f'Independent source paragraph was overjoined: {actual!r}'


def test_pixel_geometry_orders_fragmented_word_and_lines_before_model_guard(tmp_path,monkeypatch):
    """OCR block order cannot displace an independently readable multiline source."""
    text='Historical records describe quiet travelers carrying ordinary tools along northern roads through peaceful villages beside broad rivers beneath clear skies before sunset tonight.'
    ordered=optical(text,20,80,columns=12,step=45)
    first=dict(ordered[0],text='Histo',bbox=[20,80,40,90])
    second=dict(ordered[0],text='rical',bbox=[41,80,63,90])
    # An italic word fragment arrives after later OCR blocks; its original
    # pixel position, and every other word position, are still correct.
    shuffled=[first]+ordered[12:]+[second]+ordered[1:12]
    readings=[answer(block(text,bbox=(0,180,1000,500)))]
    actual,_,_=convert(tmp_path,monkeypatch,readings,[shuffled],[[]])
    assert actual==[text],f'OCR ordering displaced the corroborated model paragraph: {actual!r}'


def test_pixel_geometry_ordering_cannot_exonerate_an_actual_omitted_source_line(tmp_path,monkeypatch):
    """Reordering source evidence must retain the complete-source omission guard."""
    text='Amber boats carry quiet travelers past the northern village after sunset while ordinary workers prepare useful tools beside the river before every morning journey.'
    printed=optical(text,20,80)
    tokens=text.split();candidate=' '.join(tokens[:4]+tokens[16:])
    shuffled=printed[:4]+printed[16:]+printed[4:16]
    readings=[answer(block(candidate,bbox=(20,180,480,500)))]
    actual,_,_=convert(tmp_path,monkeypatch,readings,[shuffled],[[]])
    assert ' '.join(tokens[4:16]) in ' '.join(actual),'Real omitted source lines must remain published with uncertainty'


@pytest.mark.parametrize('case',['unflagged_continuation','real_indent','terminal_punctuation','heading'])
def test_source_pdf_edge_continuation_uses_geometry_and_lexical_evidence_when_flags_absent(tmp_path,monkeypatch,case):
    """The same source paragraph can cross a PDF edge with missing reader flags."""
    left='Quiet travelers approach the old' if case!='terminal_punctuation' else 'Quiet travelers approach the old bridge.'
    right='northern bridge before the first light.'
    following=optical(right,20,30)
    if case=='real_indent':following[0]['bbox'][0]+=30;following[0]['bbox'][2]+=30
    readings=[answer(block(left,next=False)),answer(block(right,role='heading' if case=='heading' else 'body',bbox=(20,50,480,350),previous=False))]
    actual,_,_=convert(tmp_path,monkeypatch,readings,[optical(left,20,280),following])
    if case=='unflagged_continuation':assert actual==[left+' '+right],f'Pixel-observed unflagged PDF continuation broke: {actual!r}'
    elif case=='heading':assert actual==[left]
    else:assert actual==[left,right],f'Independent PDF-edge paragraph was overjoined: {actual!r}'


def synthetic_tsv(tokens):
    header='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
    return header+''.join(chr(9).join(map(str,(5,1,1,1,1,i+1,10+i*10,20,8,10,98,word)))+'\n' for i,word in enumerate(tokens))


def synthetic_display():
    from types import SimpleNamespace
    pix=SimpleNamespace(width=100,height=200,tobytes=lambda kind:b'synthetic-portrait-pixels')
    return SimpleNamespace(rect=SimpleNamespace(width=100,height=200),pixmap=lambda **kwargs:pix),pix


@pytest.mark.parametrize('quote',['"','"quoted"'])
def test_actual_optical_stdout_parser_preserves_literal_quotes_and_following_records(tmp_path,monkeypatch,quote):
    """Tesseract TSV text is literal print, not CSV quoting syntax."""
    from types import SimpleNamespace
    tokens=['before',quote,'after',r'plain\word','', 'final']
    monkeypatch.setattr(vision_pixels.shutil,'which',lambda name:'/synthetic/tesseract')
    monkeypatch.setattr(vision_pixels.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=synthetic_tsv(tokens).encode(),stderr=b''))
    display,pix=synthetic_display();actual=vision_pixels._recognize(display,pix,pix.tobytes('png'),tmp_path/'fresh.json')
    assert [w['text'] for w in actual]==[w for w in tokens if w], 'A printed quote cannot consume rows or lose its literal glyphs'
    assert all(len(w['bbox'])==4 for w in actual)


def test_old_optical_cache_is_recomputed_without_modifying_old_evidence_then_reused(tmp_path,monkeypatch):
    """A parser repair cannot keep reusing a cache containing embedded TSV rows."""
    import hashlib,json
    from types import SimpleNamespace
    display,pix=synthetic_display();identity=hashlib.sha256(pix.tobytes('png')).hexdigest()
    old=tmp_path/(identity+'.json');old_bytes=json.dumps([{'text':'old corrupted TSV record','bbox':[0,0,1,1]}]).encode();old.write_bytes(old_bytes)
    calls=[];tokens=['before','"','after']
    def recognize(*args,**kwargs):
        calls.append(1);return SimpleNamespace(returncode=0,stdout=synthetic_tsv(tokens).encode(),stderr=b'')
    monkeypatch.setattr(vision_pixels.shutil,'which',lambda name:'/synthetic/tesseract')
    monkeypatch.setattr(vision_pixels.subprocess,'run',recognize)
    first=vision_pixels.words(display,tmp_path);second=vision_pixels.words(display,tmp_path)
    assert [w['text'] for w in first]==tokens and second==first and len(calls)==1
    assert old.read_bytes()==old_bytes,'Original recognition evidence must remain preserved'
