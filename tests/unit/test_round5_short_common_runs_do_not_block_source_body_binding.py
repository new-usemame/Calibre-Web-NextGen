"""SOURCE-free coherent source body must not be classified as borrowed common prose.

A coarse upper hint omits the rest of an accurately read paragraph around art.
A separate reserved paragraph shares numerous short function-word runs, without
sharing a substantive contiguous reading. Such coincidence cannot suppress the
source-wide exact anchors of the correct paragraph. A genuinely stolen long
foreign run remains owned by the other paragraph and is never duplicated.
"""
import importlib.util
from pathlib import Path
from difflib import SequenceMatcher
import pytest
from cps.services.reflow import vision_pipeline, vision_reading

_helper = Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():
    _helper = Path(vision_pipeline.__file__).parents[3] / 'tests/unit/test_round4_defects.py'
_s = importlib.util.spec_from_file_location('round5_short_shared_source_runs', _helper)
h = importlib.util.module_from_spec(_s)
_s.loader.exec_module(h)
pytestmark = pytest.mark.unit


@pytest.mark.parametrize('borrowed', [False, True])
def test_short_common_word_matches_do_not_erase_exact_source_body_tail(tmp_path, monkeypatch, borrowed):
    p = h.InkPage()
    foreign_lines = ['The sailors measure the winds and the waves near the harbor',
                     'where the captains keep the coastal notes.']
    foreign = ' '.join(foreign_lines)
    p.text(foreign_lines, 'separate', x=22, y=25, size=5)
    upper = 'The careful observers recorded a well-'
    lower_lines = ['preserved sample while the workers compared the water and',
                   'the earlier measurements near the laboratory where the watchers',
                   'kept the original notes beside independently calibrated instruments.']
    lower = ' '.join(lower_lines)
    accurate = upper + lower
    target = accurate + (' ' + foreign if borrowed else '')
    matches = SequenceMatcher(None, foreign.casefold().split(), target.casefold().split(), autojunk=False).get_matching_blocks()
    if borrowed:
        assert max(size for a, b, size in matches) >= vision_reading.UNALIGNED_RUN
    else:
        assert sum(size for a, b, size in matches) >= vision_reading.UNALIGNED_RUN
        assert max(size for a, b, size in matches) < vision_reading.UNALIGNED_RUN
    p.text(upper, 'wrapped', x=22, y=68, size=7, model=target)
    p.figure()
    for i, line in enumerate(lower_lines):
        p.page.insert_text((22, 230 + i * 14), line, fontsize=6)
    later = 'A separate concluding paragraph remains outside both earlier accounts.'
    p.text(later, 'later', x=22, y=305, size=5)
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [p])
    # Includes recovered OTHER containers, but excludes note/disclosure evidence.
    body = []
    for root in docs.values():
        for node in root.findall('x:body', h.NS):
            for child in node:
                if child.tag.endswith(('}p', '}div', '}h2')) and child.get('class') != 'caption':
                    body.append(h.plain(child))
    stream = ' '.join(body)
    assert stream.count(foreign) == 1, 'a genuine other paragraph remains once, never stolen by whole-model text'
    if not borrowed:
        assert accurate in body, 'short coincidental foreign matches cannot block the source-proven complete body paragraph around art'
        assert lower in stream and later in stream
        refs, notes = h.refs_and_notes(docs)
        assert not refs and not notes
