"""Independent footer-note majority must not convert live figure-page prose."""
import importlib.util
from pathlib import Path
_spec=importlib.util.spec_from_file_location('round4_footer_helpers',Path(__file__).with_name('test_round4_defects.py'))
h=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


def test_two_footer_notes_on_figure_page_do_not_extend_previous_page_note_into_body(tmp_path,monkeypatch):
    previous,current=h.InkPage(),h.InkPage()
    previous.text('Earlier live body prose forms an ordinary complete paragraph.', 'body')
    previous.text('7 A previous small footer note describes observations and', 'prior','footnote',y=330,size=7,marker='7',model='A previous small footer note describes observations and')
    opening='An ordinary paragraph introduces a clear geometric figure.'
    current.text(opening,'opening',size=10)
    current.figure()
    current.text('8 A separate footer note provides independent detailed observations about this figure.', 'firstnote','footnote',y=300,size=7,marker='8',model='A separate footer note provides independent detailed observations about this figure.')
    current.text('9 Another separate footer note supplies additional careful information about a different object.', 'secondnote','footnote',y=350,size=7,marker='9',model='Another separate footer note supplies additional careful information about a different object.')
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[previous,current])
    assert opening in h.body_texts(docs), 'ordinary full-size source prose stays live even when two footer notes have more words'
    _,notes=h.refs_and_notes(docs)
    assert all(opening not in ''.join(note.itertext()) for _,note in notes)
    assert len(notes)==3, 'three separately marked source notes retain their own owners'
