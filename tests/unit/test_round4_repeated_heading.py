"""A source chapter heading cannot be erased because a running head repeats it."""
import importlib.util
from pathlib import Path
import pytest

_spec = importlib.util.spec_from_file_location('repeat_heading_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)

@pytest.mark.parametrize('chapter_opening', [True, False])
def test_repeated_margin_heading_keeps_authored_chapter_but_removes_furniture(tmp_path, monkeypatch, chapter_opening):
    title = 'A synthetic chapter names the violet mechanism'
    pages = []
    for index in range(2):
        page = h.InkPage()
        page.text(title, 'title', 'heading' if chapter_opening and index == 0 else 'running_head', y=40, size=11)
        page.text(['Ordinary source prose explains a bounded example.',
                   'Each paragraph remains useful live reading material.'], 'body', y=80, size=9)
        pages.append(page)
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, pages)
    headings = [node for node in h.body_nodes(docs) if node.tag.endswith('}h2') and title in h.plain(node)]
    assert len(headings) == int(chapter_opening), 'the authored chapter title remains exactly once; running furniture is excluded'
    assert title not in '\n'.join(h.body_texts(docs))
    if chapter_opening:
        assert {s.get('data-source-page') for s in headings[0].findall('.//x:span[@data-source-page]', h.NS)} == {'0'}
