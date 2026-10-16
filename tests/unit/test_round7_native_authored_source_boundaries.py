"""Synthetic originals: authored native boundaries survive public conversion.

Fixtures are deliberately cached as ordinary p elements. Original ink, typography,
row cadence and table topology provide the boundary evidence. Text and geometry
are written for these tests; no OCR, provider calls or measurement contracts are used.
"""
import copy,json,re,zipfile
from collections import Counter
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import assemble,build_epub,skeleton,vision_pipeline as vp

def tag(n):return n.tag.rsplit('}',1)[-1]
def plain(n):return ' '.join(''.join(n.itertext()).split())
def union(boxes):return(min(b[0]for b in boxes),min(b[1]for b in boxes),max(b[2]for b in boxes),max(b[3]for b in boxes))
def tokens(text):return re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*",text)

class Original:
    def __init__(self,tmp_path,pages=1,pitch=14):
        self.root=tmp_path;self.doc=pymupdf.open();self.pages={};self.rows=[];self.expected=[];self.notes=[]
        for pno in range(pages):
            self.doc.new_page(width=600,height=800);self.pages[pno]=[]
            calibration=[]
            for i in range(6):
                text='Calibration leaf '+str(pno)+' physical row '+str(i)+' develops the original account'
                if i==5:text+='.'
                calibration.append(self.row(pno,[['t',text]],70,(200 if pno else 100)+i*pitch))
            self.element(pno,calibration)
    def row(self,pno,runs,x,y,size=10):
        boxes=[];xx=x;drawing=[]
        # PyMuPDF coalesces adjacent same-font spans: draw that same physical
        # string once rather than assuming separate get_text span identities.
        for atom in runs:
            if drawing and atom[0]=='t'and drawing[-1][0]=='t'and atom[2:]==drawing[-1][2:]:drawing[-1][1]+=atom[1]
            else:drawing.append(copy.deepcopy(atom))
        for run in drawing:
            value=run[1];font='hebo'if len(run)>2 and run[2]=='bold'else'helv'
            raised=run[0]in('raised','sup');fs=6 if raised else size;yy=y-4 if raised else y
            self.doc[pno].insert_text((xx,yy),value,fontname=font,fontsize=fs)
            width=pymupdf.get_text_length(value,fontname=font,fontsize=fs)
            # Use the exact span at its printed origin; construction does not
            # obtain typography or lexical evidence from a producer helper.
            hits=[s for b in self.doc[pno].get_text('dict')['blocks']for ln in b.get('lines',[])for s in ln['spans']if s['text']==value and abs(s['origin'][0]-xx)<.02 and abs(s['origin'][1]-yy)<.02]
            assert len(hits)==1,'Synthetic literal span must be physically unique'
            boxes.append(tuple(hits[0]['bbox']));xx+=width
        text=' '.join(''.join(r[1]for r in runs).split());r=dict(pno=pno,text=text,bbox=union(boxes),runs=copy.deepcopy(runs),size=size)
        self.rows.append(r);self.expected.append(text);return r
    def fields(self,pno,texts,y):
        rows=[self.row(pno,[['t',text]],70+110*i,y)for i,text in enumerate(texts)]
        return dict(pno=pno,text=' '.join(texts),bbox=union([r['bbox']for r in rows]),runs=[['t',' '.join(texts)]],size=10)
    def element(self,pno,rows,kind='p'):
        runs=[]
        for i,r in enumerate(rows):
            if i:runs.append(['t',' '])
            runs+=copy.deepcopy(r['runs'])
        e=assemble.Element(kind,runs=runs,pno=pno,bbox=union([r['bbox']for r in rows]),line_boxes=[r['bbox']for r in rows],column=0,level=2 if kind=='h'else 0)
        self.pages[pno].append(e);return e
    def publish(self):
        elements=[e for pno in range(len(self.pages))for e in self.pages[pno]]
        book=assemble.Book(elements=elements,pages=self.pages,notes=self.notes,style=skeleton.BookStyle(body_size=10,page_count=len(self.pages)))
        before=copy.deepcopy(book)
        class Offline:
            def __init__(self,root):self.cache=root/'offline-cache';self.cache.mkdir();self.dispatches=0
            def read(self,*a,**k):self.dispatches+=1;raise AssertionError('No native model dispatch permitted')
            def read_marker(self,*a,**k):self.dispatches+=1;raise AssertionError('No native marker dispatch permitted')
        reader=Offline(self.root);out=self.root/'actual.epub';self.doc.save(self.root/'synthetic-original.pdf')
        assert all(vp.clean_native(self.doc[p])for p in range(len(self.pages)))
        (self.root/'synthetic-proof.json').write_text(json.dumps(dict(rows=self.rows,native_elements=[dict(pno=e.pno,kind=e.kind,runs=e.runs,bbox=e.bbox,line_boxes=e.line_boxes)for e in elements]),indent=2))
        report=vp.convert(self.doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
        assert reader.dispatches==0 and book==before,'Cached original preparation remains immutable; no network or model route'
        assert build_epub.validate(out)==[],'Public converter must complete a valid EPUB'
        assert report['native_pages']==list(range(len(self.pages)))and report['vision_pages']==[]
        (self.root/'actual-report.json').write_text(json.dumps(report,indent=2))
        with zipfile.ZipFile(out)as z:docs={n:ET.fromstring(z.read(n))for n in z.namelist()if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)}
        paragraphs=[]
        def visit(n,aside=False):
            aside=aside or tag(n)=='aside'
            if not aside and tag(n)in('p','li','h1','h2','h3','h4'):paragraphs.append(n)
            for c in n:visit(c,aside)
        for root in docs.values():visit(root)
        # SOURCE carriers exclude generated Return/context labels and footer
        # notes. Compare all physical ordinary-word occurrences, not set equality.
        carriers=[n for p in paragraphs for n in p.iter()if n.get('data-source-page')is not None]
        authored=' '.join(plain(n)for n in carriers)
        assert Counter(tokens(authored))==Counter(tokens(' '.join(self.expected))),'Every original ordinary word must stay live exactly once'
        return SimpleNamespace(paragraphs=paragraphs,docs=docs,report=report)
    def close(self):self.doc.close()

@pytest.mark.parametrize('bold',[True,False])
def test_authored_definition_gaps_preserve_each_original_paragraph_through_public_repairs(tmp_path,bold):
    f=Original(tmp_path)
    try:
        intro=f.row(0,[['t','The separately authored introduction is complete.']],70,270);f.element(0,[intro])
        definitions=[]
        for i,label in enumerate(('Amber','Birch','Cedar','Dune','Elm','Fern','Grove')):
            runs=[['t',label+':','bold']]if bold else[['t',label+':']]
            runs.append(['t',' independent definition '+str(i)+' describes its own category'])
            r=f.row(0,runs,70,310+i*27);f.element(0,[r]);definitions.append(r)
        end=f.row(0,[['t','A separately authored closing account is complete.']],70,535);f.element(0,[end])
        r=f.publish()
        assert all(sum(tag(p)=='p'and plain(p)==q['text']for p in r.paragraphs)==1 for q in definitions),'Authored paragraph gaps cannot collapse distinct definition paragraphs, even when cached kind is p'
        if bold:assert all(sum(tag(n)=='strong'and plain(n)==q['text'].split()[0]for p in r.paragraphs for n in p.iter())==1 for q in definitions),'Each short original bold label retains its own style exactly once'
    finally:f.close()

@pytest.mark.parametrize('ruled',[True,False])
def test_native_aligned_table_rows_do_not_become_next_leaf_prose(tmp_path,ruled):
    f=Original(tmp_path,pages=2)
    try:
        table=[]
        for i in range(4):
            r=f.fields(0,['Region'+str(i),'Kind'+str(i),'Value'+str(i),'Class'+str(i)],690+i*18);f.element(0,[r]);table.append(r)
        if ruled:
            for x in (65,175,285,395,505):f.doc[0].draw_line((x,673),(x,758),color=(0,0,0),width=.7)
            for y in (673,697,715,733,758):f.doc[0].draw_line((65,y),(505,y),color=(0,0,0),width=.7)
        opening=f.row(1,[['t','the following ordinary narrative belongs to a separate paragraph']],70,70)
        closing=f.row(1,[['t','and ends this independently authored prose account.']],70,84);f.element(1,[opening,closing]);f.pages[1].insert(0,f.pages[1].pop())
        r=f.publish();exact=opening['text']+' '+closing['text']
        assert sum(tag(p)=='p'and plain(p)==exact for p in r.paragraphs)==1,'Repeated aligned original table fields are a structural boundary before ordinary next-leaf prose'
        assert not any(opening['text']in plain(p)and any(q['text']in plain(p)for q in table)for p in r.paragraphs),'Native table cells/rows cannot fuse into the following prose parent'
    finally:f.close()

def test_centered_larger_bold_title_footer_does_not_join_next_leaf_publisher_lines(tmp_path):
    f=Original(tmp_path,pages=2)
    try:
        heading=f.row(0,[['t','An Independent Synthetic Volume','bold']],115,400,20);f.element(0,[heading],kind='h')
        year=f.row(0,[['t','2047','bold']],286,735,14);f.element(0,[year])
        publishers=[]
        for i,text in enumerate(('North Harbor Press','Cedar Publishing House','Seventy Lantern Road')):
            row=f.row(1,[['t',text]],70,70+i*27);f.element(1,[row]);publishers.append(row)
        f.pages[1]=f.pages[1][-3:]+f.pages[1][:-3]
        r=f.publish()
        assert sum(tag(p)=='p'and plain(p)==year['text']for p in r.paragraphs)==1,'An isolated centered larger all-bold title/footer is not an unfinished prose sentence'
        assert all(sum(tag(p)=='p'and plain(p)==q['text']for p in r.paragraphs)==1 for q in publishers),'Distinct publisher/address lines keep their printed authored boundaries'
        assert not any(year['text']in plain(p)and publishers[0]['text']in plain(p)for p in r.paragraphs)
    finally:f.close()

@pytest.mark.parametrize('pitch',[14,27])
def test_genuine_regular_and_double_spaced_running_prose_still_joins(tmp_path,pitch):
    f=Original(tmp_path,pitch=pitch)
    try:
        rows=[f.row(0,[['t',t]],70,310+i*pitch)for i,t in enumerate(('The original ordinary account continues without termination','while its next physical row develops the same argument','until the final original row completes this explanation.'))]
        for q in rows:f.element(0,[q])
        r=f.publish();exact=' '.join(q['text']for q in rows)
        assert sum(tag(p)=='p'and plain(p)==exact for p in r.paragraphs)==1,'Habitual same-territory running-prose cadence can be double-spaced; paragraph-gap proof must retain genuine continuation'
        assert r.report['degraded_unit_count']==0
    finally:f.close()

def test_same_row_inline_bold_continuation_keeps_one_parent_and_style(tmp_path):
    f=Original(tmp_path)
    try:
        left=f.row(0,[['t','The observer keeps an '],['t','inline emphasis','bold']],70,310)
        right=f.row(0,[['t',' while following the same original account']],left['bbox'][2],310)
        last=f.row(0,[['t','and closes this uninterrupted physical paragraph.']],70,324)
        for q in(left,right,last):f.element(0,[q])
        r=f.publish();exact=' '.join((left['text'],right['text'].strip(),last['text']))
        assert sum(tag(p)=='p'and plain(p)==exact for p in r.paragraphs)==1,'Inline bold does not itself prove an authored paragraph boundary'
        assert sum(tag(n)=='strong'and plain(n)=='inline emphasis'for p in r.paragraphs for n in p.iter())==1
    finally:f.close()

def test_genuine_raised_ordinal_and_printed_note_continuation_keeps_own_return(tmp_path):
    f=Original(tmp_path)
    try:
        a=f.row(0,[['t','The observer describes the ']],70,310)
        b=f.row(0,[['t','1'],['raised','st'],['t',' position within the same ordinary account']],70,324)
        c=f.row(0,[['t','and completes the explanation'],['sup','1',0],['t','.']],70,338)
        for q in(a,b,c):f.element(0,[q])
        note='The independently printed footer explains the account.'
        f.doc[0].insert_text((70,740),'1 '+note,fontsize=9)
        nb=f.doc[0].search_for('1 '+note)[0];f.notes=[assemble.Note(1,note,0,bbox=tuple(nb))]
        r=f.publish();exact=' '.join(q['text']for q in(a,b,c));owners=[p for p in r.paragraphs if tag(p)=='p'and plain(p)==exact]
        assert len(owners)==1,'Typed raised and note atoms retain genuine continuous paragraph ownership'
        refs=[n for n in owners[0].iter()if tag(n)=='a'and'noteref'in n.get('class','').split()];assert len(refs)==1 and plain(refs[0])=='1'
        target=refs[0].get('href').split('#')[-1];targets=[n for root in r.docs.values()for n in root.iter()if n.get('id')==target];assert len(targets)==1
        assert any(tag(n)=='a'and(n.get('href')or'').split('#')[-1]==refs[0].get('id')for n in targets[0].iter()),'Actual own note retains its exact Return'
        assert sum(tag(n)=='sup'and plain(n)=='st'for n in owners[0].iter())==1
    finally:f.close()

def test_genuine_unfinished_page_turn_still_has_one_paragraph(tmp_path):
    f=Original(tmp_path,pages=2)
    try:
        a=f.row(0,[['t','The independent observer continues the']],70,740);f.element(0,[a])
        b=f.row(1,[['t','same original explanation across this physical leaf']],70,70);c=f.row(1,[['t','until the final source row completes its sentence.']],70,84);f.element(1,[b,c]);f.pages[1].insert(0,f.pages[1].pop())
        r=f.publish();exact=' '.join(q['text']for q in(a,b,c))
        assert sum(tag(p)=='p'and plain(p)==exact for p in r.paragraphs)==1,'Structural fences must retain true unfinished source-proved page-turn continuation'
    finally:f.close()

@pytest.mark.parametrize('barrier',['terminal','indent'])
def test_true_complete_or_indented_paragraph_remains_separate(tmp_path,barrier):
    f=Original(tmp_path)
    try:
        a=f.row(0,[['t','The preceding authored account is complete.'if barrier=='terminal'else'The preceding authored account remains open']],70,310);f.element(0,[a])
        b=f.row(0,[['t','another independently authored account starts here']],90 if barrier=='indent'else 70,324);c=f.row(0,[['t','and completes its own original paragraph.']],70,338);f.element(0,[b,c])
        r=f.publish();assert any(plain(p)==a['text']for p in r.paragraphs)
        assert not any(a['text']in plain(p)and b['text']in plain(p)for p in r.paragraphs),'Terminal punctuation and genuine paragraph indent remain structural barriers'
    finally:f.close()
