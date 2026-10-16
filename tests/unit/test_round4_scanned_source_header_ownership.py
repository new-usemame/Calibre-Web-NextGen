"""Source-free actual-converter controls for mislabeled marginal scan titles."""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline

helper = Path(__file__).with_name('test_round4_defects.py')
if not helper.exists():
    helper = Path(vision_pipeline.__file__).resolve().parents[3] / 'tests/unit/test_round4_defects.py'
spec = importlib.util.spec_from_file_location('scan_header_ownership_helpers', helper)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
TITLE = 'The cobalt chapter'
BODY = 'Ordinary violet source prose remains live and readable.'


def pages(mode):
    result = []
    for n in range(4):
        p = h.InkPage()
        if n == 0:
            if mode == 'mislabeled_header':
                # Header glyphs are deliberately larger than ordinary prose,
                # as in many books; being smaller than BODY is not ownership.
                p.text(TITLE, 'candidate', 'heading', x=105, y=22, size=10)
                p.page.draw_line((23, 32), (280, 32), width=.5)
            elif mode == 'large_authored_title':
                p.text(TITLE, 'candidate', 'heading', x=65, y=25, size=17)
                p.page.draw_line((23, 40), (280, 40), width=.5)
            else:
                # An equally small authored subsection below the separating
                # rule must survive even if its title repeats a running head.
                p.text('A proved marginal chapter', 'head', 'running_head', x=85, y=22, size=10)
                p.page.draw_line((23, 32), (280, 32), width=.5)
                p.text(TITLE, 'candidate', 'heading', x=24, y=70, size=10)
        else:
            p.text(['An opening cobalt chapter', 'A continuing cobalt chapter',
                    'The final cobalt chapter'][n-1], 'head', 'running_head', x=90, y=22, size=10)
            p.page.draw_line((23, 32), (280, 32), width=.5)
        p.text(str(n+41), 'folio', 'folio', x=272, y=22, size=7)
        p.text([BODY, 'Every ordinary word retains its printed paragraph order.'],
               'body', y=125, size=8)
        result.append(p)
    return result


def test_misclassified_heading_matches_original_marginal_header_family(tmp_path, monkeypatch):
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, pages('mislabeled_header'))
    visible = [h.plain(n) for n in h.body_nodes(docs)]
    assert TITLE not in visible, 'original header-family position, glyph size and separating-rule proof must override a mistaken HEADING role'
    assert sum(BODY in text for text in visible) == 4


@pytest.mark.parametrize('mode', ['large_authored_title', 'small_authored_subsection'])
def test_authored_heading_survives_near_or_below_proved_scan_header_family(tmp_path, monkeypatch, mode):
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, pages(mode))
    headings = [n for n in h.body_nodes(docs) if n.tag.endswith('}h2') and h.plain(n) == TITLE]
    assert len(headings) == 1, 'a real large title or small authored subsection cannot be erased merely by a margin family'
    assert {s.get('data-source-page') for s in headings[0].findall('.//x:span[@data-source-page]', h.NS)} == {'0'}
    assert sum(BODY in text for text in h.body_texts(docs)) == 4
