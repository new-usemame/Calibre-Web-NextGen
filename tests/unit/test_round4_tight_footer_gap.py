"""Tightly spaced isolated source footer marks stay outside live reading."""
import importlib.util
from pathlib import Path
import pytest
_spec = importlib.util.spec_from_file_location('tight_footer_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)

@pytest.mark.parametrize('glyph,role', [('I', 'other'), ('O', 'figure')])
def test_tightly_spaced_small_footer_is_not_prose_or_raster(tmp_path, monkeypatch, glyph, role):
    page = h.InkPage()
    lines = ['I record an ordinary account of this useful mechanism.',
             'The final ordinary line explains another careful source lesson']
    page.text(lines, 'body', y=351, size=9)
    page.page.insert_text((178, 377), glyph, fontsize=7)
    page.blocks.append(h.model_block(glyph, 'footer', role, [175, 367, 190, 381]))
    _, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    assert h.body_texts(docs) == [' '.join(lines)], 'a small isolated source footer must not become a standalone body word'
    assert not [x for x in result['images'].values() if x['kind'] == 'figure'], 'a small isolated source footer must not become a body raster'
