"""Independent source-free publication controls, frozen before implementation.

All pages and text are synthetic. Actual raster ink and locked source words
exercise convert, the published EPUB, image pixels and reference destinations.
Only synthetic pages and text are used; no external source is required.
"""
import copy
import io
import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf
import pytest
from PIL import Image

from cps.services.reflow import build_epub, vision_pipeline, vision_pixels

pytestmark = pytest.mark.unit
NS = {'x': 'http://www.w3.org/1999/xhtml'}
W, H = 300, 400


def model_block(text, ident, role, rect, marker='', previous=False, next=False):
    return dict(id=ident, text=text, role=role, marker=marker,
                bbox=[rect[0] / W * 1000, rect[1] / H * 1000,
                      rect[2] / W * 1000, rect[3] / H * 1000],
                low_confidence=[], continues_previous=previous,
                continues_next=next)


class InkPage:
    def __init__(self):
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=W, height=H)
        self.blocks = []
        self.corrupt_optical_marker = False

    def text(self, lines, ident, role='body', x=24, y=50, size=9,
             marker='', previous=False, next=False, model=None, color=(0, 0, 0)):
        lines = [lines] if isinstance(lines, str) else lines
        for i, line in enumerate(lines):
            self.page.insert_text((x, y + i * (size + 5)), line,
                                  fontsize=size, color=color)
        rect = [x - 1, y - size - 3, W - 20, y + (len(lines) - 1) * (size + 5) + 4]
        block = model_block(model if model is not None else ' '.join(lines),
                            ident, role, rect, marker, previous, next)
        self.blocks.append(block)
        return block

    def figure(self, ident='art', hint=None, caption=True):
        # One connected illustration, with independently recognizable corner ink.
        rect = [35, 90, 260, 175]
        self.page.draw_rect(rect, color=(0, 0, 0), fill=(.85, .85, .85), width=2)
        self.page.draw_line((35, 90), (260, 175), color=(0, 0, 0), width=2)
        for r in ([35, 90, 43, 98], [252, 90, 260, 98],
                  [35, 167, 43, 175], [252, 167, 260, 175]):
            self.page.draw_rect(r, color=(0, 0, 1), fill=(0, 0, 1))
        self.blocks.append(model_block('A synthetic connected diagram', ident,
                                       'figure', hint or rect))
        if caption:
            self.text('A connected synthetic diagram.', 'cap', 'caption', y=190, size=7)
        return rect

    def reference(self, number=7, raised=True, corrupted=False, ident='body'):
        # Full-height parentheses deliberately prevent a whole-word-height rule.
        prefix = 'The clear crystal'
        self.page.insert_text((24, 55), prefix, fontsize=9)
        x = 24 + pymupdf.get_text_length(prefix, fontsize=9)
        self.page.insert_text((x, 55), '(', fontsize=9)
        px = x + pymupdf.get_text_length('(', fontsize=9)
        self.page.insert_text((px, 51 if raised else 55), str(number),
                              fontsize=5 if raised else 9)
        px += pymupdf.get_text_length(str(number), fontsize=5 if raised else 9)
        self.page.insert_text((px, 55), '), carries a simple explanation.', fontsize=9)
        self.page.insert_text((24, 69), 'Another ordinary line keeps the source region coherent.', fontsize=9)
        text = f'The clear crystal({1 if corrupted else number}), carries a simple explanation. Another ordinary line keeps the source region coherent.'
        self.blocks.append(model_block(text, ident, 'body', [23, 41, 282, 73]))
        self.corrupt_optical_marker = corrupted

    def finish(self):
        words = [{'text': w[4], 'bbox': list(w[:4]), 'confidence': 99}
                 for w in self.page.get_text('words', sort=True)]
        if self.corrupt_optical_marker:
            changed = 0
            for word in words:
                before = word['text']
                word['text'] = re.sub(r'(?<![0-9])7(?=\))', '1', before) if ')' in before else '1' if before == '7' else before
                changed += int(before != word['text'])
            assert changed, 'synthetic optical error must leave the actual raised digit ink unchanged'
        # No hidden or native text is transferred to the scanned test input.
        image = self.page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes('png')
        self.doc.close()
        return image, words, {'blocks': self.blocks}


def convert_ink(tmp_path, monkeypatch, pages):
    prepared = [p.finish() for p in pages]
    doc = pymupdf.open()
    for image, _, _ in prepared:
        page = doc.new_page(width=W, height=H)
        page.insert_image(page.rect, stream=image)
    monkeypatch.setattr(vision_pixels, 'words',
                        lambda display, cache: copy.deepcopy(prepared[display.pno][1]))

    class Reader:
        cache = tmp_path / 'synthetic-cache' / 'readings'
        calls = 0

        def read(self, image):
            value = prepared[self.calls][2]
            self.calls += 1
            return {'answer': copy.deepcopy(value), 'cached': True, 'new_cost_usd': 0}

    reader = Reader()
    path = tmp_path / 'synthetic.epub'
    try:
        result = vision_pipeline.convert(doc, str(path), reader)
    finally:
        doc.close()
    assert reader.calls == len(pages), 'exactly one synthetic read per source page'
    assert build_epub.validate(str(path)) == []
    with zipfile.ZipFile(path) as archive:
        docs = {name: ET.fromstring(archive.read(name)) for name in archive.namelist()
                if name.endswith('.xhtml') and not name.endswith('nav.xhtml')
                and not name.endswith('vision-about.xhtml')}
    return path, result, docs


def body_nodes(docs):
    return [node for root in docs.values() for body in root.findall('x:body', NS)
            for node in body if node.tag.rsplit('}', 1)[-1] in ('p', 'figure', 'h2')]


def plain(node):
    node = copy.deepcopy(node)
    for parent in node.iter():
        for child in list(parent):
            if child.tag.endswith('}a'):
                tail = child.tail or ''
                parent.remove(child)
                parent.text = (parent.text or '') + tail
    return re.sub(r'\s+', ' ', ''.join(node.itertext())).strip()


def body_texts(docs):
    return [plain(n) for n in body_nodes(docs)
            if n.tag.endswith('}p') and n.get('class') != 'caption']


def refs_and_notes(docs):
    refs = [(name, a) for name, root in docs.items() for a in root.findall('.//x:a', NS)
            if a.get('class') == 'noteref']
    notes = [(name, a) for name, root in docs.items() for a in root.findall('.//x:aside', NS)
             if 'vision-disclosure' not in (a.get('class') or '')]
    return refs, notes


def destination(name, href):
    file, ident = href.split('#', 1)
    return (posixpath.normpath(posixpath.join(posixpath.dirname(name), file))
            if file else name), ident


def assert_exact_returns(docs, count):
    refs, notes = refs_and_notes(docs)
    assert len(refs) == count, 'every source-proven printed reference must be linked'
    targets = {(name, note.get('id')): note for name, note in notes}
    for name, ref in refs:
        target = destination(name, ref.get('href'))
        assert target in targets
        backs = targets[target].findall('.//x:a', NS)
        assert any(destination(target[0], a.get('href')) == (name, ref.get('id'))
                   for a in backs if a.get('href') and '#' in a.get('href'))
    return refs, notes


@pytest.mark.parametrize('hint', ['tiny', 'overbroad'])
def test_complete_figure_crop_preserves_whole_ink_and_excludes_prose(tmp_path, monkeypatch, hint):
    page = InkPage()
    page.text(['A readable crimson paragraph belongs to live text.',
               'Every ordinary source word stays outside the illustration.'],
              'prose', color=(1, 0, 0))
    rect = page.figure(hint=[110, 115, 165, 140] if hint == 'tiny' else [10, 25, 280, 198])
    path, result, docs = convert_ink(tmp_path, monkeypatch, [page])
    figures = [(src, rec) for src, rec in result['images'].items() if rec['kind'] == 'figure']
    assert len(figures) == 1
    src, rec = figures[0]
    assert rec['bbox'][0] <= rect[0] and rec['bbox'][1] <= rect[1]
    assert rec['bbox'][2] >= rect[2] and rec['bbox'][3] >= rect[3]
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(src))
        image = Image.open(io.BytesIO(archive.read(name))).convert('RGB')
    blue = [(x, y) for y in range(image.height) for x in range(image.width)
            if (lambda p: p[2] > 130 and p[0] < 100 and p[1] < 100)(image.getpixel((x, y)))]
    assert blue and min(x for x, y in blue) < image.width * .1 and max(x for x, y in blue) > image.width * .9
    assert min(y for x, y in blue) < image.height * .15 and max(y for x, y in blue) > image.height * .85
    assert not any(r > 130 and g < 90 and b < 90 for r, g, b in image.getdata()), 'figure may not rasterize the crimson prose'
    assert len(body_texts(docs)) == 1


def test_same_column_sentence_surrounds_art_as_one_live_paragraph(tmp_path, monkeypatch):
    page = InkPage()
    first = 'The patient travellers followed a winding route beneath'
    last = 'a quiet canopy before reaching their destination safely.'
    page.text(first, 'first')
    page.figure()
    page.text(last, 'last', y=218)
    _, _, docs = convert_ink(tmp_path, monkeypatch, [page])
    assert body_texts(docs) == [first + ' ' + last]
    nodes = body_nodes(docs)
    assert nodes[0].tag.endswith('}p') and nodes[1].tag.endswith('}figure')


@pytest.mark.parametrize('barrier', ['terminal', 'indent', 'heading', 'table'])
def test_real_paragraph_or_structure_barrier_is_not_moved_across_art(tmp_path, monkeypatch, barrier):
    page = InkPage()
    first = 'The patient travellers followed a winding route beneath'
    if barrier == 'terminal':
        first += '.'
    page.text(first, 'first', next=True)
    page.figure()
    if barrier == 'heading':
        page.text('A Genuine New Section', 'section', 'heading', y=211, size=12)
    elif barrier == 'table':
        page.text('A 10 B 20 C 30', 'table', 'table', y=211, size=8)
    last = 'a quiet canopy before reaching their destination safely.'
    page.text(last, 'last', x=43 if barrier == 'indent' else 24, y=245, previous=True)
    if barrier == 'indent':
        continuation = 'An ordinary second line returns to the column margin.'
        page.page.insert_text((24, 259), continuation, fontsize=9)
        page.blocks[-1]['text'] += ' ' + continuation
        page.blocks[-1]['bbox'] = [23 / W * 1000, 232 / H * 1000, 280 / W * 1000, 263 / H * 1000]
    _, _, docs = convert_ink(tmp_path, monkeypatch, [page])
    body = body_texts(docs)
    assert len(body) == 2 and body[0] == first
    assert body[1].startswith(last)
    nodes = body_nodes(docs)
    assert nodes[1].tag.endswith('}figure')


def test_deferred_art_waits_for_three_page_paragraph_end(tmp_path, monkeypatch):
    first, second, third = InkPage(), InkPage(), InkPage()
    a = 'The patient travellers followed a winding route beneath'
    b = 'a quiet canopy and then continued further into'
    c = 'the peaceful valley while passing several shallow pools beyond'
    d = 'the final bend before arriving at their destination safely.'
    first.text(a, 'first', next=True)
    first.figure()
    first.text(b, 'tail', y=218, next=True)
    second.text(c, 'middle', previous=True, next=True)
    third.text(d, 'end', previous=True)
    third.text('Separate ordinary prose begins a genuinely new paragraph.', 'new', x=39, y=105)
    _, _, docs = convert_ink(tmp_path, monkeypatch, [first, second, third])
    assert body_texts(docs) == [' '.join([a, b, c, d]), 'Separate ordinary prose begins a genuinely new paragraph.']
    nodes = body_nodes(docs)
    assert [n.tag.rsplit('}', 1)[-1] for n in nodes] == ['p', 'figure', 'p', 'p']
    assert nodes[2].get('class') == 'caption'
    assert {s.get('data-source-page') for s in nodes[0].findall('.//x:span[@data-source-page]', NS)} == {'0', '1', '2'}


def test_note_continuations_and_repeated_furniture_stay_outside_body(tmp_path, monkeypatch):
    first, second, third = InkPage(), InkPage(), InkPage()
    first.text('A closing ordinary paragraph cites a source note.', 'body', model='A closing ordinary paragraph cites<n:n7> a source note.')
    first.text('7 A synthetic source note begins with careful observations and', 'n7', 'footnote', y=320, size=7, marker='7', model='A synthetic source note begins with careful observations and')
    first.text('additional details continue the same small printed note here', 'tail', y=336, size=7)
    second.text('NOTES', 'heading', x=120, y=35, size=9)
    second.text('further observations finish that same source note precisely.', 'continuation', y=55, size=7)
    second.text('8 A second small source note discusses another object and', 'n8', 'footnote', y=130, size=7, marker='8', model='A second small source note discusses another object and')
    second.text('Printed in Sampletown', 'imprint', x=98, y=375, size=7)
    third.text('NOTES', 'heading', x=120, y=35, size=9)
    third.text('further material completes the second note without body prose.', 'continuation', y=55, size=7)
    third.text('Printed in Sampletown', 'imprint', x=98, y=375, size=7)
    _, _, docs = convert_ink(tmp_path, monkeypatch, [first, second, third])
    body = '\n'.join(body_texts(docs))
    assert 'additional details' not in body and 'further observations' not in body and 'further material' not in body
    assert 'NOTES' not in body and 'Printed in Sampletown' not in body
    _, notes = refs_and_notes(docs)
    note7 = next(n for _, n in notes if 'A synthetic source note' in ''.join(n.itertext()))
    note8 = next(n for _, n in notes if 'A second small source note' in ''.join(n.itertext()))
    assert 'additional details' in ''.join(note7.itertext()) and 'further observations' in ''.join(note7.itertext())
    assert 'further material' in ''.join(note8.itertext())
    assert {s.get('data-source-page') for s in note7.findall('.//x:span', NS)} == {'0', '1'}
    assert {s.get('data-source-page') for s in note8.findall('.//x:span', NS)} == {'1', '2'}


@pytest.mark.parametrize('corrupted', [False, True])
def test_raised_inner_digits_inside_full_height_parentheses_link_remote_multiple_origins(tmp_path, monkeypatch, corrupted):
    first, second, last = InkPage(), InkPage(), InkPage()
    first.reference(raised=True, corrupted=corrupted)
    second.reference(raised=True, corrupted=corrupted)
    last.text('7 A synthetic source note explains the crystal.', 'note', 'footnote', y=320, size=7, marker='7', model='A synthetic source note explains the crystal.')
    _, _, docs = convert_ink(tmp_path, monkeypatch, [first, second, last])
    refs, notes = assert_exact_returns(docs, 2)
    assert len({destination(name, a.get('href')) for name, a in refs}) == 1
    assert all(''.join(a.itertext()) == '7' for _, a in refs)


def test_ordinary_baseline_parenthetical_list_numbers_do_not_invent_notes(tmp_path, monkeypatch):
    first, last = InkPage(), InkPage()
    first.reference(raised=False)
    last.text('7 A separate printed note has no raised origin here.', 'note', 'footnote', y=320, size=7, marker='7', model='A separate printed note has no raised origin here.')
    _, _, docs = convert_ink(tmp_path, monkeypatch, [first, last])
    assert refs_and_notes(docs)[0] == []
    assert 'crystal(7),' in '\n'.join(body_texts(docs))


def test_ordinary_math_numbers_are_not_remote_note_references(tmp_path, monkeypatch):
    first, last = InkPage(), InkPage()
    prose = 'A simple calculation uses (7) plus (9) in an ordinary numbered list.'
    first.text(prose, 'body')
    last.text('7 A separate printed note discusses one topic.', 'n7', 'footnote', y=300, size=7, marker='7', model='A separate printed note discusses one topic.')
    last.text('9 Another printed note discusses another topic.', 'n9', 'footnote', y=340, size=7, marker='9', model='Another printed note discusses another topic.')
    _, _, docs = convert_ink(tmp_path, monkeypatch, [first, last])
    assert refs_and_notes(docs)[0] == []
    assert body_texts(docs) == [prose]


def test_new_chapter_body_is_not_promoted_to_previous_footer_note(tmp_path, monkeypatch):
    first, last = InkPage(), InkPage()
    first.text('A complete ordinary paragraph refers to a local note.', 'body', model='A complete ordinary paragraph refers<n:local> to a local note.')
    first.text('7 A small printed note ends at the scope boundary and', 'local', 'footnote', y=330, size=7, marker='7', model='A small printed note ends at the scope boundary and')
    last.text('A Genuine New Chapter', 'chapter', 'heading', y=50, size=12)
    prose = 'Ordinary full size body prose begins beneath this new chapter.'
    last.text(prose, 'body', y=90, size=9)
    _, _, docs = convert_ink(tmp_path, monkeypatch, [first, last])
    assert prose in body_texts(docs)
    _, notes = refs_and_notes(docs)
    assert all(prose not in ''.join(note.itertext()) for _, note in notes)


def test_ambiguous_source_proven_remote_note_marker_fails_explicitly(tmp_path, monkeypatch):
    origin, one, two = InkPage(), InkPage(), InkPage()
    origin.reference(raised=True)
    one.text('7 First competing source note gives one explanation.', 'one', 'footnote', y=320, size=7, marker='7', model='First competing source note gives one explanation.')
    two.text('7 Second competing source note gives another explanation.', 'two', 'footnote', y=320, size=7, marker='7', model='Second competing source note gives another explanation.')
    from cps.services.reflow import vision_pipeline as vp
    from tests.unit.round7_public_note_contract_support import source_visible
    strict=vp._literal_note_refs;rejected=[]
    def observed(*args,**kwargs):
        try:return strict(*args,**kwargs)
        except ValueError as error:rejected.append(str(error));raise
    monkeypatch.setattr(vp,'_literal_note_refs',observed)
    path,report,docs=convert_ink(tmp_path, monkeypatch, [origin, one, two])
    assert rejected,'strict helper must still reject ambiguous selected remote owner'
    source_visible(path,report,{0,1,2})
    refs,notes=refs_and_notes(docs)
    assert refs==[],'ambiguous original glyph must not invent either normal note owner'
    assert len(notes)==2
    assert sum('First competing source note' in ''.join(n.itertext()) for _,n in notes)==1
    assert sum('Second competing source note' in ''.join(n.itertext()) for _,n in notes)==1


def test_typed_local_note_id_has_priority_over_competing_remote_label(tmp_path, monkeypatch):
    first, last = InkPage(), InkPage()
    first.text('A complete ordinary paragraph cites its local note.', 'body', model='A complete ordinary paragraph cites<n:local> its local note.')
    first.text('7 The local source note owns this explicit reference.', 'local', 'footnote', y=320, size=7, marker='7', model='The local source note owns this explicit reference.')
    last.text('7 Another source note uses the same printed number.', 'remote', 'footnote', y=320, size=7, marker='7', model='Another source note uses the same printed number.')
    _, _, docs = convert_ink(tmp_path, monkeypatch, [first, last])
    refs, notes = assert_exact_returns(docs, 1)
    name, ref = refs[0]
    target_name, target_id = destination(name, ref.get('href'))
    note = next(n for file, n in notes if (file, n.get('id')) == (target_name, target_id))
    assert 'The local source note' in ''.join(note.itertext())
