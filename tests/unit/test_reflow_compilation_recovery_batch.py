"""Procedural alignment faults must be disclosed together before publication."""
import copy

import pytest

from cps.services.reflow import unit_recovery as ur, vision_pipeline as vp
from tests.unit import test_round4_defects as ink

pytestmark = pytest.mark.unit


def _pages():
    pages = []
    modes = {}
    for colors in [('amber', 'silver', 'copper'), ('purple', 'orange', 'marble')]:
        page = ink.InkPage()
        for index, color in enumerate(colors):
            lines = [f'The {color} sample keeps the panel',
                     'clear while gentle lamps light it.']
            block = page.text(lines, f'body{index}', y=45 + 85 * index, size=7)
            modes[block['text']] = ('whole', 'group', 'contracted')[index]
        pages.append(page)
    good = ink.InkPage()
    good.text('The final clear sample remains readable.', 'healthy')
    pages.append(good)
    return pages, modes


@pytest.mark.parametrize("page_fallback", [False, True])
def test_independent_quality_failures_recover_together_with_exact_owners(tmp_path, monkeypatch, page_fallback):
    pages, modes = _pages()
    align = vp.vision_reading.align
    recover = ur.Controller.recover
    refine = ur.Controller.refine_units
    attempts = []
    def conservative_refinement(controller, units, error):
        owned = refine(controller, units, error)
        return [dict(page=0, block=None) if page_fallback and unit == dict(page=0, block='body1') else unit for unit in owned]
    def defective_alignment(text, *args, **kwargs):
        result = align(text, *args, **kwargs)
        mode = modes.get(text)
        if mode == 'whole':
            result.update(tokens=['zzqv', 'rrjx', 'ppqx'], marked={0, 1, 2}, fallback=True)
        elif mode == 'group':
            tokens = list(result['tokens'])
            tokens[6:9] = ['zzqv', 'rrjx', 'ppqx']
            result.update(tokens=tokens, marked={6, 7, 8}, fallback=False)
        elif mode == 'contracted':
            result.update(tokens=['clear', 'sample'], marked={0, 1}, fallback=True)
        return result
    def observed_recovery(controller, failure):
        attempts.append(copy.deepcopy(failure.units))
        return recover(controller, failure)
    monkeypatch.setattr(vp.vision_reading, 'align', defective_alignment)
    monkeypatch.setattr(ur.Controller, 'recover', observed_recovery)
    monkeypatch.setattr(ur.Controller, 'refine_units', conservative_refinement)
    path, report, docs = ink.convert_ink(tmp_path, monkeypatch, pages)
    expected = ({(0, None)} | {(1, f'body{block}') for block in range(3)}
                if page_fallback else {(page, f'body{block}') for page in (0, 1) for block in range(3)})
    assert len(attempts) == 1, 'Independent bad blocks must not restart the book one at a time.'
    assert {(unit['page'], unit['block']) for unit in attempts[0]} == expected
    assert len(attempts[0]) == len(expected), 'Every authoritative source owner is retained once.'
    if page_fallback:
        whole = next(unit for unit in attempts[0] if unit['page'] == 0)
        failures = whole['source_failure_units']
        assert len(failures) == 3
        assert {(unit['page'], unit['block']) for unit in failures} == {(0, 'body0'), (0, None), (0, 'body2')}
        assert all(unit.get('source_failure_reason') for unit in failures)
        assert report['pages'][0]['source'] == 'original_source'
    assert report['pages'][2]['source'] == 'vision'
    assert 'The final clear sample remains readable.' in ' '.join(ink.plain(root) for root in docs.values())
    text = ' '.join(ink.plain(root) for root in docs.values())
    assert all(word not in text for word in ('zzqv', 'rrjx', 'ppqx'))
    for page in ((1,) if page_fallback else (0, 1)):
        units = report['pages'][page]['degraded_units']
        assert {unit['block'] for unit in units} == {f'body{i}' for i in range(3)}
        assert all(unit.get('source_image') for unit in units)
    assert path.exists()


@pytest.mark.parametrize('fatal', [False, True])
def test_unrelated_and_fatal_failures_are_not_collected(tmp_path, monkeypatch, fatal):
    first = ink.InkPage()
    first.text('The first clear sample remains readable.', 'first')
    second = ink.InkPage()
    second.text('The second clear sample remains readable.', 'second')
    compile_page = vp.compile_page
    recover = ur.Controller.recover
    calls = []
    seen_at_recovery = []
    rejected = False
    def fail_once(answer, words, display, pno, *args, **kwargs):
        nonlocal rejected
        calls.append(pno)
        if pno == 0 and not rejected:
            rejected = True
            if fatal:
                raise MemoryError('Synthetic allocation failure.')
            raise ValueError('Synthetic independent structural failure.')
        return compile_page(answer, words, display, pno, *args, **kwargs)
    def observed_recovery(controller, failure):
        seen_at_recovery.append(list(calls))
        return recover(controller, failure)
    monkeypatch.setattr(vp, 'compile_page', fail_once)
    monkeypatch.setattr(ur.Controller, 'recover', observed_recovery)
    if fatal:
        original = tmp_path / 'synthetic.epub'
        original.write_bytes(b'Existing artifact remains intact.')
        with pytest.raises(MemoryError, match='Synthetic allocation failure'):
            ink.convert_ink(tmp_path, monkeypatch, [first, second])
        assert calls == [0]
        assert seen_at_recovery == []
        assert original.read_bytes() == b'Existing artifact remains intact.'
    else:
        _, _, docs = ink.convert_ink(tmp_path, monkeypatch, [first, second])
        assert seen_at_recovery == [[0]], 'Non-quality failures retain immediate recovery.'
        assert 'The second clear sample remains readable.' in ' '.join(ink.plain(root) for root in docs.values())
