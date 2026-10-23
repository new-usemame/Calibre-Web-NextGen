"""Independent additional source-only ownership and bare marker controls."""
import importlib.util
from pathlib import Path
import pytest
import pymupdf
_spec = importlib.util.spec_from_file_location('round4_defect_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


def test_note_section_does_not_capture_next_full_size_body_without_heading(tmp_path, monkeypatch):
    notes, body = h.InkPage(), h.InkPage()
    notes.text('NOTES', 'title', x=120, y=35)
    notes.text('7 A small printed note ends without further material.', 'note', 'footnote', y=90, size=7, marker='7', model='A small printed note ends without further material.')
    prose = 'Ordinary full size prose returns on a new page without any heading.'
    body.text(['Ordinary full size prose returns on a new',
               'page without any heading.',
               'The next printed line belongs to the same',
               'ordinary paragraph.'], 'body', size=11)
    assert ' '.join(body.page.get_text().split())==' '.join([prose, 'The next printed line belongs to the same ordinary paragraph.']), 'The fixture must paint its complete expected body text inside the page'
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [notes, body])
    assert ' '.join([prose, 'The next printed line belongs to the same ordinary paragraph.']) in h.body_texts(docs)
    assert all(prose not in ''.join(n.itertext()) for _, n in h.refs_and_notes(docs)[1])


def test_repeated_short_top_body_openings_remain_live_body(tmp_path, monkeypatch):
    first, second = h.InkPage(), h.InkPage()
    opening = 'The same ordinary opening appears on two separate pages.'
    first.text(opening, 'body', y=50)
    second.text(opening, 'body', y=50)
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [first, second])
    assert h.body_texts(docs).count(opening) == 2


def bare_origin(marker, mathematical=False):
    page = h.InkPage()
    prefix = 'The expression x' if mathematical else 'The clear crystal'
    page.page.insert_text((24, 55), prefix, fontsize=9)
    x = 24 + pymupdf.get_text_length(prefix, fontsize=9)
    page.page.insert_text((x, 51), marker, fontsize=5)
    x += pymupdf.get_text_length(marker, fontsize=5)
    tail = ' has a mathematical meaning.' if mathematical else ' carries a careful explanation.'
    page.page.insert_text((x, 55), tail, fontsize=9)
    page.page.insert_text((24, 69), 'Another ordinary printed line completes this source paragraph.', fontsize=9)
    page.blocks.append(h.model_block(prefix + marker + tail + ' Another ordinary printed line completes this source paragraph.', 'body', 'body', [23, 41, 282, 73]))
    return page


@pytest.mark.parametrize('marker', ['7', '*'])
def test_bare_raised_source_markers_link_to_unique_remote_notes(tmp_path, monkeypatch, marker):
    origin, notes = bare_origin(marker), h.InkPage()
    notes.text(marker + ' A synthetic note explains the clear crystal.', 'note', 'footnote', y=320, size=7, marker=marker, model='A synthetic note explains the clear crystal.')
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [origin, notes])
    refs, _ = h.assert_exact_returns(docs, 1)
    assert ''.join(refs[0][1].itertext()) == marker


def test_superscript_mathematical_power_does_not_invent_remote_note(tmp_path, monkeypatch):
    origin, notes = bare_origin('7', mathematical=True), h.InkPage()
    notes.text('7 A separate note has no printed reference from this formula.', 'note', 'footnote', y=320, size=7, marker='7', model='A separate note has no printed reference from this formula.')
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [origin, notes])
    assert h.refs_and_notes(docs)[0] == []
    assert 'x7 has a mathematical meaning.' in '\n'.join(h.body_texts(docs))
