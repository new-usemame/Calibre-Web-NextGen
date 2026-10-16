"""A recovered furniture disclosure cannot split an authored page-turn sentence."""
from cps.services.reflow import vision_pipeline as vp, unit_recovery
from tests.unit.round7_recovery_test_support import h
from tests.unit.round7_public_note_contract_support import source_visible

LEFT = 'The careful observer carried the crystal toward'
RIGHT = 'the quiet laboratory beside the shaded river.'
WHOLE = LEFT + ' ' + RIGHT


def test_failed_source_header_disclosure_moves_after_complete_body_sentence(tmp_path, monkeypatch):
    left = h.InkPage()
    left.text('The amber introduction remains intact.', 'intro', y=45)
    left.text(LEFT, 'tail', y=350, next=True)
    right = h.InkPage()
    right.text('Synthetic notebook heading', 'header', 'running_head', y=22, size=7)
    right.text(RIGHT, 'head', y=45, previous=True)
    right.text('The violet conclusion remains intact.', 'conclusion', y=180)
    original = vp.bind_geometry
    reached = []

    def bind(answer, *args, **kwargs):
        for block in answer['blocks']:
            if block['id'] == 'header' and 'header' not in unit_recovery.current().blocked(1):
                reached.append(block['id'])
                raise ValueError('Synthetic source-owned header geometry rejection')
        return original(answer, *args, **kwargs)

    monkeypatch.setattr(vp, 'bind_geometry', bind)
    path, report, docs = h.convert_ink(tmp_path, monkeypatch, [left, right])
    assert reached == ['header']
    body = h.body_texts(docs)
    assert body.count(WHOLE) == 1, 'the BODY sentence must join around the neutral source control'
    assert LEFT not in body and RIGHT not in body
    assert body.count('The amber introduction remains intact.') == 1
    assert body.count('The violet conclusion remains intact.') == 1
    assert not any('Synthetic notebook heading' in text for text in body)
    assert {(u['page'], u.get('block')) for u in report['degraded_units']} == {(1, 'header')}
    source_visible(path, report, {0, 1})
    nodes = [node for root in docs.values() for body in root.findall('.//x:body', h.NS) for node in body]
    complete = next(node for node in nodes if node.tag.endswith('}p') and h.plain(node) == WHOLE)
    assert not any(a.get('class') == 'source-disclosure-ref' for a in complete.findall('.//x:a', h.NS)), 'source notice must remain at the paragraph boundary'


def test_clean_source_header_keeps_same_authored_sentence_continuous(tmp_path, monkeypatch):
    left = h.InkPage()
    left.text('The amber introduction remains intact.', 'intro', y=45)
    left.text(LEFT, 'tail', y=350, next=True)
    right = h.InkPage()
    right.text('Synthetic notebook heading', 'header', 'running_head', y=22, size=7)
    right.text(RIGHT, 'head', y=45, previous=True)
    right.text('The violet conclusion remains intact.', 'conclusion', y=180)
    path, report, docs = h.convert_ink(tmp_path, monkeypatch, [left, right])
    assert h.body_texts(docs).count(WHOLE) == 1
    assert report['degraded_units'] == []
