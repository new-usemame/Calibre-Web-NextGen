# SPDX-License-Identifier: GPL-3.0-or-later
"""Native rule geometry binds notes without guessing from smaller type."""
import pytest
from cps.services.reflow import assemble, extract, skeleton
pytestmark = pytest.mark.unit


def line(text, x, y, size=10):
    return extract.Line([extract.Span(text, size, 'Times-Roman', 0,
        (x,y,x+len(text)*size*.45,y+size))], (x,y,x+len(text)*size*.45,y+size))


def block(number, lines):
    return extract.Block(number, (min(l.bbox[0] for l in lines),min(l.bbox[1] for l in lines),
        max(l.bbox[2] for l in lines),max(l.bbox[3] for l in lines)),lines)


def pages(marker=True, continuation=True):
    opening=[line(' As this note explains a source reading,',63,225),line('1',60,222,5)] if marker else [line('A complete displayed quotation ends here.',60,225)]
    a=extract.RawPage(0,500,700,blocks=[block(0,[line('Main body before the separate region.',40,140),line('More complete body text above the rule.',40,154)]),
        block(1,opening),block(2,[line('The explanation extends through an equal',80,300)]),block(3,[line('1',40,650)])],drawing_rects=[(40,210,150,211)])
    b=extract.RawPage(1,500,700,blocks=[block(0,[line('Main body on the following page.',40,140),line('Body continues independently here.',40,154)]),
        block(1,[line('[number] in the following interval.',60,520) if continuation else line('A separate complete display ends here.',60,520)]),block(2,[line('2',40,650)])],drawing_rects=[(40,505,150,506)])
    return [a,b]


def test_native_early_same_size_ruled_note_and_bracket_continuation_are_side_channel():
    raw=pages();style=skeleton.BookStyle(body_size=10)
    book=assemble.assemble([skeleton.page_skeleton(p,style) for p in raw],style,raw)
    assert len(book.notes)==2
    assert book.notes[0].num==1 and book.notes[0].text.startswith('As this note')
    assert book.notes[1].num is None and book.notes[1].text.startswith('[number]')
    assert book.notes[1].continued_from==(0,1)
    assert not any('equal' in e.text or '[number]' in e.text for e in book.elements)
    assert book.conservation.ok


def test_rule_alone_does_not_turn_a_display_into_a_note():
    raw=pages(marker=False);style=skeleton.BookStyle(body_size=10)
    book=assemble.assemble([skeleton.page_skeleton(p,style) for p in raw],style,raw)
    assert not book.notes
    assert any('displayed quotation' in e.text for e in book.elements)


def test_complete_prior_note_does_not_claim_a_new_display():
    raw=pages(continuation=False);style=skeleton.BookStyle(body_size=10)
    book=assemble.assemble([skeleton.page_skeleton(p,style) for p in raw],style,raw)
    assert len(book.notes)==1
    assert any('separate complete display' in e.text for e in book.elements)


@pytest.mark.parametrize('kind',['scan','grid','uncertain_number'])
def test_uncertain_or_table_rule_does_not_create_native_note(kind):
    raw=pages();style=skeleton.BookStyle(body_size=10)
    if kind=='scan':raw[0].source_geometry={'space':'reading','layer':'ocr'}
    elif kind=='grid':raw[0].drawing_rects.append((150,210,151,400))
    else:raw[0].blocks[1].lines[1].spans[0].uncertain=True
    book=assemble.assemble([skeleton.page_skeleton(p,style) for p in raw],style,raw)
    assert not book.notes


def test_emitted_continuation_links_resolve_and_keep_note_words(tmp_path):
    import zipfile
    from xml.etree import ElementTree as ET
    from cps.services.reflow import build_epub
    raw=pages();style=skeleton.BookStyle(body_size=10)
    book=assemble.assemble([skeleton.page_skeleton(p,style) for p in raw],style,raw)
    target=tmp_path/'notes.epub';build_epub.build(book,str(target))
    ns='{http://www.w3.org/1999/xhtml}'
    with zipfile.ZipFile(target) as z:
        roots={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.endswith('.xhtml')}
    ids={(name,e.get('id')) for name,r in roots.items() for e in r.iter() if e.get('id')}
    links=[(name,a.get('href')) for name,r in roots.items() for a in r.iter(ns+'a') if 'note_source_' in a.get('href','') or 'note_tail_' in a.get('href','')]
    assert len(links)==2
    import posixpath
    for name,href in links:
        part,anchor=href.split('#');resolved=posixpath.join(posixpath.dirname(name),part) if part else name
        assert (resolved,anchor) in ids
    asides=[a for r in roots.values() for a in r.iter(ns+'aside')]
    assert len(asides)==2
    assert sum('[number] in the following interval.' in ''.join(a.itertext()) for a in asides)==1
    assert all('[number]' not in ''.join(p.itertext()) for r in roots.values() for p in r.iter(ns+'blockquote'))
