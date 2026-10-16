"""Printed selected note labels must support catalogue identity; body math is prose."""
import importlib.util
from pathlib import Path
import pytest
_spec = importlib.util.spec_from_file_location('round4_catalogue_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


def test_wrong_model_note_label_cannot_hide_selected_printed_note_owner(tmp_path, monkeypatch):
    origin, notes = h.InkPage(), h.InkPage()
    origin.reference(raised=True)
    notes.text('7 A selected printed note provides the clear source explanation.', 'note', 'footnote', y=320, size=7, marker='8', model='A selected printed note provides the clear source explanation.')
    # Publication must not treat the printed7 as outside scope merely because
    # a model metadata field mislabeled the selected note8.
    from cps.services.reflow import vision_pipeline as vp
    from tests.unit.round7_public_note_contract_support import source_visible
    strict=vp._note_ownership;rejected=[]
    def observed(*args,**kwargs):
        try:return strict(*args,**kwargs)
        except Exception as error:
            cause=getattr(error,'cause',error)
            if isinstance(cause,ValueError):rejected.append(str(cause))
            raise
    monkeypatch.setattr(vp,'_note_ownership',observed)
    path,report,docs=h.convert_ink(tmp_path,monkeypatch,[origin,notes])
    assert rejected,'strict catalogue must reject model label contradicting selected printed source'
    source_visible(path,report,{0,1})
    assert h.refs_and_notes(docs)[0]==[],'disputed catalogue cannot invent a normal note target'


def test_baseline_numeric_body_prefix_does_not_become_note_catalogue_entry(tmp_path, monkeypatch):
    page = h.InkPage()
    calculation = '7 + 2 = 9 remains an ordinary baseline numeric calculation.'
    page.text(calculation, 'math', 'body', size=10)
    page.text('7 A separate small footer note belongs only in the notes.', 'note', 'footnote', y=330, size=7, marker='7', model='A separate small footer note belongs only in the notes.')
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    refs, notes = h.refs_and_notes(docs)
    assert refs == [], 'ordinary baseline number matching a note label must not invent a reference'
    assert calculation in h.body_texts(docs)
    assert len(notes) == 1
    assert all(calculation not in ''.join(note.itertext()) for _, note in notes)


def test_unmarked_numeric_note_continuation_keeps_owner_and_all_source_pages(tmp_path, monkeypatch):
    origin, notes, continuation = h.InkPage(), h.InkPage(), h.InkPage()
    origin.reference(raised=True)
    notes.text('NOTES', 'title', 'heading', y=35, size=11)
    first = 'The selected source note begins a numerical observation and'
    notes.text('7 ' + first, 'note', 'footnote', y=70, size=7, marker='7', model=first)
    second = '2 + 3 remains part of the same unmarked note explanation.'
    continuation.text(second, 'continued', 'footnote', y=55, size=7, marker='')
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [origin, notes, continuation])
    _, emitted = h.assert_exact_returns(docs, 1)
    assert len(emitted) == 1, 'numeric unmarked continuation is part of original note7, not new note2'
    note = emitted[0][1]
    assert first in ''.join(note.itertext()) and second in ''.join(note.itertext())
    assert {int(s.get('data-source-page')) for s in note.findall('.//x:span[@data-source-page]', h.NS)} == {1, 2}
