"""A damaged terminal atom must not attach detached note text to the body."""
import pytest
from cps.services.reflow import assemble,skeleton,extract
from tests.unit.test_reflow_rules_first_seams import _slightly_smaller_note,_line

pytestmark=pytest.mark.unit


def source_tail(*, gap=30, terminal='glyph'):
    previous=_slightly_smaller_note()
    from dataclasses import replace
    opening=previous.blocks[-1].lines[0]
    words=opening.spans[1]
    opening.spans[1]=replace(words,text=' A preceding printed note reaches an uncertain final ',
        bbox=(46,words.bbox[1],220,words.bbox[3]))
    opening.spans.append(replace(words,text='word.',bbox=(221,words.bbox[1],250,words.bbox[3]),
        transcription_uncertain=terminal=='glyph'))
    current=_slightly_smaller_note(gap=gap)
    current.pno=1
    numbered=current.blocks[-1]
    for line in numbered.lines:
        line.bbox=tuple(v+20 if i in (1,3) else v for i,v in enumerate(line.bbox))
        for sp in line.spans:sp.bbox=tuple(v+20 if i in (1,3) else v for i,v in enumerate(sp.bbox))
    numbered.bbox=numbered.lines[0].bbox
    tail=_line('and finishes here without becoming body prose.',40,325,11.5)
    current.blocks.insert(1,extract.Block(7,tail.bbox,[tail]))
    # Detach from body, and place above the actually admitted numbered note.
    body=current.blocks[0].lines[0]
    body.bbox=(40,250,260,262);body.spans[0].bbox=body.bbox
    current.blocks[0].bbox=body.bbox
    if gap==0:
        tail.bbox=(40,263,260,274.5);tail.spans[0].bbox=tail.bbox
        current.blocks[1].bbox=tail.bbox
    return [previous,current]


@pytest.mark.parametrize('scan',[False,True])
def test_source_bound_terminal_atom_allows_qualified_detached_note_tail(scan):
    raws=source_tail();style=skeleton.BookStyle(body_size=12)
    skels=[skeleton.page_skeleton(r,style) for r in raws]
    skels[1].is_scan=scan
    book=assemble.assemble(skels,style,raws)
    assert book.conservation.ok
    tails=[n for n in book.notes if n.pno==1 and n.num is None]
    assert len(tails)==1 and tails[0].uncertain
    assert tails[0].text=='and finishes here without becoming body prose.'
    assert all('finishes here' not in e.text for e in book.pages[1])
    from cps.services.reflow import build_epub
    from xml.etree import ElementTree as ET
    root=ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+build_epub.page_fragment(book,1)+'</root>')
    assert any('finishes here' in ''.join(n.itertext()) for n in root.findall('aside'))
    assert not any('finishes here' in ''.join(n.itertext()) for n in root.findall('p'))


@pytest.mark.parametrize('change',['complete','attached','uppercase'])
def test_unproved_tail_remains_in_its_source_body(change):
    raws=source_tail(gap=0 if change=='attached' else 30,terminal='text' if change=='complete' else 'glyph')
    if change=='uppercase':raws[1].blocks[1].lines[0].spans[0].text='A new complete display begins here.'
    style=skeleton.BookStyle(body_size=12)
    book=assemble.assemble([skeleton.page_skeleton(r,style) for r in raws],style,raws)
    assert not any(n.pno==1 and n.num is None for n in book.notes)
    assert book.conservation.ok
