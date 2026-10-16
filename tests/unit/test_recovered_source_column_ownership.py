"""Synthetic text written for source ownership tests; no OCR or network.

The optical fixture is generated from the same synthetic source word geometry
before rasterizing it. Public tests replace only optical recognition with this
source-bound cache and use a finite local cached answer. Recovery, binding,
compilation, packaging, and link validation are actual producer operations.
"""
import copy,io,json,posixpath,re,zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import vision_pipeline as vp,vision_pixels,build_epub
from cps.services.reflow.source_display import SourceDisplay

def union(words):
    bs=[w['bbox'] for w in words]
    return [min(b[0]for b in bs),min(b[1]for b in bs),max(b[2]for b in bs),max(b[3]for b in bs)]
def block(words,ident,role='body',rect=(520,640)):
    box=union(words);w,h=rect
    return dict(id=ident,role=role,text=' '.join(v['text']for v in words),marker='1' if role=='footnote' else '',bbox=[box[0]/w*1000,box[1]/h*1000,box[2]/w*1000,box[3]/h*1000],low_confidence=[],continues_previous=False,continues_next=False)
def tag(n):return n.tag.rsplit('}',1)[-1]
def authored_tokens(node):
    vals=[]
    def walk(n):
        if tag(n)=='a' and n.get('class')in('source-disclosure-ref','noteref'):return
        if n.text:vals.extend(n.text.split())
        for ch in n:
            walk(ch)
            if ch.tail:vals.extend(ch.tail.split())
    walk(node);return vals

def source_fixture(mode):
    src=pymupdf.open();page=src.new_page(width=520,height=640);groups={}
    def words(name,x,y,count=24):
        dense=mode=='overlap' and name in ('Left','Middle','Right')
        if dense:count=60
        across,pitch,size=(10,14,3)if dense else(2,60,8)
        tokens=[name+str(i).zfill(2)for i in range(count)]
        # Dense source rows reach the existing eight-token line fallback,
        # rather than only harmless two-word swaps in the first fixture.
        for i,t in enumerate(tokens):page.insert_text((x+(i%across)*pitch,y+(i//across)*14),t,fontsize=size,fontname='helv')
        actual={v[4]:dict(text=v[4],bbox=list(v[:4]))for v in page.get_text('words')}
        result=[actual[t]for t in tokens];groups[name]=result;return result
    ha=words('HeaderAlpha',25,35,1);left=words('Left',25,100);middle=words('Middle',185,100);right=words('Right',345,100)
    hb=words('HeaderBeta',345,65,1)
    form=[]
    if mode=='overlap':
        tokens=['Form'+str(i).zfill(2)for i in range(12)]
        for i,t in enumerate(tokens):page.insert_text((25+(i%6)*75,490+(i//6)*14),t,fontsize=8)
        actual={v[4]:dict(text=v[4],bbox=list(v[:4]))for v in page.get_text('words')};form=[actual[t]for t in tokens]
        # Source optical list order can expose two uncovered runs whose union
        # boxes overlap. It does not redefine the declared reading order.
        optical=ha+left+middle+hb+right+form
        answer={'blocks':[block(ha,'ha','heading'),block(left[:8],'healthy'),block(hb,'hb','heading')]}
    else:
        optical=ha+left+middle+right+hb
        answer={'blocks':[block(ha,'ha','heading'),block(left,'healthy'),block(hb,'hb','heading')]}
    expected=left+middle+right+form
    image=page.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False).tobytes('png')
    doc=pymupdf.open();leaf=doc.new_page(width=520,height=640);leaf.insert_image(leaf.rect,stream=image)
    assert leaf.get_text('words')==[],'Public fixture must be image-only, not native text'
    src.close();return doc,optical,answer,[w['text']for w in expected]

class OfflineReader:
    def __init__(self,root,answer):self.cache=root/'offline-cache';self.cache.mkdir();self.answer=answer;self.calls=0
    def read(self,image):
        self.calls+=1
        assert self.calls<=1,'No paid reread/repeated synthetic primary is allowed'
        return dict(answer=copy.deepcopy(self.answer),cached=True,new_cost_usd=0)
    def read_marker(self,*a,**kw):raise AssertionError('No marker/model dispatch permitted')

@pytest.mark.parametrize('mode',['order','overlap'])
def test_public_conversion_recovers_three_proved_columns_once_in_source_order(tmp_path,monkeypatch,mode):
    doc,optical,answer,expected=source_fixture(mode);before=copy.deepcopy(answer)
    monkeypatch.setattr(vision_pixels,'words',lambda *a,**kw:copy.deepcopy(optical))
    reader=OfflineReader(tmp_path,answer);out=tmp_path/'actual.epub'
    try:
        doc.save(tmp_path/'synthetic-original.pdf')
        (tmp_path/'synthetic-source-proof.json').write_text(json.dumps(dict(optical=optical,answer=answer,expected_order=expected),indent=2)+'\n')
        report=vp.convert(doc,str(out),reader)
        assert build_epub.validate(out)==[],'All synthetic publication must remain a valid EPUB'
        assert reader.calls==1 and answer==before,'Healthy cached model answer is immutable; no reread'
        assert report['vision_pages']==[0] and report['native_pages']==[]
        roots={}
        with zipfile.ZipFile(out)as z:
            for name in z.namelist():
                if name.endswith('.xhtml'):roots[name]=ET.fromstring(z.read(name))
        for name,root in roots.items():
            for a in root.iter():
                if tag(a)!='a' or '#'not in a.get('href',''):continue
                leaf,identity=a.get('href').split('#',1);target=posixpath.normpath(posixpath.join(posixpath.dirname(name),leaf))if leaf else name
                assert target in roots and any(n.get('id')==identity for n in roots[target].iter()),'Every Source disclosure and Return target must resolve'
        sequence=[]
        for name,root in roots.items():
            if not re.fullmatch(r'OEBPS/ch\d+\.xhtml',name):continue
            def visit(n,aside=False):
                inside=aside or tag(n)=='aside'
                if tag(n)in('p','h1','h2','h3')and not inside:
                    sequence.extend(t for t in authored_tokens(n)if re.fullmatch(r'(?:Left|Middle|Right|Form)\d+',t))
                for ch in n:visit(ch,inside)
            visit(root)
        (tmp_path/'actual-report.json').write_text(json.dumps(report,indent=2)+'\n')
        (tmp_path/'actual-authored-sequence.json').write_text(json.dumps(sequence)+'\n')
        assert Counter(sequence)==Counter(expected),'Every original SOURCE word instance must occur live exactly once; broad recovery union must not re-admit already owned ink'
        assert sequence==expected,'Persistent proved source gutters require complete left column then middle then right, followed by the separate form'
        healthy=next(b for b in report['pages'][0]['blocks']if b['id']=='healthy')
        assert healthy['published_tokens']==before['blocks'][1]['text'].split()and not healthy['fallback'],'Healthy model BODY words must remain unchanged and independently owned'
    finally:doc.close()

def compile_fixture(tmp_path,optical,answer,source_words=None):
    doc=pymupdf.open();page=doc.new_page(width=520,height=640);display=SourceDisplay(doc,0);pkg=build_epub._Package(str(tmp_path/'compiled.epub'))
    regions={};owners={}
    try:
        bound,_=vp.bind_geometry(answer,optical,display,regions,owners)
        recovered,ids=vp.cover_missing_lines(bound,optical,optical if source_words is None else source_words,display,regions,owners)
        published,report=vp.compile_page(recovered,optical if source_words is None else source_words,display,0,pkg,{},optical,ids,regions,owners)
        return recovered,ids,report,published
    finally:pkg.abandon();doc.close()

def rows(prefix,x,y,count=24):return [dict(text=prefix+str(i).zfill(2),bbox=[x+(i%2)*60,y+(i//2)*14,x+(i%2)*60+42,y+(i//2)*14+9])for i in range(count)]

def test_legitimate_single_column_recovers_tail_without_damaging_healthy_model(tmp_path):
    optical=rows('Single',30,100);answer={'blocks':[block(optical[:8],'healthy')]}
    recovered,ids,report,_=compile_fixture(tmp_path,optical,answer)
    actual=[t for b in report['blocks']for t in b.get('published_tokens',[])]
    assert actual==[w['text']for w in optical],'Ordinary single-column omission remains one ordered live sequence'
    assert next(b for b in report['blocks']if b['id']=='healthy')['published_tokens']==answer['blocks'][0]['text'].split()

def test_original_layer_recovery_excludes_already_owned_model_ink(tmp_path):
    optical=rows('LayerLeft',25,100)+rows('LayerRight',345,100)
    answer={'blocks':[block(optical[:8],'healthy')]}
    _,_,report,_=compile_fixture(tmp_path,optical,answer,source_words=copy.deepcopy(optical))
    actual=[t for b in report['blocks']for t in b.get('published_tokens',[])]
    assert Counter(actual)==Counter(w['text']for w in optical),'Chosen original-layer words cannot re-admit existing covered BODY merely because the recovery union contains it'
    assert actual==[w['text']for w in optical],'Proved disjoint columns retain their declared original-layer order'

@pytest.mark.parametrize('role',['footnote','table'])
def test_explicit_note_or_table_territory_is_not_recovered_as_extra_body(tmp_path,role):
    body=rows('Body',25,100);owned=rows('Owned',345,100)
    answer={'blocks':[block(body,'healthy'),block(owned,'owned',role)]}
    if role=='footnote':answer['blocks'][1]['marker']='1'
    _,ids,report,_=compile_fixture(tmp_path,body+owned,answer)
    assert not ids,'Already owned note/table ink must never be recovered as a BODY/OTHER duplicate'
    assert next(b for b in report['blocks']if b['id']=='healthy')['published_tokens']==[w['text']for w in body]
    assert [b['role']for b in report['blocks']if b['id']=='owned']==[role]

def test_repeated_lexical_words_on_distinct_source_instances_are_preserved(tmp_path):
    optical=rows('Repeated',30,100);optical[12]['text']=optical[2]['text']
    answer={'blocks':[block(optical[:8],'healthy')]}
    _,_,report,_=compile_fixture(tmp_path,optical,answer)
    actual=[t for b in report['blocks']for t in b.get('published_tokens',[])]
    assert actual==[w['text']for w in optical],'Instance reservation cannot deduplicate legitimate repeated lexical values'

def test_gutter_spanning_source_word_is_not_guessed_into_a_neighboring_column(tmp_path):
    healthy=rows('Guard',30,50,8);spanning=[dict(text='Spanning'+str(i).zfill(2),bbox=[30,180+i*14,475,189+i*14])for i in range(18)]
    answer={'blocks':[block(healthy,'healthy')]}
    _,_,report,_=compile_fixture(tmp_path,healthy+spanning,answer)
    actual=[t for b in report['blocks']for t in b.get('published_tokens',[])]
    assert actual==[w['text']for w in healthy+spanning],'A full-width word/row has no persistent vertical gutter; preserve its content without arbitrary column assignment'
