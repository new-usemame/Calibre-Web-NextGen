"""Public cached TABLE controls: exact printed cells, source-grid proof, no model/OCR."""
import copy,hashlib,io,json,re,zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
import pymupdf,pytest
from PIL import Image
from cps.services.reflow import vision_pipeline as vp,vision_pixels,build_epub
BODY='The independent healthy account remains live exactly once.'
GRID=(80.,250.,520.,410.)
CELLS=(('Amber','17','East'),('Azure','29','West'),('Copper','31','North'),('Silver','43','South'))
def tag(n):return n.tag.rsplit('}',1)[-1]
def plain(n):return ' '.join(''.join(n.itertext()).split())
def block(ident,role,text,box):
    return dict(id=ident,role=role,text=text,marker='',bbox=[box[0]/600*1000,box[1]/800*1000,box[2]/600*1000,box[3]/800*1000],low_confidence=[],continues_previous=False,continues_next=False)

def publish(tmp_path,monkeypatch,case):
    original=pymupdf.open();page=original.new_page(width=600,height=800)
    page.insert_text((70,100),BODY,fontsize=12)
    bodybox=list(page.get_text('words')[0][:4]);bodybox=[68,85,540,105]
    rows=CELLS if case!='numeric' else (('11','17','23'),('29','31','37'),('41','43','47'),('53','59','61'))
    if case=='prose_not_grid':
        text='The printed prose describes a specimen without data cells.'
        page.insert_text((95,300),text,fontsize=12);tabletext=text
    else:
        page.draw_rect(GRID,color=(0,0,0),width=1)
        for x in (220,370):page.draw_line((x,250),(x,410),color=(0,0,0),width=1)
        for y in (290,330,370):page.draw_line((80,y),(520,y),color=(0,0,0),width=1)
        for i,row in enumerate(rows):
            for j,text in enumerate(row):page.insert_text(((95,235,385)[j],276+i*40),text,fontsize=12)
        tabletext=('\n' if case=='linebreaks' else ' ').join(' '.join(row) for row in rows)
    foreign='This foreign readable paragraph keeps its independent source owner.'
    if case=='foreign_body':
        page.insert_text((90,348),foreign,fontsize=10)
    if case=='descriptive_alt':tabletext='A diagram summarizing amber readings beside the western specimen.'
    if case=='changed_cell':tabletext=tabletext.replace('Copper','Inventedlabel')
    optical=[dict(text=w[4],bbox=list(w[:4]),confidence=99,source_row=[w[5],w[6]],source_word=w[7]) for w in page.get_text('words',sort=True)]
    answer=dict(blocks=[block('b1','body',BODY,bodybox),block('t1','table',tabletext,GRID)])
    if case=='foreign_body':
        ws=[w for w in optical if 337<w['bbox'][1]<349];box=[min(w['bbox'][0] for w in ws),min(w['bbox'][1] for w in ws),max(w['bbox'][2] for w in ws),max(w['bbox'][3] for w in ws)]
        answer['blocks'].insert(1,block('b2','body',foreign,box))
    # Pixel-only source is actual original fixture: optical witnesses are native
    # construction coordinates, never OCR output or response-derived words.
    pix=page.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False)
    sourcepng=pix.tobytes('png');(tmp_path/'synthetic-original.png').write_bytes(sourcepng);original.save(tmp_path/'synthetic-vector-construction.pdf')
    doc=pymupdf.open();scan=doc.new_page(width=600,height=800);scan.insert_image(scan.rect,stream=sourcepng)
    assert scan.get_text('words')==[] and not vp.clean_native(scan),'Actual public route must be pixel publication, not native Book/table shortcut'
    doc.save(tmp_path/'synthetic-image-only-source.pdf')
    (tmp_path/'source-grid-word-and-cached-answer-proof.json').write_text(json.dumps(dict(case=case,grid_bbox=GRID,rows=rows,optical_words=optical,answer=answer,source_image_sha256=hashlib.sha256(sourcepng).hexdigest(),original_vector_drawings=[list(d['rect']) for d in page.get_drawings()]),indent=2)+'\n')
    class Reader:
        cache=tmp_path/'offline-cache'/'readings'
        primary=0;targeted=0
        def read(self,image):
            self.primary+=1;return dict(answer=copy.deepcopy(answer),cached=True,new_cost_usd=0.,config_sha256='synthetic-primary')
        def reread(self,image):
            self.targeted+=1;return dict(answer=copy.deepcopy(answer),cached=True,new_cost_usd=0.,config_sha256='synthetic-r2')
        def read_marker(self,*args,**kwargs):raise AssertionError('No marker request exists in synthetic TABLE controls')
    reader=Reader();reader.cache.mkdir(parents=True)
    monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(optical))
    out=tmp_path/'actual.epub'
    try:
        report=vp.convert(doc,str(out),reader)
        assert reader.primary==1 and reader.targeted<=1 and report['native_pages']==[] and report['vision_pages']==[0]
        assert build_epub.validate(out)==[],'Synthetic table publication must remain valid EPUB'
        (tmp_path/'actual-report.json').write_text(json.dumps(report,indent=2)+'\n')
        with zipfile.ZipFile(out) as z:
            roots=[ET.fromstring(z.read(n)) for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)]
            reading=[];figures=[];asides=[]
            def visit(node,inside=False):
                inside=inside or tag(node)=='aside'
                if tag(node)=='aside':asides.append(node)
                if not inside and tag(node)=='figure':figures.append(node)
                if not inside and tag(node) in ('p','h1','h2','h3','figcaption','td','th'):reading.append(node)
                for c in node:visit(c,inside)
            for root in roots:visit(root)
            visible=' '.join(plain(n) for n in reading)
            assert visible.count(BODY)==1,'Independent readable BODY survives table handling exactly once'
            if case=='foreign_body':assert visible.count(foreign)==1,'A separately proved readable BODY row within an art hint cannot become cell data or disappear'
            images={n:z.read(n) for n in z.namelist() if n.startswith('OEBPS/images/')}
        return dict(report=report,roots=roots,reading=reading,figures=figures,asides=asides,visible=visible,images=images,cells=[v for row in rows for v in row],tabletext=tabletext)
    finally:doc.close();original.close()

@pytest.mark.parametrize('case',['flat','linebreaks','numeric'])
def test_exact_source_grid_cell_text_is_live_once_and_original_table_is_retained(tmp_path,monkeypatch,case):
    r=publish(tmp_path,monkeypatch,case)
    actual=Counter(r['visible'].split());expected=Counter(r['cells'])
    assert all(actual[word]==count for word,count in expected.items()),'Exact printed cached TABLE cells must exist as live XHTML text once; img.alt and optional SOURCE image cannot satisfy live cell publication'
    assert len(r['figures'])==1,'Readable original ruled table stays in reading flow exactly once alongside live source cells'
    img=next(n for n in r['figures'][0].iter() if tag(n)=='img');rec=r['report']['images'][img.get('src')];box=rec['bbox']
    assert rec['page']==0 and rec['kind']=='table' and all(box[i]<=GRID[i]+1 if i<2 else box[i]>=GRID[i]-1 for i in range(4)),'The source table image must retain complete original grid extent and owner'
    picture=Image.open(io.BytesIO(r['images']['OEBPS/'+img.get('src')])).convert('L')
    assert picture.getextrema()[0]<50 and picture.width>600 and picture.height>200,'Source table image remains a substantive unpainted graphic'

@pytest.mark.parametrize('case',['descriptive_alt','prose_not_grid','changed_cell'])
def test_accessibility_description_unruled_prose_or_nonmatching_cells_are_not_guessed_live_data(tmp_path,monkeypatch,case):
    r=publish(tmp_path,monkeypatch,case)
    assert not any(tag(n) in ('td','th') for root in r['roots'] for n in root.iter()),'No grid/text proof means no invented live table-cell structure'
    if case=='descriptive_alt':assert 'diagram summarizing' not in r['visible'],'Accessibility description cannot be blindly promoted into authored table data'
    if case=='changed_cell':assert 'Inventedlabel' not in r['visible'],'A mismatching cached cell cannot override original printed text'
    assert r['figures'] or any(any(tag(n)=='img' for n in a.iter()) for a in r['asides']),'Unproved table text must retain original source imagery'

def test_foreign_owned_body_in_table_hint_requires_explicit_whole_source_context(tmp_path,monkeypatch):
    r=publish(tmp_path,monkeypatch,'foreign_body')
    nodes={n.get('id'):n for root in r['roots'] for n in root.iter() if n.get('id')}
    links=[n for root in r['roots'] for n in root.iter() if tag(n)=='a' and 'source-disclosure-ref' in n.get('class','')]
    assert links,'Readable foreign BODY inside table hint must force explicit original SOURCE context, never blindly duplicate it as table data'
    assert any(c.get('block')=='t1' and c.get('status')=='DEGRADED_SOURCE_CONTEXT' for c in r['report'].get('degraded_units',[]))
    for link in links:
        target=nodes.get(link.get('href','').split('#')[-1]);assert target is not None
        assert any(tag(n)=='img' for n in target.iter())
        assert any(tag(n)=='a' and 'Return' in plain(n) and n.get('href','').split('#')[-1]==link.get('id') for n in target.iter())
