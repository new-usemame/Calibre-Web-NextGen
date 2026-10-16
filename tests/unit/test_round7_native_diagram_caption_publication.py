"""Synthetic text and geometry exercise native caption ownership through public vp.convert.

Prepared native kinds deliberately model the heading/paragraph misclassification.
The public converter must use independent same-leaf SOURCE geometry to repair
only an unambiguous short centered caption immediately below its own artwork.
"""
import copy,json,re,zipfile,hashlib,io
from PIL import Image
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import assemble,build_epub,extract,skeleton,vision_pipeline as vp

LABEL='Aurora Specimen'
NEXT='Borealis Specimen'
BODY='The regular body paragraph retains every independently printed synthetic word.'
CASES=('bold_h','plain_p','above_figure','intervening_prose','large_gap','foreign_leaf','multiple_figures','larger_font','misaligned')

class OfflineReader:
    def __init__(self,root):self.cache=root/'no-model-cache';self.cache.mkdir()
    def read(self,*a,**k):raise AssertionError('Synthetic native fixtures must not dispatch model reads')
    def read_marker(self,*a,**k):raise AssertionError('Synthetic fixtures have no note markers')

def local(node):return node.tag.rsplit('}',1)[-1]
def text(node):return ' '.join(''.join(node.itertext()).split())

def fixture(root,case):
    doc=pymupdf.open();pages={};figures=[];expected={}
    def leaf():
        page=doc.new_page(width=600,height=800);pages[page.number]=[];return page
    def line(page,value,y,kind='p',bold=False,size=12,center=300):
        font='hebo' if bold else 'helv';width=pymupdf.get_text_length(value,fontname=font,fontsize=size)
        page.insert_text((center-width/2,y),value,fontname=font,fontsize=size)
        spans=[s for b in page.get_text('dict')['blocks'] if 'lines' in b for ln in b['lines'] for s in ln['spans'] if s['text']==value]
        assert len(spans)==1
        box=tuple(spans[0]['bbox']);el=assemble.Element(kind,runs=[['t',value,'bold']] if bold else [['t',value]],pno=page.number,bbox=box,line_boxes=[box],level=2 if kind=='h' else 0)
        pages[page.number].append(el);return el
    def diagram(page,box,identity):
        page.draw_rect(box,color=(0,0,0),width=2)
        page.draw_line((box[0]+10,box[1]+15),(box[2]-10,box[3]-15),color=(0,0,0),width=3)
        page.draw_circle(((box[0]+box[2])/2,(box[1]+box[3])/2),min(22,(box[3]-box[1])/5),color=(0,0,0),width=2)
        if identity in ('second','extra'):page.draw_line((box[0]+10,box[3]-15),(box[2]-10,box[1]+15),color=(0,0,0),width=3)
        pages[page.number].append(assemble.Element('fig',pno=page.number,bbox=tuple(box),placed='geometry'))
        figures.append(dict(pno=page.number,bbox=tuple(box),found='native'))
        with Image.open(io.BytesIO(extract.crop_jpeg(doc,page.number,box))) as crop:
            rgb=crop.convert('RGB');expected[identity]=(rgb.size,hashlib.sha256(rgb.tobytes()).hexdigest())
    page=leaf()
    for i in range(7):line(page,BODY+' Row '+str(i)+'.',100+i*24)
    page=leaf();line(page,BODY,60)
    if case=='above_figure':line(page,LABEL,135,'h',True)
    if case=='multiple_figures':
        diagram(page,[90,380,510,410],'first');diagram(page,[90,415,510,435],'extra')
    else:diagram(page,[90,160,510,430],'first')
    if case=='intervening_prose':line(page,'This intervening paragraph separates the genuine heading from the illustration.',450)
    if case!='above_figure' and case!='foreign_leaf':
        y=630 if case=='large_gap' else 469 if case=='intervening_prose' else 450
        line(page,LABEL,y,'p' if case=='plain_p' else 'h',case!='plain_p',24 if case=='larger_font' else 12,95 if case=='misaligned' else 300)
    line(page,'This later paragraph remains independent and complete.',715)
    page=leaf()
    if case=='foreign_leaf':line(page,LABEL,90,'h',True)
    else:line(page,BODY,60)
    diagram(page,[90,160,510,430],'second');line(page,NEXT,450,'caption')
    line(page,'The final paragraph follows the separate second illustration.',650)
    book=assemble.Book(elements=[e for rows in pages.values() for e in rows],pages=pages,figures=figures,style=skeleton.BookStyle(body_size=12,page_count=3))
    assert all(vp.clean_native(doc[n]) for n in range(len(doc)))
    pdf=root/'synthetic-original.pdf';doc.save(pdf)
    (root/'synthetic-prepared-book.json').write_text(json.dumps(dict(pages={str(n):[dict(kind=e.kind,text=e.text,bbox=e.bbox,pages=e.pages,pno=e.pno,level=e.level) for e in rows] for n,rows in pages.items()},figures=figures,body_size=12,case=case),indent=2)+'\n')
    before=copy.deepcopy(book)
    out=root/'actual.epub'
    try:report=vp.convert(doc,str(out),OfflineReader(root),native_prepared=SimpleNamespace(book=book))
    finally:doc.close()
    assert book==before,'Caller-owned cached preparation must remain untouched'
    (root/'actual-report.json').write_text(json.dumps(report,indent=2)+'\n')
    assert build_epub.validate(out)==[], 'Synthetic public output must be a valid EPUB'
    assert report['native_pages']==[0,1,2] and not report['vision_pages'] and not report.get('degraded_units'), 'Classification cannot be rescued with raster fallback'
    with zipfile.ZipFile(out) as z:
        roots=[(n,ET.fromstring(z.read(n))) for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)]
        observed=[]
        for name,tree in roots:
            for fig in tree.iter():
                if local(fig)!='figure':continue
                imgs=[n for n in fig.iter() if local(n)=='img'];assert len(imgs)==1
                src=imgs[0].get('src')
                with Image.open(io.BytesIO(z.read('OEBPS/'+src))) as graphic:
                    rgb=graphic.convert('RGB');matched=[]
                    for identity,(size,source_rgb_sha) in expected.items():
                        if rgb.size==size:decoded=rgb
                        elif rgb.size==(size[0]+16,size[1]+16):
                            # A fixed white border is presentation; every SOURCE pixel must survive.
                            strips=[rgb.crop(b) for b in [(0,0,rgb.width,8),(0,rgb.height-8,rgb.width,rgb.height),(0,8,8,rgb.height-8),(rgb.width-8,8,rgb.width,rgb.height-8)]]
                            assert all(set(strip.getdata())=={(255,255,255)} for strip in strips),'Artwork padding must be white only'
                            decoded=rgb.crop((8,8,rgb.width-8,rgb.height-8))
                        else:continue
                        if hashlib.sha256(decoded.tobytes()).hexdigest()==source_rgb_sha:matched.append(identity)
                    assert len(matched)==1,'Every actual diagram must retain exact decoded original SOURCE pixels with a unique identity'
                    digest=expected[matched[0]]
                observed.append((name,fig,digest))
        assert sum(d==expected['first'] for _,_,d in observed)==1,'First original diagram must remain intact once'
        assert sum(d==expected['second'] for _,_,d in observed)==1,'Second original diagram must remain intact once and distinct'
    return roots,observed,expected

@pytest.mark.parametrize('case',['bold_h','plain_p'])
def test_unique_source_caption_belongs_to_own_diagram_and_cannot_hijack_next_diagram(tmp_path,case):
    roots,figures,digests=fixture(tmp_path,case)
    exact=[(home,n) for home,r in roots for n in r.iter() if local(n) in ('p','h1','h2','h3','h4','figcaption') and text(n)==LABEL]
    assert len(exact)==1,'SOURCE caption must be published exactly once'
    home,cap=exact[0]
    assert local(cap)=='figcaption','Nearby centered own-diagram caption must not become h/chaptertitle or ordinary p'
    own=[(h,fig,d) for h,fig,d in figures if cap in list(fig.iter())]
    assert len(own)==1 and own[0][2]==digests['first'],'Caption must follow its own SOURCE image, never the next leaf diagram'
    assert not any(local(n).startswith('h') and text(n)==LABEL for _,r in roots for n in r.iter())
    next_fig=next(fig for _,fig,d in figures if d==digests['second'])
    assert NEXT in text(next_fig) and LABEL not in text(next_fig),'Next diagram retains separate authored caption identity'
    assert list(own[0][1].iter()).index(cap)>next(i for i,n in enumerate(own[0][1].iter()) if local(n)=='img'),'Caption follows original own-diagram artwork'
    assert not any(local(n)=='h1' and text(n)==LABEL for _,r in roots for n in r.iter()),'No generated chapter hijack'

@pytest.mark.parametrize('case',['above_figure','intervening_prose','large_gap','foreign_leaf','multiple_figures','larger_font','misaligned'])
def test_real_or_unproved_heading_is_preserved_without_guessed_figure_owner(tmp_path,case):
    roots,figures,digests=fixture(tmp_path,case)
    labels=[n for _,r in roots for n in r.iter() if local(n) in ('p','h1','h2','h3','h4','figcaption') and text(n)==LABEL]
    assert len(labels)==1 and local(labels[0]) in ('h1','h2','h3','h4'),'Negative SOURCE geometry must preserve genuine or unproved heading'
    assert not any(local(n)=='figcaption' and LABEL in text(n) for _,r in roots for n in r.iter())
    assert not any(LABEL in text(fig) for _,fig,_ in figures),'Heading must not be guessed into either diagram'
