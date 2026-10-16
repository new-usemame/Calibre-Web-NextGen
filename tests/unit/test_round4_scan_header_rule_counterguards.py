"""Independent original-pixel rule and source-family counterguards."""
from pathlib import Path
import importlib.util
import copy
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline, vision_pixels
from cps.services.reflow.source_display import SourceDisplay
helper = Path(vision_pipeline.__file__).resolve().parents[3] / 'tests/unit/test_round4_defects.py'
spec = importlib.util.spec_from_file_location('header_rule_counter_helpers', helper)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


@pytest.mark.parametrize('kind', ['short_underline', 'thick_border', 'bottom_clipped_thick_border', 'connected_vertical_border'])
def test_original_rule_proof_rejects_short_thick_or_unmeasured_border(tmp_path, kind):
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=300, height=400)
        page.insert_text((100, 25), 'A source title', fontsize=10)
        box = list(page.get_text('words')[0][:4])
        row_words = page.get_text('words')
        box = [min(w[0] for w in row_words), min(w[1] for w in row_words),
               max(w[2] for w in row_words), max(w[3] for w in row_words)]
        y = box[3] + 8
        if kind == 'short_underline':
            page.draw_line((110, y), (150, y), width=.5)
        elif kind == 'thick_border':
            page.draw_rect((20, y, 280, y+6), color=(0,0,0), fill=(0,0,0))
        elif kind == 'connected_vertical_border':
            page.draw_line((20, y), (280, y), width=.5)
            page.draw_line((20, y), (20, y+15), width=.5)
        else:
            bottom = min(page.rect.height*.16, box[3] + (box[3]-box[1])*3)
            page.draw_rect((20, bottom-.7, 280, bottom+6.3), color=(0,0,0), fill=(0,0,0))
        assert vision_pixels.header_separating_rule(SourceDisplay(doc, 0), box) is None, 'a clipped border has unmeasured thickness and cannot prove a thin separating rule'
    finally:
        doc.close()


@pytest.mark.parametrize('guard', ['two_source_pages', 'figure', 'table', 'caption', 'footnote', 'figure_rule', 'table_rule', 'caption_rule', 'footnote_rule'])
def test_unproved_or_other_owned_source_header_family_cannot_consume_heading(tmp_path, guard):
    source = pymupdf.open()
    scans = {}
    try:
        for n in range(4):
            p = h.InkPage()
            title = p.text('A cobalt title', 'head', 'heading' if n == 0 else 'running_head', x=105, y=22, size=10)
            p.page.draw_line((23,32), (280,32), width=.5)
            p.text('Ordinary source text remains printed below the rule.', 'body', y=125, size=8)
            if guard == 'two_source_pages' and n == 3:
                title['role'] = 'heading'
            if guard in ('figure','table','caption','footnote') and n == 0:
                p.blocks.append(h.model_block('An independently owned source region', 'owner', guard, [90,8,240,31], marker='7' if guard=='footnote' else ''))
            if guard.endswith('_rule') and n == 0:
                role = guard.removesuffix('_rule')
                p.blocks.append(h.model_block('An independently owned rule territory', 'rule-owner', role, [20,29,280,35], marker='7' if role=='footnote' else ''))
            image, words, answer = p.finish()
            page = source.new_page(width=300, height=400)
            page.insert_image(page.rect, stream=image)
            scans[n] = dict(answer=copy.deepcopy(answer), optical=words, display=SourceDisplay(source,n), regions={}, geometry_repairs=[])
        # PyMuPDF invalidates earlier Page handles when new pages are added;
        # bind all displays only after the complete source document exists.
        for n, scan in scans.items():
            scan['display'] = SourceDisplay(source,n)
        vision_pipeline._source_header_ownership(scans)
        assert scans[0]['answer']['blocks'][0]['role'] == 'heading', 'fewer than three source pages or a figure/table/caption/note owner cannot prove disposable furniture'
    finally:
        source.close()
