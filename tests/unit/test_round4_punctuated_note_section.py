"""Punctuated repeated note titles preserve their source-bound continuation."""
import importlib.util
from pathlib import Path
import pytest
_spec=importlib.util.spec_from_file_location('punctuated_section_helpers',Path(__file__).with_name('test_round4_defects.py'))
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)

@pytest.mark.parametrize('notes_section',[True,False])
def test_punctuated_note_title_carries_tail_but_real_chapter_stays_body(tmp_path,monkeypatch,notes_section):
    title='NOTES.' if notes_section else 'RIVER BASINS.'
    opening='7 A small source note explains how the current moves through the'
    tail='narrow channel into the quiet reservoir.'
    first=h.InkPage()
    first.text(title,'head','heading',x=110,y=23,size=8)
    first.text('Ordinary main source words point to this note.','body',y=50,size=9,
        model='Ordinary main source words point to this note.<n:n7>')
    first.text('6 Another separately numbered source note gives a complete account.','n6','footnote',y=95,size=7,marker='6')
    first.text(opening,'n7','footnote',y=330,size=7,marker='7')
    second=h.InkPage()
    second.text(title,'head','heading',x=110,y=23,size=8)
    second.text(tail,'tail','body',y=65,size=7)
    second.text(['8 An independent printed note gives several ordinary source details.',
                 'Its complete explanation remains in this distinct labelled note.'],'n8','footnote',y=115,size=7,marker='8')
    second.text('9 A final printed note gives another complete source account.','n9','footnote',y=330,size=7,marker='9')
    second.text('NORTHBANK PRESS, PRINTERS.','printer','other',x=94,y=347,size=4)
    _,result,docs=h.convert_ink(tmp_path,monkeypatch,[first,second])
    refs,notes=h.assert_exact_returns(docs,1)
    owner=next(note for name,note in notes if 'A small source note' in h.plain(note))
    body=' '.join(h.body_texts(docs))
    if notes_section:
        assert tail not in body, 'the unmarked continuation below a punctuated note running-title is not ordinary body'
        assert opening+' '+tail in h.plain(owner), 'the prior labelled note retains its complete continued sentence'
        assert {s.get('data-source-page') for s in owner.findall('.//x:span[@data-source-page]',h.NS)}=={'0','1'}
        assert 'NORTHBANK PRESS' not in ' '.join(h.plain(note) for _,note in notes), 'separate small printer imprint stays outside the note lane'
        assert not any(h.plain(node)==title for node in h.body_nodes(docs)), 'repeated notes furniture is not an authored chapter heading'
    else:
        assert tail in body, 'a genuine repeated chapter heading is a structural barrier to note carry'
        assert tail not in h.plain(owner)
        assert any(h.plain(node)==title for node in h.body_nodes(docs)), 'authored chapter heading is preserved'
