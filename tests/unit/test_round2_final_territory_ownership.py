"""Independent final source-territory ownership witness; synthetic source only."""
if __package__:
    from .test_round2_assembly import answer,block,optical,convert
else:
    from test_round2_assembly import answer,block,optical,convert


def test_recovered_closing_line_is_not_stolen_by_another_bodys_obsolete_broad_reservation(tmp_path,monkeypatch):
    left='Quiet travelers carry useful baskets along northern roads while patient workers prepare ordinary tools beside the'
    head='broad river before each morning journey requires a small'
    closing='amount of careful preparation so every distant village can be reached safely.'
    later='Bright stars guide careful sailors through quiet waters while distant lamps illuminate safe harbors tonight. Their patient companions arrange sturdy maps beside warm cabins.'
    pixels=optical(left,20,280,columns=5,step=45)+optical(head,320,30,columns=6,step=45)+optical(closing,320,70,columns=6,step=45)+optical(later,320,115,columns=5,step=45)
    # A coherent later paragraph has a broad initial rectangle which also
    # contains an unmatched closing row from the omitted preceding paragraph.
    # Its final matched territory starts below that row. Neither output may
    # silently discard source words between old and final ownership.
    readings=[answer(block(left,bbox=(20,650,480,950)),block(later,ident='b2',bbox=(520,165,980,540)))]
    body,_,_=convert(tmp_path,monkeypatch,readings,[pixels],[[]])
    assert body==[left+' '+head+' '+closing,later],f'Printed closing words vanished between obsolete reservation and final region ownership: {body!r}'
