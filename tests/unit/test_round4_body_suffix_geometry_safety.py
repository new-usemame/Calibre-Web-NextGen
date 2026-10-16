"""Independent negative controls for geometry-only body suffix corroboration."""
import importlib.util
import io
from pathlib import Path
import zipfile
from PIL import Image
from cps.services.reflow import vision_pipeline

helper_path = Path(__file__).with_name('test_round4_defects.py')
if not helper_path.exists():
    helper_path = Path(__file__).parents[1] / 'test_round4_defects.py'
spec = importlib.util.spec_from_file_location('suffix_safety_helpers', helper_path)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

class DistantDiagramSuffix(h.InkPage):
    def __init__(self):
        super().__init__()
        self.text('We examine clear source facts with several ordinary useful workpieces.',
            'body', size=7, model='We examine clear source facts with several ordinary useful workpieces.')
        # A damaged broad hint overlaps a real source illustration. Exact body
        # anchors are on row50; a far diagram label is not its final word.
        self.blocks[0]['bbox'] = h.model_block('', 'unused', 'body', [23, 38, 282, 175])['bbox']
        self.figure(hint=[110, 115, 165, 140])
        self.page.insert_text((80, 130), 'kpieces:', fontsize=8, color=(1, 0, 0))
    def finish(self):
        image, words, reading = super().finish()
        changed = 0
        for word in words:
            if word['text'] == 'workpieces.':
                word['text'] = 'zzzzzzzzzz'
                changed += 1
        assert changed == 1, 'actual body ink remains intact while its optical token is damaged'
        return image, words, reading

def test_broad_body_hint_cannot_borrow_far_diagram_suffix_as_paragraph_word(tmp_path, monkeypatch):
    path, report, docs = h.convert_ink(tmp_path, monkeypatch, [DistantDiagramSuffix()])
    figures = [(src, r) for src, r in report['images'].items() if r['kind'] == 'figure']
    assert len(figures) == 1
    src, rec = figures[0]
    assert rec['bbox'][0] <= 35 and rec['bbox'][1] <= 90
    assert rec['bbox'][2] >= 260 and rec['bbox'][3] >= 175, 'full diagram survives broad but damaged model body hint'
    with zipfile.ZipFile(path) as archive:
        picture = Image.open(io.BytesIO(archive.read(next(n for n in archive.namelist() if n.endswith(src))))).convert('RGB')
    assert any(r > 130 and g < 90 and b < 90 for r, g, b in picture.getdata()), 'far authored diagram suffix must remain in illustration'
    assert not any('kpieces:' in text for text in h.body_texts(docs)), 'diagram suffix is not promoted into live body text'

def test_repeated_local_source_suffix_instances_do_not_choose_an_arbitrary_body_owner():
    candidate = 'We examine clear source facts with several ordinary useful workpieces'.casefold().split()
    optical = [dict(text=t, bbox=[24+i*20, 40, 40+i*20, 49]) for i, t in enumerate(candidate[:-1])]
    optical += [dict(text='kpieces:', bbox=[24, 58, 61, 67]),
                dict(text='kpieces:', bbox=[84, 58, 121, 67])]
    anchors = vision_pipeline._geometry_anchors(optical, list(range(len(optical))), candidate, body=True)
    assert all(i < len(candidate)-1 for i, j in anchors), 'two locally plausible suffix instances cannot prove which physical source word owns the single model word'
    assert len(anchors) == len(candidate)-1, 'all exact ordinary body anchors survive the conservative ambiguity rejection'
