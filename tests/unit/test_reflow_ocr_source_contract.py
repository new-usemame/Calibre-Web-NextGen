# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader-visible OCR requirements, exercised through the real pipeline.

These are deliberately red before the source-recovery implementation. Fixtures
contain original synthetic prose, rendered to pixels; no paid model is involved.
The OCR owner may select the public opt-in setting when wiring recovery, but must
retain these assertions about text and spread order.
"""

import re
import shutil

from lxml import etree
import pymupdf
import pytest

from cps.services.reflow import pipeline


pytestmark = [pytest.mark.unit, pytest.mark.skipif(
    not shutil.which("tesseract"), reason="real OCR source contract needs Tesseract")]

LEFT = [
    "The first chapter follows the river.",
    "A wooden bridge connects the banks.",
    "Several houses stand beside the road.",
    "Their gardens contain flowers and trees.",
    "The morning light reaches the windows.",
    "People walk towards the market square.",
    "A baker opens the shop before sunrise.",
    "Fresh bread rests upon a narrow shelf.",
    "Children gather near the old fountain.",
    "They wait for a friend from the village.",
    "The final left sentence ends this account.",
]
RIGHT = [
    "The second chapter begins in the forest.",
    "Tall branches shelter the winding path.",
    "A traveller pauses beside a fallen tree.",
    "Small birds move between the leaves.",
    "The distant hills remain covered in mist.",
    "Warm sunlight gradually clears the valley.",
    "A narrow stream flows over smooth stones.",
    "The traveller follows its course downhill.",
    "An open field appears beyond the woods.",
    "There is a small cottage beside the gate.",
    "The final right sentence closes the story.",
]


def _scan(*, spread=False, clockwise=0, damaged_layer=False):
    with pymupdf.open() as master:
        page = master.new_page(width=1000 if spread else 500, height=700)
        for col, lines in enumerate([LEFT, RIGHT] if spread else [LEFT]):
            for line, text in enumerate(lines):
                page.insert_text((col * 500 + 40, 90 + line * 32), text, fontsize=17)
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(2, 2).prerotate(clockwise))
        scan = pymupdf.open()
        target = scan.new_page(width=pixels.width / 2, height=pixels.height / 2)
        target.insert_image(target.rect, stream=pixels.tobytes("png"))
        if damaged_layer:
            # A useless invisible extraction layer must not outrank clear pixels.
            target.insert_text((40, 50), "xqzvv rrrr qqqq zzzzz", fontsize=12,
                               render_mode=3)
    return scan


def _reading_text(result):
    fragments = "\n".join(result.page_html.values())
    root = etree.HTML("<div>" + fragments + "</div>")
    return re.sub(r"\s+", " ", " ".join(root.itertext()))


@pytest.mark.parametrize("damaged_layer", [False, True])
def test_clear_scan_yields_readable_source_text_without_a_paid_model(damaged_layer):
    with _scan(damaged_layer=damaged_layer) as document:
        result = pipeline.run(document)
    text = _reading_text(result)
    assert LEFT[0] in text
    assert LEFT[-1] in text
    assert text.index(LEFT[0]) < text.index(LEFT[-1])


def test_sideways_spread_reads_left_page_then_right_page():
    with _scan(spread=True, clockwise=90) as document:
        result = pipeline.run(document)
    text = _reading_text(result)
    anchors = [LEFT[0], LEFT[-1], RIGHT[0], RIGHT[-1]]
    assert all(anchor in text for anchor in anchors), text
    positions = [text.index(anchor) for anchor in anchors]
    assert positions == sorted(positions)
