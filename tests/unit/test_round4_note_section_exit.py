"""A genuine note section must end before ordinary body with its own footer notes."""
import importlib.util
from pathlib import Path
_spec = importlib.util.spec_from_file_location('round4_section_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


def test_full_size_body_with_footer_notes_ends_carried_note_section(tmp_path, monkeypatch):
    notes, body = h.InkPage(), h.InkPage()
    notes.text('NOTES', 'title', 'heading', y=35, size=11)
    notes.text('7 The earlier note gives a small source explanation and', 'prior', 'footnote', y=70, size=7, marker='7', model='The earlier note gives a small source explanation and')
    opening = 'An ordinary full-size paragraph introduces a new geometric figure.'
    body.text(opening, 'opening', size=10)
    body.figure()
    body.text('8 A small footer note explains the new figure using separate ordinary observations.', 'firstnote', 'footnote', y=300, size=7, marker='8', model='A small footer note explains the new figure using separate ordinary observations.')
    body.text('9 A second small footer note preserves a different independent source observation.', 'secondnote', 'footnote', y=350, size=7, marker='9', model='A second small footer note preserves a different independent source observation.')
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [notes, body])
    assert opening in h.body_texts(docs), 'full-size ordinary body ends prior note section even with current small footer notes'
    _, emitted = h.refs_and_notes(docs)
    assert all(opening not in ''.join(note.itertext()) for _, note in emitted)
    assert len(emitted) == 3, 'new page footer notes and prior note retain distinct owners'
