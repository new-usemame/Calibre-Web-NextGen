"""Hidden damaged layers and retained source structure must not silently flatten."""
import io
import re
import zipfile
import pymupdf
import pytest
from cps.services.reflow import extract,source,skeleton,assemble,build_epub,ocr


def _hidden_scan(hidden=True):
    doc=pymupdf.open();page=doc.new_page(width=400,height=600)
    paper=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,400,600));paper.clear_with(255)
    page.insert_image(page.rect,stream=paper.tobytes('png'))
    page.insert_text((40,100),'zq$ if lt ; 9 : 6 7',fontsize=12,render_mode=3 if hidden else 0)
    return doc


@pytest.mark.parametrize('hidden',[True,False])
def test_short_hidden_scan_noise_requests_recovery_but_visible_source_is_preserved(hidden):
    with _hidden_scan(hidden) as doc:
        raw=extract.read_page(doc,0)
        assert source.needs_recovery(raw)==('damaged_layer' if hidden else '')


def test_failed_hidden_scan_recovery_preserves_pixels_and_exact_source_link(monkeypatch,tmp_path):
    monkeypatch.setattr(ocr,'_engine',lambda *a:None)
    def failed(*a,**k):raise ocr.OCRFailed('fixture recognition unavailable')
    monkeypatch.setattr(ocr,'recognize_page',failed)
    with _hidden_scan() as doc:
        raw=extract.read_page(doc,0)
        recovery=source.recover(doc,[raw],extract.document_fingerprint(doc),mode='auto')
        assert recovery.failed==1
        style=skeleton.book_style(recovery.pages)
        book=assemble.assemble([skeleton.page_skeleton(raw,style)],style,[raw])
        assert book.conservation.ok
        path=tmp_path/'fallback.epub';build_epub.build(book,str(path),doc=doc)
        assert build_epub.validate(str(path))==[]
        with zipfile.ZipFile(path) as z:
            body=''.join(z.read(n).decode() for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n))
            assert 'zq$' not in body
            assert 'original-p0000.xhtml#page' in body
            assert 'Original page image' in body


def test_numbered_source_lines_are_distinct_items_not_inline_numbers(tmp_path):
    from xml.etree import ElementTree as ET
    with pymupdf.open() as doc:
        p=doc.new_page(width=400,height=600)
        for i,text in enumerate(['First source item','Second source item continues','Third source item']):
            p.insert_text((40,120+i*13),str(i+1)+'.',fontsize=11)
            p.insert_text((60,120+i*13),text,fontsize=11)
        p.insert_text((40,230),'Prose refers to 1. one point and 2. another in one sentence.',fontsize=10)
        artwork=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,120,100));artwork.clear_with(80)
        p.insert_image(pymupdf.Rect(220,330,340,430),stream=artwork.tobytes('png'))
        book=assemble.deterministic_book(doc);path=tmp_path/'list.epub';build_epub.build(book,str(path),doc=doc)
        assert book.conservation.ok
        assert build_epub.validate(str(path))==[]
        with zipfile.ZipFile(path) as z:
            roots=[ET.fromstring(z.read(n)) for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)]
            ns='{http://www.w3.org/1999/xhtml}'
            lists=[n for root in roots for n in root.iter(ns+'ol')]
            assert len(lists)==1
            assert [re.sub(r'\s+',' ',''.join(n.itertext())).strip() for n in lists[0].findall(ns+'li')]==['1. First source item','2. Second source item continues','3. Third source item']
            assert any('Prose refers to 1.' in ''.join(n.itertext()) for root in roots for n in root.iter(ns+'p'))


def test_overpainted_paired_columns_preserve_complete_source_associations(tmp_path):
    def put(p):
        p.insert_text((40,110),'Authors',fontsize=10);p.insert_text((180,110),'Meaning',fontsize=10)
        for i in range(3):
            y=140+i*75
            p.insert_text((40,y),'Author '+str(i+1),fontsize=10)
            p.insert_text((40,y+13),'Work title '+str(i+1),fontsize=10)
            p.insert_text((180,y),'This definition belongs to this author.',fontsize=9)
            p.insert_text((180,y+12),'Its continuation remains associated.',fontsize=9)
    with pymupdf.open() as raster:
        p=raster.new_page(width=400,height=500);put(p);png=p.get_pixmap().tobytes('png')
    with pymupdf.open() as doc:
        p=doc.new_page(width=400,height=500);put(p);p.insert_image(p.rect,stream=png)
        book=assemble.deterministic_book(doc)
        assert book.conservation.ok
        assert any(f['found']=='unverified_paired_columns' for f in book.figures)
        assert not any('Work title' in e.text for e in book.elements)
        path=tmp_path/'paired.epub';build_epub.build(book,str(path),doc=doc)
        assert build_epub.validate(str(path))==[]
        with zipfile.ZipFile(path) as z:
            body=''.join(z.read(n).decode() for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n))
            assert 'Original column relationships' in body
            assert 'original-p0000.xhtml#page' in body


def test_plausible_invisible_prose_is_not_automatically_replaced_by_second_ocr():
    with _hidden_scan() as doc:
        doc[0].insert_textbox((40,150,360,300),
            'This is a plausible paragraph of ordinary prose with enough words to pass a language test. '
            'The source is a normal passage with complete sentences and the same readable words in each line. '
            'It does not require another recognition pass merely because the embedded transcript is invisible.',
            fontsize=10,render_mode=3)
        raw=extract.read_page(doc,0)
        assert source.needs_recovery(raw)==''


@pytest.mark.parametrize('numeric',[True,False])
def test_unverified_three_column_layout_owns_pixels_once_and_preserves_surrounding_prose(numeric,tmp_path):
    def put(p,hidden):
        p.insert_textbox((30,25,420,95),'A surrounding source paragraph remains outside the numeric table. '
            'This is ordinary prose with enough words to establish that the page has a readable passage. '
            'It remains in the source and should still reflow around the table.',fontsize=9,render_mode=3 if hidden else 0)
        for col in range(3):
            for row in range(12):
                text=f'{row+1} {col+1}.{row:02d}' if numeric else f'Column {col+1} line {row+1}'
                p.insert_text((40+col*130,110+row*13),text,fontsize=9,render_mode=3 if hidden else 0)
    with pymupdf.open() as pixels:
        p=pixels.new_page(width=440,height=500);put(p,False);png=p.get_pixmap().tobytes('png')
    with pymupdf.open() as doc:
        p=doc.new_page(width=440,height=500);p.insert_image(p.rect,stream=png);put(p,True)
        raw=extract.read_page(doc,0)
        assert raw.is_page_scan
        assert raw.text_layer_invisible
        book=assemble.deterministic_book(doc)
        assert book.conservation.ok
        regions=[f for f in book.figures if f['found']=='unverified_scan_layout']
        assert len(regions)==1
        assert not any('Column 1 line' in e.text or '1 1.00' in e.text for e in book.elements)
        if numeric:
            assert regions[0]['bbox'][1]>80
            assert any('surrounding source paragraph' in e.text for e in book.elements)
        else:
            assert tuple(regions[0]['bbox'])==(0,0,440,500)
        path=tmp_path/'layout.epub';build_epub.build(book,str(path),doc=doc)
        assert build_epub.validate(str(path))==[]


def test_uncertain_ocr_crops_keep_the_proven_column_reading_order():
    blocks=[]
    for x in (30,280):
        for y in (100,200,300):
            lines=[]
            for j,text in enumerate(('This source paragraph begins with words',
                                      'and continues as ordinary prose in its column.',
                                      'The final source line ends this paragraph.')):
                span=extract.Span(text,10,'ocr',0,(x,y+j*13,x+200,y+j*13+10),uncertain=j==1)
                lines.append(extract.Line([span],span.bbox))
            blocks.append(extract.Block(len(blocks),(x,y,x+200,y+36),lines))
    raw=extract.RawPage(pno=0,width=520,height=450,blocks=blocks,
        images=[extract.Image(bbox=(0,0,520,450),area_ratio=1.0)])
    style=skeleton.book_style([raw]);book=assemble.assemble([skeleton.page_skeleton(raw,style)],style,[raw])
    assert book.conservation.ok
    positions=[f['bbox'][0] for f in book.figures if f['found']=='ocr_uncertain_region']
    assert len(positions)==6
    assert max(positions[:3])<min(positions[3:])


@pytest.mark.parametrize('invisible',[True,False])
def test_source_tabular_contents_rows_do_not_flatten_an_unverified_scan(invisible):
    lines=[]
    for i,label in enumerate(('The first source chapter in the printed book','The second source chapter in the printed book','The third source chapter in the printed book','The last source chapter in the printed book')):
        span=extract.Span(label+'\t'+str(20+i*10),10,'Times',0,(30,100+i*18,380,110+i*18))
        lines.append(extract.Line([span],span.bbox))
    raw=extract.RawPage(pno=0,width=420,height=400,blocks=[extract.Block(0,(30,100,380,164),lines)],
        images=[extract.Image(bbox=(0,0,420,400),area_ratio=1.0)],text_layer_invisible=invisible)
    style=skeleton.book_style([raw]);book=assemble.assemble([skeleton.page_skeleton(raw,style)],style,[raw])
    assert book.conservation.ok
    assert bool([f for f in book.figures if f['found']=='unverified_scan_layout']) is invisible
    assert any('The first source chapter' in e.text for e in book.elements) is not invisible

@pytest.mark.parametrize('hidden',[True,False])
def test_scan_figure_owns_connected_large_symbol_blocks_above_partial_crop(hidden,monkeypatch,tmp_path):
    # A measured figure begins in the second symbol row. The source layer groups
    # its header/first rows separately; those must not become junk prose above it.
    def put(page,mode):
        page.insert_text((80,95),'Day  Night',fontsize=18,render_mode=mode)
        page.insert_text((80,125),'Q / X',fontsize=22,render_mode=mode)
        page.insert_text((80,155),'0 VS 9',fontsize=22,render_mode=mode)
        page.insert_text((80,185),'Y / Z',fontsize=22,render_mode=mode)
        page.insert_textbox((50,240,350,550),('This ordinary source paragraph must remain outside the figure. It has complete sentences and readable words that establish a reliable prose region after the diagram. The paragraph continues in its normal reading order without changing its words or promoting a chart label into prose. ')*2,fontsize=10,render_mode=mode)
    with pymupdf.open() as pixels:
        p=pixels.new_page(width=400,height=600);put(p,0);png=p.get_pixmap().tobytes('png')
    with pymupdf.open() as doc:
        p=doc.new_page(width=400,height=600);p.insert_image(p.rect,stream=png);put(p,3 if hidden else 0)
        raw=extract.read_page(doc,0);style=skeleton.book_style([raw]);style.body_size=10
        monkeypatch.setattr(skeleton,'_scan_figures',lambda *a:[skeleton.Region(kind='figure',bbox=(65,140,250,205),reason='scan_figure_band')])
        skel=skeleton.page_skeleton(raw,style)
        book=assemble.assemble([skel],style,[raw])
        assert book.conservation.ok
        assert any('ordinary source paragraph' in e.text for e in book.elements)
        figure=next(f for f in book.figures if f['found']=='scan_figure_band')
        if hidden:
            assert figure['bbox'][1] < 80
            assert not any('Day' in e.text or 'Q / X' in e.text for e in book.elements)
        else:
            assert figure['bbox'][1]==140
        path=tmp_path/'connected.epub';build_epub.build(book,str(path),doc=doc)
        assert build_epub.validate(str(path))==[]

@pytest.mark.parametrize('separate_numbers',[True,False])
def test_scan_contents_separate_page_number_column_preserves_whole_row_region(separate_numbers,tmp_path):
    def put(p,mode):
        for section in range(3):
            for row in range(5):
                y=70+section*90+row*15
                label=['Introductory observations','The deeper account','Related principles','Additional considerations','Closing observations'][row]
                if separate_numbers:
                    p.insert_text((50,y),label,fontsize=10,render_mode=mode)
                    p.insert_text((320,y),str(100+section*10+row),fontsize=10,render_mode=mode)
                else:
                    p.insert_text((50,y),label+' refers to 123 incidents in ordinary prose.',fontsize=10,render_mode=mode)
        p.insert_textbox((40,380,380,550),('This unrelated ordinary prose below the rows must remain selectable. It contains complete readable sentences and references to 12 events within the paragraph. ')*3,fontsize=10,render_mode=mode)
    with pymupdf.open() as pixels:
        p=pixels.new_page(width=420,height=600);put(p,0);png=p.get_pixmap().tobytes('png')
    with pymupdf.open() as doc:
        p=doc.new_page(width=420,height=600);p.insert_image(p.rect,stream=png);put(p,3)
        book=assemble.deterministic_book(doc)
        assert book.conservation.ok
        assert any('unrelated ordinary prose' in e.text for e in book.elements)
        regions=[f for f in book.figures if f['found']=='unverified_scan_layout']
        if separate_numbers:
            assert any(f['bbox'][1]<65 and f['bbox'][3]>305 for f in regions)
            assert not any('Introductory observations' in e.text or 'Closing observations' in e.text for e in book.elements)
        else:
            assert not regions
        path=tmp_path/'contents.epub';build_epub.build(book,str(path),doc=doc)
        assert build_epub.validate(str(path))==[]

@pytest.mark.parametrize('barrier',['prose','other_column','disconnected'])
def test_connected_scan_figure_stops_at_source_boundaries(barrier):
    from types import SimpleNamespace as NS
    def line(text,box,size=20):return NS(text=text,stripped=text,bbox=box,size=size)
    seed=line('X / Y',(110,90,220,115))
    upper=line('Q / Z',(110,60,220,80))
    if barrier=='prose':upper=line('An ordinary sentence with enough readable words must stay prose.',(110,60,220,80),10)
    if barrier=='other_column':upper=line('Q / Z',(10,60,90,80))
    if barrier=='disconnected':upper=line('Q / Z',(110,20,220,40))
    blocks=[(NS(bbox=ln.bbox),[ln]) for ln in [upper,seed]]
    figure=skeleton.Region(kind='figure',bbox=(100,100,250,200),reason='scan_figure_band')
    kept,art=skeleton._complete_unverified_figure_tops(NS(is_page_scan=True,text_layer_invisible=True),blocks,[figure],NS(body_size=10))
    assert figure.bbox==(100,88,250,200)
    assert any(upper is ln for _,lines in kept for ln in lines)
    assert [ln.text for region in art for ln in region.lines]==['X / Y']

def test_captioned_invisible_diagram_keeps_outer_art_and_has_one_owner(tmp_path):
    def text(p,mode):
        for x,y,t in [(140,80,'12 y'),(90,110,'Q'),(210,110,'R'),(80,150,'X'),(230,150,'Y'),(100,200,'Z'),(200,200,'0'),(140,220,'20 y')]:
            p.insert_text((x,y),t,fontsize=9,render_mode=mode)
        p.insert_text((65,275),'Figure 1 - General periods and subperiods',fontsize=10,render_mode=mode)
        p.insert_textbox((40,300,360,480),('The surrounding ordinary prose describes the diagram without becoming part of its image. This paragraph must remain readable and selectable below the complete illustration. ')*3,fontsize=10,render_mode=mode)
    with pymupdf.open() as pixels:
        p=pixels.new_page(width=400,height=520);p.draw_circle((160,155),100,color=(0,0,0));text(p,0);png=p.get_pixmap().tobytes('png')
    with pymupdf.open() as doc:
        p=doc.new_page(width=400,height=520);p.insert_image(p.rect,stream=png);text(p,3)
        raw=extract.read_page(doc,0);style=skeleton.book_style([raw]);style.body_size=10
        probe=extract.ScanPixelProbe(doc,0,mask=[ln.bbox for b in raw.text_blocks for ln in b.lines])
        book=assemble.assemble([skeleton.page_skeleton(raw,style,pixel_probe=probe)],style,[raw])
        assert book.conservation.ok
        diagrams=[f for f in book.figures if f['bbox'][1]<250]
        assert len(diagrams)==1
        x0,y0,x1,y1=diagrams[0]['bbox']
        assert x0<61 and x1>259 and y0<56 and y1>254
        assert not any('12 y' in e.text for e in book.elements)
        assert any('surrounding ordinary prose' in e.text for e in book.elements)
        path=tmp_path/'circle.epub';build_epub.build(book,str(path),doc=doc)
        assert build_epub.validate(str(path))==[]

@pytest.mark.parametrize('dark',[True,False])
def test_unverified_text_on_full_page_art_preserves_picture_once(dark,tmp_path):
    prose=('The author describes the history and purpose of this volume in a readable cover paragraph. '
           'Its portrait and background artwork are part of the source, not optional decoration. ')*5
    with pymupdf.open() as pixels:
        p=pixels.new_page(width=400,height=500)
        if dark:p.draw_rect(p.rect,color=None,fill=(.05,.1,.15))
        p.insert_textbox((40,40,360,300),prose,fontsize=10,color=(1,1,1) if dark else (0,0,0))
        if dark:p.draw_rect((250,340,360,460),color=None,fill=(.8,.4,.2))
        png=p.get_pixmap().tobytes('png')
    with pymupdf.open() as doc:
        p=doc.new_page(width=400,height=500);p.insert_image(p.rect,stream=png)
        p.insert_textbox((40,40,360,300),prose,fontsize=10,render_mode=3)
        book=assemble.deterministic_book(doc)
        assert book.conservation.ok
        if dark:
            assert any(tuple(f['bbox'])==(0,0,400,500) for f in book.figures)
            assert not any('The author describes' in e.text for e in book.elements)
        else:
            assert any('The author describes' in e.text for e in book.elements)
        path=tmp_path/'cover.epub';build_epub.build(book,str(path),doc=doc)
        assert build_epub.validate(str(path))==[]

@pytest.mark.parametrize('references',[True,False])
def test_unverified_reference_columns_keep_entry_associations(references,tmp_path):
    def put(p,mode):
        for col in range(2):
            for row in range(14):
                text=f'Term {chr(65+row)}, 125, 273-286, 541' if references else 'This is normal prose with readable words.'
                p.insert_text((35+col*210,80+row*16),text,fontsize=9,render_mode=mode)
        p.insert_textbox((35,340,425,600),('Ordinary source prose continues after the reference columns. It remains readable and selectable, with complete sentences that explain the surrounding discussion. ')*6,fontsize=10,render_mode=mode)
    with pymupdf.open() as pixels:
        p=pixels.new_page(width=460,height=650);put(p,0);png=p.get_pixmap().tobytes('png')
    with pymupdf.open() as doc:
        p=doc.new_page(width=460,height=650);p.insert_image(p.rect,stream=png);put(p,3)
        book=assemble.deterministic_book(doc);assert book.conservation.ok
        if references:
            assert any(f['found']=='unverified_scan_layout' for f in book.figures)
            assert not any('Term A' in e.text for e in book.elements)
        else:
            assert any('normal prose' in e.text for e in book.elements)
        path=tmp_path/'index.epub';build_epub.build(book,str(path),doc=doc)
        assert build_epub.validate(str(path))==[]


@pytest.mark.parametrize("sequence",[True,False])
def test_deep_margin_folio_sequence_is_furniture_but_body_digits_are_not(sequence):
    from cps.services.reflow import extract, skeleton, assemble
    raws=[]
    for p in range(4):
        def line(text,y):
            return extract.Line([extract.Span(text,10,'Times',0,(40,y,300,y+10),y+10)],(40,y,300,y+10))
        lines=[line('This is complete ordinary prose continuing across the page.',100),
               line(str(20+p*3),130),line('This is the final body sentence on the page.',570),line(str(p+1) if sequence else '42',640)]
        raws.append(extract.RawPage(p,500,800,[extract.Block(i,l.bbox,[l]) for i,l in enumerate(lines)],[],0))
    style=skeleton.book_style(raws)
    for raw in raws:
        skel=skeleton.page_skeleton(raw,style)
        assert any(r.kind==('furniture' if sequence else 'body') and any(l.text==(str(raw.pno+1) if sequence else '42') for l in r.lines) for r in skel.regions)
        assert any(r.kind=='body' and any(l.text==str(20+raw.pno*3) for l in r.lines) for r in skel.regions)


@pytest.mark.parametrize('barrier',[None,'column','gap','figure','unrelated'])
def test_source_numbered_list_wraps_across_extraction_blocks_without_eating_other_regions(barrier):
    def line(text,x,y):
        return extract.Line([extract.Span(text,10,'Times',0,(x,y,250,y+10))],(x,y,250,y+10))
    lines=[line('1. First item.',40,20),line('2. Second item.',40,33),line('3. A plan-',40,46)]
    a=skeleton.Region(kind='list',lines=lines,list_groups=[[l] for l in lines],bbox=(40,20,250,56))
    tail=[line('et in this item.',60,59),line('4. Final item.',40,72)]
    b=skeleton.Region(kind='body',lines=tail,bbox=(40,59,250,82))
    if barrier=='column':b.column=1
    if barrier=='gap':b.lines=[line('et in this item.',60,99),line('4. Final item.',40,112)]
    if barrier=='unrelated':b.lines=[line('Independent prose begins here.',40,59)]
    regions=[a,b]
    if barrier=='figure':regions.insert(1,skeleton.Region(kind='figure',bbox=(40,56,250,59)))
    page=skeleton.PageSkeleton(0,400,600,regions=regions)
    skeleton._join_numbered_regions(page)
    if barrier is not None:
        assert len(a.list_groups)==3 and b in page.regions
    else:
        assert len(page.regions)==1 and len(a.list_groups)==4
        assert [l.text for l in a.list_groups[2]]==['3. A plan-','et in this item.']
        assert a.list_groups[3][0].text=='4. Final item.'


@pytest.mark.parametrize('tail_x,tail_y',[(40,85),(60,140)])
def test_list_continuation_consumes_only_proved_prefix_of_a_mixed_pdf_block(tail_x,tail_y):
    def line(text,x,y):
        return extract.Line([extract.Span(text,10,'Times',0,(x,y,250,y+10))],(x,y,250,y+10))
    head=[line('1. One.',40,20),line('2. A plan-',40,33)]
    tail=[line('et completes the item.',60,46),line('3. Last item.',40,59),line('Independent prose remains outside.',tail_x,tail_y)]
    a=skeleton.Region(kind='list',lines=head,list_groups=[[l] for l in head],bbox=(40,20,250,43))
    b=skeleton.Region(kind='body',lines=tail,bbox=(40,46,250,tail_y+10))
    page=skeleton.PageSkeleton(0,400,600,regions=[a,b])
    before=[id(l) for r in page.regions for l in r.lines]
    skeleton._join_numbered_regions(page)
    assert len(page.regions)==2 and len(a.list_groups)==3
    assert page.regions[1].text=='Independent prose remains outside.'
    assert [id(l) for r in page.regions for l in r.lines]==before


@pytest.mark.parametrize('overlap',[False,True])
def test_continued_list_accepts_only_geometrically_separate_marker_and_text_on_one_row(overlap):
    def line(text,x,y,width=180):
        return extract.Line([extract.Span(text,10,'Times',0,(x,y,x+width,y+10))],(x,y,x+width,y+10))
    head=[line('1. One.',40,20),line('2. A plan-',40,33)]
    tail=[line('et completes the item.',60,46),line('3.',40,60,8),line('Last item.',45 if overlap else 60,59)]
    a=skeleton.Region(kind='list',lines=head,list_groups=[[l] for l in head],bbox=(40,20,220,43))
    b=skeleton.Region(kind='body',lines=tail,bbox=(40,46,240,70))
    page=skeleton.PageSkeleton(0,400,600,regions=[a,b])
    skeleton._join_numbered_regions(page)
    if overlap:
        assert len(page.regions)==2 and len(a.list_groups)==2
    else:
        assert len(page.regions)==1 and len(a.list_groups)==3
        assert [l.text for l in a.list_groups[-1]]==['3.','Last item.']

@pytest.mark.parametrize('uncertain',[True,False])
def test_numbered_list_keeps_source_punctuation_qualification_and_passage_route(uncertain,tmp_path):
    def line(text,y,flag=False):
        span=extract.Span(text,10,'Times',0,(40,y,260,y+10),punctuation_uncertain=flag)
        return extract.Line([span],span.bbox)
    lines=[line('1. First item.',40),line('2. Qualified second item.',55,uncertain)]
    region=skeleton.Region(kind='list',lines=lines,list_groups=[[ln] for ln in lines],bbox=(40,40,260,65))
    page=skeleton.PageSkeleton(0,400,600,regions=[region])
    book=assemble.assemble([page],skeleton.BookStyle(body_size=10))
    element=next(e for e in book.pages[0] if e.kind=='list')
    assert element.punctuation_uncertain is uncertain
    assert [assemble.plain_text(r) for r in element.list_items]==[ln.text for ln in lines]
    with pymupdf.open() as doc:
        source=doc.new_page(width=400,height=600)
        for ln in lines:source.insert_text((40,ln.bbox[3]),ln.text,fontsize=10)
        path=tmp_path/'list.epub';build_epub.build(book,str(path),doc=doc,
            page_html={0:build_epub.page_fragment(book,0,book.style)})
        assert build_epub.validate(str(path))==[]
        with zipfile.ZipFile(path) as archive:
            bodies=''.join(archive.read(n).decode() for n in archive.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n))
            assert ('Original punctuation may differ' in bodies) is uncertain
            if uncertain:
                match=re.search(r'original-p0000.xhtml#(text_\d+)',bodies)
                assert match
                original=archive.read('OEBPS/original-p0000.xhtml').decode()
                assert 'id="'+match.group(1)+'"' in original

@pytest.mark.parametrize('sequence',[True,False])
def test_proven_margin_folio_cannot_be_absorbed_into_a_footnote(sequence):
    raws=[]
    for page in range(3):
        body=extract.Line([extract.Span('Ordinary body prose preserves the numeric citation 88 and marker 3.',10,'Times',0,(100,200,430,210))],(100,200,430,210))
        citation=extract.Line([extract.Span('3',5,'Times',1,(180,612,183,617)),
            extract.Span(' A reference, p. 88.',8,'Times',0,(183,613,368,622))],(180,612,368,622))
        folio=extract.Line([extract.Span(str(7+page) if sequence else '42',9,'Times',0,(439,639,449,648))],(439,639,449,648))
        raws.append(extract.RawPage(page,612,792,[extract.Block(0,body.bbox,[body]),
            extract.Block(1,(180,612,449,648),[citation,folio])],[],0))
    style=skeleton.book_style(raws)
    pages=[skeleton.page_skeleton(raw,style) for raw in raws]
    book=assemble.assemble(pages,style,raws)
    assert book.conservation.ok
    assert all('numeric citation 88 and marker 3' in e.text for e in book.elements if e.kind=='p')
    assert len(book.notes)==3 and all(n.num==3 for n in book.notes)
    if sequence:
        assert all(n.text=='A reference, p. 88.' for n in book.notes)
        assert all(str(7+p) in book.furniture for p in range(3))
    else:
        assert '42' not in book.furniture
        assert all(n.text.endswith('88. 42') for n in book.notes)
    # Classification must never rewrite/delete the raw source line.
    assert raws[0].text_blocks[-1].lines[-1].text==('7' if sequence else '42')
