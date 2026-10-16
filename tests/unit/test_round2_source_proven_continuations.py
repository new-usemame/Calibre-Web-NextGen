"""Independent source-proof continuation witnesses using synthetic text written for these tests."""
import pytest
if __package__:
    from .test_round2_assembly import answer,block,optical,convert,NS
else:
    from test_round2_assembly import answer,block,optical,convert,NS


@pytest.mark.parametrize('source_end',['unfinished','terminal'])
def test_recovered_leaf_head_uses_guarded_source_tail_instead_of_borrowed_model_tail(tmp_path,monkeypatch,source_end):
    prefix='Quiet travelers carry useful baskets along northern roads while patient workers prepare ordinary tools beside the'
    if source_end=='terminal':prefix=prefix[:-3]+'river.'
    head='broad river before every morning journey through peaceful villages and distant gardens.'
    later='Bright stars guide careful sailors through quiet waters while distant lamps illuminate safe harbors tonight.'
    # The later separately owned paragraph is falsely borrowed by the model.
    # Its punctuation gives the opposite boundary signal from the real ink.
    faulty=(prefix+' '+later if source_end=='unfinished' else prefix.rstrip('.')+' '+later.rstrip('.'))
    pixels=optical(prefix,20,280,columns=5,step=45)+optical('NORTHERN GARDENS',320,5)+optical(head,320,30,columns=5,step=45)+optical(later,320,140,columns=5,step=45)
    readings=[answer(block(faulty,bbox=(20,650,480,950)),
                     block('NORTHERN GARDENS',ident='h2',role='running_head',bbox=(520,0,980,45)),
                     block(later,ident='b2',bbox=(520,340,980,600)))]
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels],[[]])
    expected=[prefix+' '+head,later] if source_end=='unfinished' else [prefix,head,later]
    assert body==expected,f'Unvalidated model-tail punctuation overruled source-owned publication: {body!r}'


@pytest.mark.parametrize('case',['source_word','terminal','indented','heading'])
def test_source_bound_word_halves_can_continue_around_owned_figure_and_caption(tmp_path,monkeypatch,case):
    left='Quiet travelers approach the old bridge-' if case!='terminal' else 'Quiet travelers approach the old bridge.'
    right='side path before the first light.'
    caption='A simple compass.'
    suffix=optical(right,320,190,columns=4,step=45)
    if case=='indented':suffix=[dict(w,bbox=[w['bbox'][0]+(14 if i<4 else 0),w['bbox'][1],w['bbox'][2]+(14 if i<4 else 0),w['bbox'][3]]) for i,w in enumerate(suffix)]
    pixels=optical(left,20,280,columns=5,step=45)+optical('North East South West',350,35,columns=2,step=60)+optical(caption,320,150)+suffix
    art=block('North East South West',ident='f1',role='heading' if case=='heading' else 'figure',bbox=(530,30,950,320))
    readings=[answer(block(left,bbox=(20,650,480,950),next=True),art,
                     block(caption,ident='c1',role='caption',bbox=(520,350,980,425)),
                     block(right,ident='b2',bbox=(520,450,980,630),previous=True))]
    body,documents,_=convert(tmp_path,monkeypatch,readings,[pixels],[[]])
    if case=='source_word':
        assert left+right in body,'A source-bound word was split by a separate right-leaf illustration'
        assert left not in body and right not in body
    else:
        assert left in body and right in body,'Terminal, indentation, and heading source barriers must remain'
    assert body.count(caption)==1,'Caption must retain its own semantic paragraph'
    if case!='heading':
        figures=[e for root in documents.values() for e in root.findall('.//x:figure',NS)]
        assert len(figures)==1 and len(figures[0].findall('.//x:img',NS))==1,'Original figure must remain once, outside prose'


@pytest.mark.parametrize('physical_wrap',[True,False])
def test_optical_fallback_keeps_literal_hyphen_without_artificial_word_space_at_a_printed_wrap(tmp_path,monkeypatch,physical_wrap):
    printed='Quiet travelers reach the harbor- side paths before dawn while careful workers prepare ordinary tools'
    pixels=optical(printed,20,80,columns=5 if physical_wrap else 8,step=55)
    candidate='Quiet travelers reach the harbor- Fabricated golden leaves discuss distant strange clouds beyond ordinary understanding tonight'
    readings=[answer(block(candidate,bbox=(0,180,800,500)))]
    body,documents,_=convert(tmp_path,monkeypatch,readings,[pixels],[[]])
    expected=printed.replace('harbor- side','harbor-side') if physical_wrap else printed
    assert body==[expected],'Only independently observed source row wraps permit half-word adjacency repair; literal hyphen remains'
    assert any(a.get('class')=='source-disclosure-ref' for root in documents.values() for a in root.findall('.//x:a',NS)),'Optical fallback remains explicitly uncertain'
