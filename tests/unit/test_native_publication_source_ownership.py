"""Synthetic public-native controls; cached Book is intentionally incomplete/misgrouped.
Text is written for these tests; no OCR, model, SOURCE/scorer or producer edits.
"""
import copy,hashlib,io,json,re,zipfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from PIL import Image
from cps.services.reflow import assemble,build_epub,extract,skeleton,vision_pipeline as vp

class OfflineReader:
    def __init__(self,root):
        self.cache=root/'offline-cache';self.cache.mkdir();self.dispatches=0
    def read(self,*a,**kw):self.dispatches+=1;raise AssertionError('No synthetic native model dispatch permitted')
    def read_marker(self,*a,**kw):self.dispatches+=1;raise AssertionError('No synthetic note marker dispatch permitted')

def tag(n):return n.tag.rsplit('}',1)[-1]
def words(n):return ' '.join(''.join(n.itertext()).split())
def union(boxes):return (min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes))
def lexical(value):return re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*",value)

class Synthetic:
    def __init__(self,root):
        self.root=root;self.doc=pymupdf.open();self.page=self.doc.new_page(width=600,height=800)
        self.rows=[];self.elements=[];self.figures=[];self.expected_text=[];self.expected_art=[]
        for n in range(6):
            row=self.row('Synthetic calibration row '+str(n)+' remains a complete sentence.',100+n*22)
            self.element([row])
    def row(self,value,y,x=70,size=12,bold=False):
        font='hebo' if bold else 'helv'
        self.page.insert_text((x,y),value,fontname=font,fontsize=size)
        spans=[s for b in self.page.get_text('dict')['blocks'] for ln in b.get('lines',[]) for s in ln['spans'] if s['text']==value]
        assert len(spans)==1, 'Every synthetic row must have a unique literal original SOURCE identity'
        r={'text':value,'bbox':tuple(spans[0]['bbox']),'size':size,'font':font,'row':len(self.rows)}
        self.rows.append(r);self.expected_text.append(value);return r
    def element(self,rows,kind='p',column=0):
        runs=[['t',' '.join(r['text'] for r in rows)]]
        e=assemble.Element(kind,runs=runs,pno=0,bbox=union([r['bbox'] for r in rows]),line_boxes=[r['bbox'] for r in rows],column=column,level=2 if kind=='h' else 0)
        self.elements.append(e);return e
    def art(self,box=(90,310,510,505),prepared=False,shape='grid'):
        self.page.draw_rect(box,color=(0,0,0),width=1)
        if shape=='grid':
            for n in range(1,6):
                x=box[0]+(box[2]-box[0])*n/6;self.page.draw_line((x,box[1]),(x,box[3]),color=(0,0,0),width=.8)
            for n in range(1,5):
                y=box[1]+(box[3]-box[1])*n/5;self.page.draw_line((box[0],y),(box[2],y),color=(0,0,0),width=.8)
            self.page.draw_line((box[0],box[3]),(box[2],box[1]),color=(0,0,0),width=2)
        else:
            center=((box[0]+box[2])/2,(box[1]+box[3])/2)
            for radius in (25,50,75):self.page.draw_circle(center,radius,color=(0,0,0),width=1)
            self.page.draw_line((box[0],center[1]),(box[2],center[1]),color=(0,0,0),width=2)
        self.expected_art.append(tuple(box))
        if prepared:
            self.elements.append(assemble.Element('fig',pno=0,bbox=tuple(box),placed='geometry'))
            self.figures.append(dict(pno=0,bbox=tuple(box),found='native'))
    def publish(self):
        assert self.page.get_images(full=True)==[], 'Fixture artwork must be vector-only original SOURCE'
        assert vp.clean_native(self.page),'Synthetic source must enter native path'
        book=assemble.Book(elements=self.elements,pages={0:self.elements},figures=self.figures,style=skeleton.BookStyle(body_size=12,page_count=1))
        before=copy.deepcopy(book);reader=OfflineReader(self.root)
        pdf=self.root/'synthetic-original.pdf';self.doc.save(pdf)
        proof=dict(original_words=[dict(text=w[4],bbox=list(w[:4]),block=w[5],line=w[6],word=w[7]) for w in self.page.get_text('words')],source_rows=self.rows,native_elements=[dict(kind=e.kind,text=e.text,bbox=e.bbox,line_boxes=e.line_boxes,column=e.column,pno=e.pno) for e in self.elements],vector_rects=[list(d['rect']) for d in self.page.get_drawings()],prepared_figures=self.figures,expected_art=self.expected_art)
        (self.root/'source-preparation-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
        out=self.root/'actual.epub';report=vp.convert(self.doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
        assert reader.dispatches==0 and book==before,'Public conversion must not dispatch or mutate cached native preparation'
        (self.root/'actual-report.json').write_text(json.dumps(report,indent=2)+'\n')
        assert build_epub.validate(out)==[],'Containment/publication must remain a valid EPUB'
        assert report['native_pages']==[0] and report['vision_pages']==[],'Control must exercise actual native cached publication'
        with zipfile.ZipFile(out) as z:
            roots=[ET.fromstring(z.read(n)) for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)]
            # Only authored reading content; optional source asides are a separate lane.
            readpars=[];figs=[];asides=[]
            def visit(node,in_aside=False):
                inside=in_aside or tag(node)=='aside'
                if tag(node)=='aside':asides.append(node)
                if tag(node)=='figure' and not inside:figs.append(node)
                if tag(node) in ('p','h1','h2','h3','h4','figcaption') and not inside:readpars.append(node)
                for child in node:visit(child,inside)
            for r in roots:visit(r)
            actual=' '.join(words(n) for n in readpars)
            authored=' '.join(words(n) for p in readpars for n in p.iter() if n.get('data-source-page')=='0')
            assert Counter(lexical(authored))==Counter(lexical(' '.join(self.expected_text))), 'All real SOURCE prose words must remain live exactly once, no invented labels or duplicates'
            images={n:z.read(n) for n in z.namelist() if n.startswith('OEBPS/images/')}
        return SimpleNamespace(report=report,roots=roots,paragraphs=readpars,figures=figs,asides=asides,images=images,actual=actual)
    def close(self):self.doc.close()

def image_rgb_equal_to_source(f,result,src,box):
    raw=result.images['OEBPS/'+src]
    with Image.open(io.BytesIO(raw)) as im:rgb=im.convert('RGB')
    with Image.open(io.BytesIO(extract.crop_jpeg(f.doc,0,box))) as im:source=im.convert('RGB')
    if rgb.size==(source.width+16,source.height+16):
        strips=[rgb.crop(b) for b in [(0,0,rgb.width,8),(0,rgb.height-8,rgb.width,rgb.height),(0,8,8,rgb.height-8),(rgb.width-8,8,rgb.width,rgb.height-8)]]
        assert all(set(s.getdata())=={(255,255,255)} for s in strips)
        rgb=rgb.crop((8,8,rgb.width-8,rgb.height-8))
    assert rgb.size==source.size and rgb.tobytes()==source.tobytes(),'Published graphic must contain exact decoded original crop pixels; no painting or guessed reconstruction'

@pytest.mark.parametrize('shape',['grid','circles'])
def test_missing_cached_native_vector_art_is_published_complete_with_no_body_inside_crop(tmp_path,shape):
    f=Synthetic(tmp_path)
    try:
        before=f.row('This paragraph introduces the independent vector specimen.',280);f.element([before])
        f.art(shape=shape)
        after=f.row('The final paragraph remains live below the complete specimen.',550);f.element([after])
        r=f.publish()
        assert len(r.figures)==1,'Original vector-only artwork omitted from cached Book must still appear once in reading flow'
        img=next(n for n in r.figures[0].iter() if tag(n)=='img');src=img.get('src')
        record=r.report['images'][src];box=record['bbox'];original=f.expected_art[0]
        assert record['page']==0 and all(box[i]<=original[i] if i<2 else box[i]>=original[i] for i in range(4)),'Recovered crop must contain entire original vector extent'
        assert not any(pymupdf.Rect(box).intersects(pymupdf.Rect(row['bbox'])) for row in f.rows),'Vector crop must never consume readable original BODY'
        image_rgb_equal_to_source(f,r,src,box)
    finally:f.close()

@pytest.mark.parametrize('decoration',['underline','page_border'])
def test_body_rule_or_page_border_is_not_guessed_to_be_missing_artwork(tmp_path,decoration):
    f=Synthetic(tmp_path)
    try:
        row=f.row('This authored sentence remains ordinary prose with a decorative rule.',300);f.element([row])
        if decoration=='underline':f.page.draw_line((70,304),(430,304),color=(0,0,0),width=.8)
        else:f.page.draw_rect((20,20,580,780),color=(0,0,0),width=.8)
        r=f.publish();assert not r.figures,'Unproved decoration or page border must not become a guessed primary figure'
    finally:f.close()

def test_vector_overlapping_readable_body_requires_unpainted_original_context_not_guessed_crop(tmp_path):
    f=Synthetic(tmp_path)
    try:
        f.art(box=(90,310,510,505))
        row=f.row('This readable paragraph occupies the interior of ambiguous vector geometry.',380,x=105);f.element([row])
        r=f.publish()
        assert any(any(tag(n)=='img' for n in a.iter()) for a in r.asides),'Unproved separation must retain explicit optional whole original SOURCE context'
        assert not r.figures,'Overlapping readable BODY cannot be erased or included in a guessed primary art crop'
        candidates=[(src,rec) for src,rec in r.report['images'].items() if rec.get('page')==0 and rec.get('bbox')==[0.0,0.0,600.0,800.0]]
        assert candidates,'Unproved vector/body separation must own full original page identity'
        assert any('source' in (n.get('class','')) and n.get('href') for root in r.roots for n in root.iter() if tag(n)=='a'),'Whole SOURCE context requires a working source-control target'
    finally:f.close()

CONTINUOUS={'upper':('The observer can locate the','Workbench by following the printed direction.'),'lower':('The observer can locate the','shelfedge by following the printed direction.'),'parenthetical':('The observer consults the panel','(shown here), and continues the same explanation.')}
@pytest.mark.parametrize('case',list(CONTINUOUS))
def test_source_proven_unindented_same_page_fragments_are_one_paragraph(tmp_path,case):
    f=Synthetic(tmp_path)
    try:
        left,right=CONTINUOUS[case];a=f.row(left,320);b=f.row(right,340);c=f.row('Additional explanation belongs to this continuous paragraph.',360)
        f.element([a]);f.element([b,c]);r=f.publish()
        joined=left+' '+right+' '+c['text']
        assert sum(tag(n)=='p' and words(n)==joined for n in r.paragraphs)==1,'Exact adjacent original unindented same-column rows must stay one continuous paragraph, including uppercase or wrapper opening'
    finally:f.close()

@pytest.mark.parametrize('case',['indent','terminal','heading','large_gap','foreign_column','art_paragraph_boundary'])
def test_true_paragraph_heading_column_or_art_boundary_is_not_joined(tmp_path,case):
    f=Synthetic(tmp_path)
    try:
        left='The observer retains the';right='following account with its own source boundary.'
        if case in ('terminal','art_paragraph_boundary'):left='The observer finishes this complete sentence.';right='This next account has its own source boundary.'
        if case=='foreign_column':right='following independent column account.'
        a=f.row(left,320);f.element([a])
        if case=='art_paragraph_boundary':f.art(box=(90,350,510,500),prepared=True)
        y=540 if case=='art_paragraph_boundary' else 430 if case=='large_gap' else 340
        x=94 if case in ('indent','art_paragraph_boundary') else 350 if case=='foreign_column' else 70
        b=f.row(right,y,x=x,bold=case=='heading');c=f.row('This own row stays here.' if case=='foreign_column' else 'The independently authored following row stays with its own block.',y+20,x=x)
        f.element([b,c],kind='h' if case=='heading' else 'p',column=1 if case=='foreign_column' else 0)
        r=f.publish()
        assert not any(left+' '+right in words(n) for n in r.paragraphs),'Source-proven structural boundary must not be collapsed by lexical resemblance'
        assert any(words(n)==left for n in r.paragraphs) and any(words(n)==right+' '+c['text'] for n in r.paragraphs)
        if case=='heading':assert any(tag(n).startswith('h') and words(n)==right+' '+c['text'] for n in r.paragraphs)
        if case=='art_paragraph_boundary':assert len(r.figures)==1,'Healthy native original figure stays intact once between separate authored paragraphs'
    finally:f.close()

@pytest.mark.parametrize('case',['three_middle_rows','one_middle_row'])
def test_noncontiguous_cached_element_ownership_cannot_reorder_original_prose_rows(tmp_path,case):
    f=Synthetic(tmp_path)
    try:
        lines=['The synthetic account begins with an unfinished explanation','and introduces the first intermediate source row','before continuing through the second intermediate source row','and then the third intermediate source row','while the later source row develops the same account','until the final source row completes the explanation.']
        rows=[f.row(value,310+i*20) for i,value in enumerate(lines)]
        interior=rows[1:4] if case=='three_middle_rows' else rows[2:3]
        remaining=[r for r in rows if r not in interior]
        f.element(remaining);f.element(interior);r=f.publish()
        exact=' '.join(lines)
        assert sum(tag(n)=='p' and words(n)==exact for n in r.paragraphs)==1,'Source rows with interleaved cached owners must publish in literal physical order once, never outer-owner-first then middle-owner-tail'
    finally:f.close()

@pytest.mark.parametrize('case',['two_columns','true_indented_paragraphs'])
def test_physical_row_reordering_does_not_flatten_columns_or_authored_paragraphs(tmp_path,case):
    f=Synthetic(tmp_path)
    try:
        if case=='two_columns':
            left=[f.row('Left column synthetic row '+str(i)+'.',310+i*20,x=60) for i in range(3)]
            right=[f.row('Right column synthetic row '+str(i)+'.',310+i*20,x=335) for i in range(3)]
            f.element(left,column=0);f.element(right,column=1)
        else:
            left=[f.row('First paragraph begins here.',310,x=94),f.row('Its unindented continuation remains here.',330)]
            right=[f.row('second paragraph starts independently.',350,x=94),f.row('Its subsequent source row remains here.',370)]
            f.element(left);f.element(right)
        r=f.publish()
        assert any(words(n)==' '.join(x['text'] for x in left) for n in r.paragraphs)
        assert any(words(n)==' '.join(x['text'] for x in right) for n in r.paragraphs)
        assert not any(' '.join(x['text'] for x in left+right)==words(n) for n in r.paragraphs),'Column and original indent proof must protect independent authored paragraph ownership'
    finally:f.close()
