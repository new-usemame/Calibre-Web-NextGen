"""Independent synthetic exact-source continuation around leading PDF artwork."""
import pytest
if __package__:
    from .test_round2_assembly import answer,block,optical,convert,NS
else:
    from test_round2_assembly import answer,block,optical,convert,NS

@pytest.mark.parametrize('case',['ordinary_false_flags','explicit_flags','footer','hyphen','terminal','indented','heading','table','art_below','other_leaf','caption_only','missing_tail','missing_head','trailing_previous_art','nonconsecutive'])
def test_cross_pdf_source_sentence_floats_only_proven_leading_art(tmp_path,monkeypatch,case):
    left='Quiet travelers carry useful tools along the northern road with the'
    right='patient workers before the first light arrives.'
    if case=='terminal':left=left[:-3]+'workers.'
    if case=='hyphen':left='Quiet travelers approach the bridge-';right='side path before the first light arrives.'
    printed_left=left if case!='missing_tail' else left.rsplit(' ',1)[0]+' those'
    printed_right=right if case!='missing_head' else right.replace('patient','careful',1)
    caption='A simple compass.'
    previous=optical(printed_left,320,280,columns=5,step=45)
    opening=optical(printed_right,20,190,columns=4,step=45)
    if case=='indented':
        opening=[dict(w,bbox=[w['bbox'][0]+(14 if i<4 else 0),w['bbox'][1],w['bbox'][2]+(14 if i<4 else 0),w['bbox'][3]]) for i,w in enumerate(opening)]
    x,y=(50,230) if case=='art_below' else ((350,35) if case=='other_leaf' else (50,35))
    current=optical('North East South West',x,y,columns=2,step=60)+optical(caption,20,150)+opening
    left_blocks=[block(left,bbox=(520,650,980,950),next=case=='explicit_flags')]
    if case=='footer':
        previous+=optical('2 A separate printed explanation.',320,350,columns=5,step=45)
        left_blocks.append(block('A separate printed explanation.',ident='n1',role='footnote',marker='2',bbox=(520,850,980,960)))
    if case=='trailing_previous_art':
        previous+=optical('North East',50,100,columns=2,step=60)
        left_blocks.append(block('North East',ident='trailing',role='figure',bbox=(50,200,450,500)))
    artbox=(50,540,450,700) if case=='art_below' else ((530,30,950,320) if case=='other_leaf' else (50,30,450,320))
    role='heading' if case=='heading' else 'table' if case=='table' else 'figure'
    right_blocks=[block('North East South West',ident='f1',role=role,bbox=artbox),block(caption,ident='c1',role='caption',bbox=(20,350,480,425)),block(right,ident='b2',bbox=(20,450,480,630),previous=case=='explicit_flags')]
    if case=='caption_only':right_blocks=right_blocks[1:]
    pixels=[previous,current];numbers=None
    if case=='nonconsecutive':pixels=[previous,optical('An unrelated intermediate page.',20,100),current];numbers=[0,2]
    body,documents,result=convert(tmp_path,monkeypatch,[answer(*left_blocks),answer(*right_blocks)],pixels,[[] for _ in pixels],page_numbers=numbers)
    if case in ('ordinary_false_flags','explicit_flags','footer','hyphen'):
        expected=left+right if case=='hyphen' else left+' '+right
        assert expected in body,'Exact source-backed sentence remained split by next-PDF leading artwork'
        assert left not in body and right not in body,'Separate source fragments survived the approved join'
        assert result['page_joins']==1
    else:
        assert left in body and right in body,'Flags alone or unsupported source geometry moved structural content across a PDF boundary'
        assert result['page_joins']==0
    assert body.count(caption)==1,'Source caption must remain once and outside the joined sentence'
    expected_figures=(0 if case in ('heading','table','caption_only') else 1)+(1 if case=='trailing_previous_art' else 0)
    if case=='table':expected_figures=1
    figures=[e for root in documents.values() for e in root.findall('.//x:figure',NS)]
    assert len(figures)==expected_figures,'Original artwork/table must survive exactly once without entering a body paragraph'
    assert all(not p.findall('.//x:img',NS) for root in documents.values() for p in root.findall('.//x:p',NS))
