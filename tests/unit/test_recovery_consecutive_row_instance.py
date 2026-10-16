"""Additive source-free physical-row adjacency control; no model or source words."""
import pymupdf
from cps.services.reflow import vision_pipeline as vp
from cps.services.reflow.source_display import SourceDisplay

def test_covered_foreign_instance_between_endpoints_prevents_body_continuation():
    def w(text,x,y,width=30):return dict(text=text,bbox=[x,y,x+width,y+9])
    healthy=[w('earlier'+str(i),30+i*35,80) for i in range(6)]+[w('still',30,100),w('continuing',65,100)]
    foreign=w('OwnedForeign',96,100,3)
    omitted=[w('remaining'+str(i),100+(i%4)*35,100+(i//4)*14) for i in range(12)]
    optical=healthy[:6]+healthy[6:]+[foreign]+omitted
    def block(words,ident,role):
        b=[min(x['bbox'][0]for x in words),min(x['bbox'][1]for x in words),max(x['bbox'][2]for x in words),max(x['bbox'][3]for x in words)]
        return dict(id=ident,role=role,text=' '.join(x['text']for x in words),marker='',bbox=[b[0]/520*1000,b[1]/640*1000,b[2]/520*1000,b[3]/640*1000],low_confidence=[],continues_previous=False,continues_next=False)
    answer={'blocks':[block(healthy,'healthy','body'),block([foreign],'foreign','other')]}
    doc=pymupdf.open();doc.new_page(width=520,height=640);display=SourceDisplay(doc,0)
    regions={b['id']:[vp.block_rect(b,display)]for b in answer['blocks']}
    regions['healthy']=[[30,80,235,89],[30,100,95,109]]
    regions['_instances']={vp._source_instance(x):'healthy' for x in healthy}
    regions['_instances'][vp._source_instance(foreign)]='foreign'
    try:
        recovered,ids=vp.cover_missing_lines(answer,optical,[],display,regions,{})
        assert ids,'The substantive omitted run must still be retained'
        assert all(not b['continues_previous'] for b in recovered['blocks'] if b['id']in ids),'An explicitly owned foreign physical row instance between endpoints forbids a proved BODY continuation'
        assert next(b for b in recovered['blocks']if b['id']=='healthy')['text']==answer['blocks'][0]['text'],'Existing healthy model text cannot be rewritten'
    finally:doc.close()
