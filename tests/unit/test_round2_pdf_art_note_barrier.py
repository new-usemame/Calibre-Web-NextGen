"""Independent current-page note barrier for source-proven floating art."""
if __package__:
    from .test_round2_assembly import answer,block,optical,convert
else:
    from test_round2_assembly import answer,block,optical,convert


def test_current_note_before_source_opening_cannot_be_hidden_by_role_filter(tmp_path,monkeypatch):
    left='Quiet travelers carry useful tools along the northern road with the'
    right='patient workers before the first light arrives.'
    caption='A simple compass.'
    previous=optical(left,320,280,columns=5,step=45)
    current=(optical('North East South West',50,35,columns=2,step=60)
             +optical(caption,20,150)
             +optical('2 A separate printed explanation.',20,175,columns=5,step=45)
             +optical(right,20,205,columns=4,step=45))
    readings=[answer(block(left,bbox=(520,650,980,950),next=True)),
              answer(block('North East South West',ident='f1',role='figure',bbox=(50,30,450,320)),
                     block(caption,ident='c1',role='caption',bbox=(20,350,480,425)),
                     block('A separate printed explanation.',ident='n1',role='footnote',marker='2',bbox=(20,430,480,490)),
                     block(right,ident='b2',bbox=(20,500,480,690),previous=True))]
    body,_,result=convert(tmp_path,monkeypatch,readings,[previous,current],[[],[]])
    assert left in body and right in body,'Current-page note cannot disappear from the source barrier check'
    assert result['page_joins']==0
    assert body.count(caption)==1
