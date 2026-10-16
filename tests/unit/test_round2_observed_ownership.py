"""Independent source-territory adversaries, using only synthetic prose."""
if __package__:
    from .test_round2_assembly import answer, block, optical, convert
else:
    from test_round2_assembly import answer, block, optical, convert


def test_borrowed_later_paragraph_cannot_be_published_in_source_leaf_tail(tmp_path, monkeypatch):
    prefix='Quiet travelers carry useful baskets along northern roads while patient workers prepare ordinary tools beside the'
    head='broad river before every morning journey through peaceful villages and distant gardens.'
    middle='Several separate sentences discuss ordinary maps roads buildings trees boats and weather today.'
    later='Bright stars guide careful sailors through quiet waters while distant lamps illuminate safe harbors tonight.'
    pixels=optical(prefix,20,300,columns=6,step=42)+optical(head,320,30,columns=6,step=42)+optical(middle,320,130,columns=6,step=42)+optical(later,320,230,columns=6,step=42)
    # A faulty reading borrows an independently printed later paragraph. Its
    # complete optical match is not proof that these are the same paragraph.
    readings=[answer(block(prefix+' '+later,bbox=(20,720,480,970)),
                     block(head,ident='b2',bbox=(520,50,980,230),previous=True),
                     block(middle,ident='b3',bbox=(520,300,980,500)),
                     block(later,ident='b4',bbox=(520,560,980,760)))]
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels],[[]])
    published=' '.join(body)
    assert published.count(later)==1,f'Later source paragraph duplicated: {body!r}'
    assert prefix+' '+head in published,f'True source leaf continuation displaced: {body!r}'


def test_tall_short_word_stays_in_its_printed_line_and_lexical_position():
    from cps.services.reflow import vision_pipeline
    words=optical('Quiet boats of careful travelers',20,80,columns=5,step=50)
    # The short word's rectangle is taller than its neighbors, but its upper
    # position and center still belong to their physical printed line.
    words[2]=dict(words[2],bbox=[120,77,135,103])
    words+=optical('Several workers prepare useful tools',20,115,columns=5,step=50)
    observed=vision_pipeline._pixel_line_order(words)
    assert [w['text'] for w in observed]==['Quiet','boats','of','careful','travelers','Several','workers','prepare','useful','tools']
    assert {id(w) for w in observed}=={id(w) for w in words},'Geometry may not drop or invent ink words'


def test_neighboring_physical_lines_remain_separate_when_boxes_vary():
    from cps.services.reflow import vision_pipeline
    upper=optical('Amber lamps brighten quiet paths',20,80,columns=5,step=50)
    lower=optical('Before dawn workers gather tools',20,99,columns=5,step=50)
    lower[0]=dict(lower[0],bbox=[20,96,63,111])
    words=lower[2:]+upper+lower[:2]
    observed=vision_pipeline._pixel_line_order(words)
    assert [w['text'] for w in observed]=='Amber lamps brighten quiet paths Before dawn workers gather tools'.split()


def test_italic_fragment_order_survives_short_word_geometry_repair():
    from cps.services.reflow import vision_pipeline
    first=optical('Records describe quiet travelers',20,80,columns=4,step=50)
    left=dict(first[0],text='Re',bbox=[20,80,29,90])
    right=dict(first[0],text='cords',bbox=[30,79,62,91])
    second=optical('carrying tools before sunset',20,110,columns=4,step=50)
    observed=vision_pipeline._pixel_line_order([left]+second+[right]+first[1:])
    assert [w['text'] for w in observed]=='Re cords describe quiet travelers carrying tools before sunset'.split()


def test_distinct_physical_repeated_paragraph_instances_are_both_preserved(tmp_path,monkeypatch):
    repeated='Bright stars guide careful sailors through quiet waters while distant lamps illuminate safe harbors tonight.'
    middle='Several separate sentences discuss ordinary maps roads buildings trees boats and weather today.'
    pixels=optical(repeated,20,280,columns=5,step=45)+optical(middle,320,30,columns=5,step=45)+optical(repeated,320,180,columns=5,step=45)
    readings=[answer(block(repeated,bbox=(20,650,480,950)),
                     block(middle,ident='b2',bbox=(520,50,980,250)),
                     block(repeated,ident='b3',bbox=(520,430,980,650)))]
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels],[[]])
    assert body==[repeated,middle,repeated],'Lexically repeated ink at separate real source rectangles is not duplication'


def test_genuine_cross_leaf_paragraph_can_share_page_with_later_owned_body(tmp_path,monkeypatch):
    left='Quiet travelers carry useful baskets along the'
    right='northern road before every morning journey toward distant gardens.'
    later='Bright stars guide careful sailors through quiet waters while distant lamps illuminate safe harbors tonight.'
    pixels=optical(left,20,280,columns=5,step=45)+optical(right,320,30,columns=5,step=45)+optical(later,320,180,columns=5,step=45)
    readings=[answer(block(left+' '+right,bbox=(20,650,480,950)),
                     block(later,ident='b2',bbox=(520,430,980,650)))]
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels],[[]])
    assert body==[left+' '+right,later],'Coherent later ownership cannot amputate a real disjoint source paragraph'


import pytest

@pytest.mark.parametrize('layout',['tall_firstline_word','sloping_column','genuine_indent'])
def test_unflagged_pdf_seam_uses_own_opening_rows_not_global_extreme(tmp_path,monkeypatch,layout):
    left='Quiet travelers carry useful baskets along the'
    right='northern road before every morning journey while patient workers prepare ordinary tools near peaceful villages beside broad rivers beneath clear skies after each passing storm brings gentle rain to distant farms and gardens.'
    words=optical(right,60,30,columns=4,step=45)
    if layout=='tall_firstline_word':
        w=words[2];words[2]=dict(w,bbox=[w['bbox'][0],24,w['bbox'][2],40])
    elif layout=='sloping_column':
        # Scan slope varies across many rows; the opening lines have no
        # paragraph indent relative to their local column trajectory.
        words=[dict(w,bbox=[w['bbox'][0]-i//4,w['bbox'][1],w['bbox'][2]-i//4,w['bbox'][3]]) for i,w in enumerate(words)]
    else:
        words=[dict(w,bbox=[w['bbox'][0]+(14 if i<4 else 0),w['bbox'][1],w['bbox'][2]+(14 if i<4 else 0),w['bbox'][3]]) for i,w in enumerate(words)]
    pixels=[optical(left,20,280),words]
    readings=[answer(block(left,next=True)),answer(block(right,bbox=(50,40,480,450),previous=False))]
    body,_,_=convert(tmp_path,monkeypatch,readings,pixels,[[],[]])
    expected=[left,right] if layout=='genuine_indent' else [left+' '+right]
    assert body==expected,f'Local source opening evidence misclassified a PDF paragraph boundary: {body!r}'
