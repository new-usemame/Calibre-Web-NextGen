"""Independent residual recovered-head controls; all source prose is invented."""
import pytest
import shutil

# Needs the local OCR engine the image ships; without it pages degrade to
# their retained source image.
real_ocr = pytest.mark.skipif(not shutil.which('tesseract'), reason='needs installed OCR engine')
if __package__:
    from .test_round2_assembly import answer, block, optical, convert
else:
    from test_round2_assembly import answer, block, optical, convert


@pytest.mark.parametrize('case',['continuation','no_marginal_head','terminal','indented','heading_barrier'])
def test_unflagged_missing_leaf_head_uses_source_flow_and_respects_boundaries(tmp_path,monkeypatch,case):
    left='Quiet travelers carry useful baskets along northern roads while patient workers prepare ordinary tools beside the'
    if case=='terminal':left=left[:-3]+'river.'
    missing='broad river before every morning journey through peaceful villages and distant gardens.'
    later='Bright stars guide careful sailors through quiet waters while distant lamps illuminate safe harbors tonight.'
    head=optical(missing,320,30,columns=5,step=45)
    if case=='indented':head=[dict(w,bbox=[w['bbox'][0]+(14 if i<5 else 0),w['bbox'][1],w['bbox'][2]+(14 if i<5 else 0),w['bbox'][3]]) for i,w in enumerate(head)]
    label='NORTHERN GARDENS'
    pixels=optical(left,20,280,columns=5,step=45)+optical(label,320,5,columns=5,step=45)+head+optical(later,320,140,columns=5,step=45)
    readings=[answer(block(left,bbox=(20,650,480,950),next=False),
                     block(label,ident='h2',role='heading' if case=='heading_barrier' else 'running_head',bbox=(520,0,980,45)),
                     block(later,ident='b2',bbox=(520,340,980,570),previous=False))]
    if case=='no_marginal_head':
        pixels=[w for w in pixels if w['bbox'][1]!=5]
        readings[0]['blocks']=[b for b in readings[0]['blocks'] if b['id']!='h2']
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels],[[]])
    if case in ('continuation','no_marginal_head'):
        assert body==[left+' '+missing,later],f'An optically bound omitted leaf head broke its source sentence: {body!r}'
    else:
        assert left in body and missing in body and later in body,'True punctuation, paragraph indentation, or heading must retain the boundary'
        assert left+' '+missing not in body


def test_no_neighbor_recovery_follows_last_body_in_ragged_source_column(tmp_path,monkeypatch):
    earlier='Several ordinary paragraphs discuss maps buildings gardens roads tools boats and the weather today.'
    left='Quiet travelers carry useful baskets along the'
    missing='northern road before every morning journey through peaceful villages and distant gardens.'
    later='Bright stars guide careful sailors through quiet waters while distant lamps illuminate safe harbors tonight.'
    pixels=optical(earlier,20,90,columns=5,step=55)+optical(left,20,280,columns=3,step=40)+optical(missing,320,30,columns=5,step=45)+optical(later,320,140,columns=5,step=45)
    readings=[answer(block(earlier,ident='earlier',bbox=(20,200,480,400)),
                     block(left,bbox=(20,650,400,950)),
                     block(later,ident='b2',bbox=(520,340,980,570)))]
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels],[[]])
    assert body==[earlier,left+' '+missing,later],'A wider earlier paragraph cannot own the next-leaf source head'


@real_ocr
@pytest.mark.parametrize('position',['footer','in_flow'])
def test_recovered_head_respects_printed_note_geometry(tmp_path,monkeypatch,position):
    left='Quiet travelers carry useful baskets along northern roads while patient workers prepare ordinary tools beside the'
    missing='broad river before every morning journey through peaceful villages and distant gardens.'
    later='Bright stars guide careful sailors through quiet waters while distant lamps illuminate safe harbors tonight.'
    note='A short printed explanation belongs outside the main paragraph.'
    note_y=350 if position=='footer' else 220
    pixels=optical(left,20,280,columns=5,step=45)+optical('1 '+note,20,note_y,columns=5,step=45)+optical('NORTHERN GARDENS',320,5)+optical(missing,320,30,columns=5,step=45)+optical(later,320,140,columns=5,step=45)
    readings=[answer(block(left.replace('travelers','travelers<n:n1>'),bbox=(20,650,480,950)),
                     block(note,ident='n1',role='footnote',marker='1',bbox=(20,note_y/400*1000,480,(note_y+40)/400*1000)),
                     block('NORTHERN GARDENS',ident='h2',role='running_head',bbox=(520,0,980,45)),
                     block(later,ident='b2',bbox=(520,340,980,570)))]
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels],[[]])
    assert note not in ' '.join(body),'Printed note must stay outside source body ownership'
    if position=='footer':assert body==[left+' '+missing,later],'A separate source footer is not an in-flow paragraph barrier'
    else:assert left in body and missing in body and left+' '+missing not in body
