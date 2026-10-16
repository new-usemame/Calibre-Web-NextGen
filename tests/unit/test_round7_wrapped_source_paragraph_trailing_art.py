"""Synthetic original paragraph rows surround trailing publication artwork.

The illustration's publication position must not manufacture a sentence end.
Source geometry, intact originals and disclosure Returns remain independent.
"""
from tests.fixtures.reflow_output_contracts import has_authored_substring
import copy,io,zipfile
import pytest
from PIL import Image
from tests.unit.round7_recovery_test_support import h

FIRST='The amber travellers carried the clear stones'
LAST='and followed the winding river with the'
LEFT=FIRST+' '+LAST
RIGHT='patient silver workers before the morning light.'
LATER='A separate violet conclusion remains complete.'
CAPTION='An amber circular diagram.'


def pages_for(case):
    a=h.InkPage();b=h.InkPage()
    x=170 if case=='foreign-leaf' else 24
    tail=LAST+'.' if case=='terminal' else LAST
    a.text(FIRST,'left',x=x,y=205 if case=='not-surrounded' else 55,size=6,model=FIRST+' '+tail,next=True)
    a.page.insert_text((x,235),tail,fontsize=6)
    a.blocks[-1]['bbox'][3]=239/h.H*1000
    if case=='foreign-leaf':a.blocks[-1]['bbox'][2]=282/h.W*1000
    rect=[35,90,130,175] if case=='foreign-leaf' else [105,90,260,175]
    a.page.draw_rect(rect,color=(0,0,0),fill=(.85,.85,.85),width=2)
    a.page.draw_line((rect[0],rect[1]),(rect[2],rect[3]),color=(0,0,0),width=2)
    for r in ([rect[0],90,rect[0]+8,98],[rect[2]-8,90,rect[2],98],[rect[0],167,rect[0]+8,175],[rect[2]-8,167,rect[2],175]):
        a.page.draw_rect(r,color=(0,0,1),fill=(0,0,1))
    if case=='source-context':
        # A source stroke extends into the paragraph's physical territory;
        # disclosure must preserve it rather than invent a clean crop.
        a.page.draw_line((rect[0]+10,90),(rect[0]+10,52),color=(0,0,0),width=2)
    a.blocks.append(h.model_block('A synthetic connected original diagram.','art','figure',rect))
    if case=='caption':a.text(CAPTION,'cap','caption',x=105,y=190,size=5)
    if case=='heading':b.text('A Genuine Separate Section','section','heading',x=24,y=30,size=7)
    b.text(RIGHT,'right',x=44 if case=='indent' else x,y=55,size=6,previous=True)
    b.text(LATER,'later',x=x,y=135,size=5)
    return [a,b],FIRST+' '+tail,rect


def check_art(path,report,docs,rect):
    imgs=[(src,r) for src,r in report['images'].items() if r.get('kind') in ('figure','figure_source_reference')]
    assert len(imgs)==1,'one intact original illustration must survive'
    src,record=imgs[0]
    with zipfile.ZipFile(path) as z:
        image=Image.open(io.BytesIO(z.read('OEBPS/'+src))).convert('RGB')
        blue=[(x,y) for y in range(image.height) for x in range(image.width) if (lambda c:c[2]>130 and c[0]<100 and c[1]<100)(image.getpixel((x,y)))]
        box=record['bbox'];assert box[0]<=rect[0] and box[1]<=rect[1] and box[2]>=rect[2] and box[3]>=rect[3]
        assert blue
        for px,py in [(rect[0]+4,94),(rect[2]-4,94),(rect[0]+4,171),(rect[2]-4,171)]:
            ix=(px-box[0])/(box[2]-box[0])*image.width;iy=(py-box[1])/(box[3]-box[1])*image.height
            assert any(abs(x-ix)<=image.width*5/(box[2]-box[0]) and abs(y-iy)<=image.height*5/(box[3]-box[1]) for x,y in blue),'each original blue corner must survive in its source position'
        for home,root in docs.items():
            for ref in root.findall('.//x:a',h.NS):
                if ref.get('class')!='source-disclosure-ref' or not (has_authored_substring(ref.text or '', '8eea9d54b9d9adb5031e1ce6424bf1fa49ced0f94699ca9a2273703153e67531', 28) and len(ref.text or '')==28):continue
                dest,ident=h.destination(home,ref.get('href'));target=next(n for n in docs[dest].iter() if n.get('id')==ident)
                assert target.findall('.//x:img',h.NS)
                assert any(a.text=='Return' and h.destination(dest,a.get('href'))==(home,ref.get('id')) for a in target.findall('.//x:a',h.NS))
    return record


@pytest.mark.parametrize('case',['clean','source-context','caption','terminal','heading','indent','foreign-leaf','not-surrounded'])
def test_wrapped_original_paragraph_carries_its_trailing_art_across_page_turn(tmp_path,monkeypatch,case):
    pages,left,rect=pages_for(case);path,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    record=check_art(path,report,docs,rect)
    if case=='source-context':
        assert record.get('degraded_source_context') or report['degraded_figure_count']>0
        assert any(has_authored_substring(a.text or '', '8eea9d54b9d9adb5031e1ce6424bf1fa49ced0f94699ca9a2273703153e67531', 28) and len(a.text or '')==28 for root in docs.values() for a in root.findall('.//x:a',h.NS))
    expected=[left+' '+RIGHT,LATER] if case in ('clean','source-context','caption') else [left,RIGHT,LATER]
    assert h.body_texts(docs)==expected,'wrapped source artwork may move only with its proved unfinished paragraph'
    assert all(not p.findall('.//x:img',h.NS) for root in docs.values() for p in root.findall('.//x:p',h.NS))
    if case=='caption':assert [h.plain(n) for n in h.body_nodes(docs) if n.get('class')=='caption']==[CAPTION]
    if case in ('clean','source-context','caption'):
        joined=next(n for n in h.body_nodes(docs) if h.plain(n)==expected[0])
        assert not joined.findall('.//x:a[@class="source-disclosure-ref"]',h.NS)
        assert {int(s.get('data-source-page')) for s in joined.findall('.//x:span',h.NS) if s.get('data-source-block') in ('left','right')}=={0,1}
    if case=='heading':assert any(n.tag.endswith('}h2') and h.plain(n)=='A Genuine Separate Section' for n in h.body_nodes(docs))
