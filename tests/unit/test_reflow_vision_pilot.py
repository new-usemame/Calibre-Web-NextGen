"""Independent v4 behavior checks using synthetic text written for these tests.

Transport tests drive the actual Reader and persistent Ledger. Publication tests
inspect the final EPUB, rather than trusting internal counters or HTML snippets.
"""
import io
import json
import posixpath
import shutil
import zipfile
from xml.etree import ElementTree as ET

import pymupdf
import pytest
import requests
from PIL import Image

from cps.services.reflow import build_epub, vision_pipeline, vision_reading
from cps.services.reflow.ledger import CapExceeded, Ledger
from cps.services.reflow.source_display import SourceDisplay

pytestmark = pytest.mark.unit
NS = {'x': 'http://www.w3.org/1999/xhtml'}
# The converter's pixel guard runs the local OCR engine the image ships. Without
# it each page degrades to its retained source image, so these assertions about
# live text cannot hold.
real_ocr = pytest.mark.skipif(not shutil.which('tesseract'), reason='needs installed OCR engine')


def block(text, *, ident='b1', role='body', marker='', bbox=(50, 50, 950, 600),
          low=(), previous=False, next=False):
    return dict(id=ident, role=role, text=text, marker=marker, bbox=list(bbox),
                low_confidence=list(low), continues_previous=previous,
                continues_next=next)


def answer(*blocks):
    return {'blocks': list(blocks)}


def jpeg(color='white'):
    buf = io.BytesIO()
    Image.new('RGB', (90, 120), color).save(buf, format='JPEG')
    return buf.getvalue()


def reply(reading=None, *, cost=.0004, finish='stop'):
    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {'id': 'synthetic-receipt', 'model': 'openai/gpt-6-luna',
                    'usage': {'cost': cost}, 'choices': [{'finish_reason': finish,
                    'message': {'content': json.dumps(reading or answer(block('A synthetic sentence.')))}}]}
    return Reply()


def test_cache_rebuild_dispatches_once_and_never_rebills(tmp_path, monkeypatch):
    ledger_path = tmp_path / 'paid.jsonl'
    calls = []
    ledger = Ledger(ledger_path, .05)

    def post(*args, **kwargs):
        # Liability must exist on disk before a socket could open.
        restarted = Ledger(ledger_path, .05)
        assert restarted.pending_usd() > 0
        calls.append(kwargs['json'])
        return reply()

    monkeypatch.setattr(requests, 'post', post)
    first = vision_reading.Reader('inert-key', tmp_path / 'cache', ledger).read(jpeg())
    rebuilt_ledger = Ledger(ledger_path, .05)
    rebuilt = vision_reading.Reader('inert-key', tmp_path / 'cache', rebuilt_ledger).read(jpeg())
    assert len(calls) == 1
    assert first['cached'] is False and rebuilt['cached'] is True
    assert rebuilt['new_cost_usd'] == 0
    assert rebuilt_ledger.spent() == pytest.approx(.0004)
    assert rebuilt_ledger.pending_usd() == 0
    assert calls[0]['reasoning']['effort'] == 'low'
    assert calls[0]['provider']['allow_fallbacks'] is False


def test_cap_refuses_before_dispatch(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(requests, 'post', lambda *a, **k: calls.append(k))
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', Ledger(tmp_path / 'paid.jsonl', .001))
    with pytest.raises(CapExceeded):
        reader.read(jpeg())
    assert calls == []


@pytest.mark.parametrize('failure', ['timeout', 'truncated', 'invalid-schema'])
def test_unusable_or_lost_response_cannot_retry_same_paid_page(tmp_path, monkeypatch, failure):
    path = tmp_path / 'paid.jsonl'
    calls = []

    def post(*args, **kwargs):
        calls.append(True)
        if failure == 'timeout':
            raise requests.Timeout('synthetic lost reply')
        if failure == 'invalid-schema':
            return reply({'blocks': [{'bad': 'shape'}]})
        return reply(finish='length')

    monkeypatch.setattr(requests, 'post', post)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', Ledger(path, .05))
    with pytest.raises((requests.Timeout, vision_reading.model.UnusableAnswer, ValueError)):
        reader.read(jpeg())
    restored = Ledger(path, .05)
    if failure == 'timeout':
        assert restored.pending_usd() > 0 and restored.spent() == 0
    else:
        assert restored.spent() == pytest.approx(.0004)
    again = vision_reading.Reader('inert-key', tmp_path / 'cache', restored)
    with pytest.raises(vision_reading.model.UncertainBilling):
        again.read(jpeg())
    assert len(calls) == 1


def test_overbound_receipt_retains_actual_bill_and_stops_new_spend(tmp_path, monkeypatch):
    """An unexpectedly large metered receipt must not vanish behind its smaller hold."""
    path = tmp_path / 'paid.jsonl'
    calls = []

    def post(*args, **kwargs):
        calls.append(True)
        return reply(cost=.08)

    monkeypatch.setattr(requests, 'post', post)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', Ledger(path, .05))
    with pytest.raises(vision_reading.model.UncertainBilling):
        reader.read(jpeg())
    restored = Ledger(path, .05)
    assert restored.spent() == pytest.approx(.08), 'observed actual bill must survive restart'
    with pytest.raises(CapExceeded):
        vision_reading.Reader('inert-key', tmp_path / 'cache', restored).read(jpeg('gray'))
    assert len(calls) == 1


def words(text):
    return [{'text': t, 'bbox': [10 + i * 8, 20, 17 + i * 8, 30]} for i, t in enumerate(text.split())]


@pytest.mark.parametrize('mode', ['added', 'dropped'])
def test_unaligned_added_or_dropped_line_falls_back_with_disclosure(mode):
    original = 'The old path crosses a stream and reaches the bridge before sunset.'
    invented = 'Imaginary bright orange birds sing loudly above an impossible palace today'
    model_text = original + ' ' + invented if mode == 'added' else 'The old path crosses'
    result = vision_reading.align(model_text, words(original))
    assert result['fallback'] is True
    assert result['tokens'] == original.split()
    assert result['marked'], 'fallback must visibly disclose its uncertainty'


def test_short_spelling_corrections_publish_vision_words_without_line_fallback():
    original = 'The narrovv path follows the stxeam beside the old bridqe today.'
    corrected = 'The narrow path follows the stream beside the old bridge today.'
    result = vision_reading.align(corrected, words(original))
    assert result['tokens'] == corrected.split() and result['fallback'] is False
    assert {result['tokens'][i] for i in result['marked']} == {'narrow', 'stream', 'bridge'}


def test_layer_agreement_is_certain_even_when_reader_flags_low_confidence():
    text = 'A clearly printed word.'
    result = vision_reading.align(text, words(text), [2])
    assert result['marked'] == set(), 'the trust rule says agreement gets no mark'


def test_page_without_layer_marks_only_reader_low_confidence_words():
    text = 'The narrow path reaches a bridge.'
    result = vision_reading.align(text, [], [1], has_layer=False)
    assert result['tokens'] == text.split()
    assert result['marked'] == {1} and result['fallback'] is False


@pytest.mark.parametrize('change', [
    {'id': 4}, {'bbox': None}, {'bbox': '1234'}, {'low_confidence': None},
    {'low_confidence': ['0']}, {'continues_next': 'false'}, {'role': 'not-a-role'},
    {'bbox': [0, 0, float('nan'), 1000]}, {'text': 'Text<n:missing>'},
])
def test_malformed_page_schema_is_rejected_as_invalid_reading(change):
    b = block('Synthetic valid text.')
    b.update(change)
    with pytest.raises(ValueError):
        vision_reading.validate(answer(b))


def scanned_doc(count=1):
    """Procedural raster pages exercise the converter route."""
    doc = pymupdf.open()
    for _ in range(count):
        p = doc.new_page(width=300, height=400)
        p.insert_image(p.rect, stream=jpeg())
    return doc


class SyntheticReader:
    def __init__(self, *readings):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        self._cache_dir = TemporaryDirectory(prefix="synthetic-vision-reader-")
        self.cache = Path(self._cache_dir.name) / "readings"
        self.readings = iter(readings)
        self.calls = 0

    def read(self, image):
        self.calls += 1
        return {'answer': next(self.readings), 'cached': True, 'new_cost_usd': 0}


def documents(path):
    with zipfile.ZipFile(path) as z:
        return {name: ET.fromstring(z.read(name)) for name in z.namelist()
                if name.endswith('.xhtml') and not name.endswith('/nav.xhtml')}


def paragraphs(docs):
    return [''.join(p.itertext()) for name, root in docs.items()
            if not name.endswith('/vision-about.xhtml') for p in root.findall('.//x:p', NS)
            if 'source-reading-annotation' not in (p.get('class') or '')]


def assert_links_resolve(docs):
    for name, root in docs.items():
        for link in root.findall('.//x:a', NS):
            href = link.get('href', '')
            if '#' not in href:
                continue
            target_file, target_id = href.split('#', 1)
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), target_file)) if target_file else name
            assert target in docs, href
            assert any(n.get('id') == target_id for n in docs[target].iter()), href


@real_ocr
def test_final_epub_note_and_disclosure_links_resolve_without_body_crops(tmp_path):
    doc = scanned_doc()
    reader = SyntheticReader(answer(
        block('The narrow<n:n1> path reaches a bridge.', low=[1]),
        block('A short printed note.', ident='n1', role='footnote', marker='1', bbox=(50, 650, 950, 800)),
        block('SYNTHETIC RUNNING HEAD', ident='rh', role='running_head', bbox=(50, 10, 950, 40)),
        block('7', ident='folio', role='folio', bbox=(450, 900, 550, 950)),
        block('A genuine diagram', ident='fig', role='figure', bbox=(50, 820, 200, 880))))
    out = tmp_path / 'notes.epub'
    result = vision_pipeline.convert(doc, str(out), reader)
    doc.close()
    assert build_epub.validate(str(out)) == []
    docs = documents(out)
    assert_links_resolve(docs)
    content = '\n'.join(ET.tostring(root, encoding='unicode') for name, root in docs.items()
                        if not name.endswith('/vision-about.xhtml'))
    assert 'SYNTHETIC RUNNING HEAD' not in content
    refs = [(name, a) for name, root in docs.items() for a in root.findall('.//x:a', NS)
            if a.get('class') == 'noteref']
    assert len(refs) == 1
    assert any('Return' == ''.join(a.itertext()) for root in docs.values() for a in root.findall('.//x:a', NS))
    assert result['disclosure_count'] == 1
    for root in docs.values():
        for p in root.findall('.//x:p', NS):
            assert p.findall('.//x:img', NS) == [], 'body sentences must remain live text'
    assert set(i['kind'] for i in result['images'].values()) == {'figure', 'vision_disclosure'}


@real_ocr
@pytest.mark.parametrize('continuation', [True, False])
def test_explicit_page_turn_flags_control_paragraph_joining(tmp_path, continuation):
    # Capitals may begin a true continuation; lowercase can begin a separate paragraph.
    left, right = ('We passed by', 'Northern Bridge before sunset.') if continuation else ('A short section', 'another section starts here.')
    doc = scanned_doc(2)
    reader = SyntheticReader(answer(block(left, next=continuation)), answer(block(right, previous=continuation)))
    out = tmp_path / 'turn.epub'
    result = vision_pipeline.convert(doc, str(out), reader)
    doc.close()
    body = paragraphs(documents(out))
    expected = [left + ' ' + right] if continuation else [left, right]
    assert body == expected, 'published paragraph boundaries must follow the page readings'
    assert result['page_joins'] == int(continuation)


def test_hidden_scanned_and_damaged_layers_route_to_vision_but_clean_native_does_not():
    doc = pymupdf.open()
    clean = doc.new_page(width=300, height=400)
    clean.insert_text((20, 50), 'A clean native sentence.')
    hidden = doc.new_page(width=300, height=400)
    hidden.insert_text((20, 50), 'Hidden OCR sentence.', render_mode=3)
    scan = doc.new_page(width=300, height=400)
    scan.insert_image(scan.rect, stream=jpeg())
    scan.insert_text((20, 50), 'OCR over scan.')
    from types import SimpleNamespace
    span = SimpleNamespace(encoding_unresolved=True)
    damaged = SimpleNamespace(text_blocks=[SimpleNamespace(lines=[SimpleNamespace(spans=[span])])])
    assert vision_pipeline.clean_native(doc[0]) is True
    assert vision_pipeline.clean_native(doc[1]) is False
    assert vision_pipeline.clean_native(doc[2]) is False
    assert vision_pipeline.clean_native(doc[0], damaged) is False
    doc.close()


def test_line_fallback_preserves_authored_note_reference(tmp_path):
    doc = scanned_doc()
    page = doc[0]
    page.insert_text((20, 50), 'The original paragraph has eleven ordinary words before its printed marker.')
    display = SourceDisplay(doc, 0)
    source_words = vision_pipeline.layer_words(doc, 0, display)
    pkg = build_epub._Package(str(tmp_path / 'fallback.epub'))
    try:
        published, report = vision_pipeline.compile_page(answer(
            block('Different invented prose<n:n1>', bbox=(0, 0, 1000, 600)),
            block('Note text.', ident='n1', role='footnote', marker='1', bbox=(0, 700, 1000, 900))),
            source_words, display, 0, pkg, {})
        assert report['blocks'][0]['fallback'] is True
        assert 'class="noteref"' in ''.join(published['body']), 'fallback must not orphan authored notes'
        assert report['unlinked_notes'] == []
    finally:
        pkg.abandon()
        doc.close()


def test_low_confidence_word_after_attached_note_keeps_its_identity():
    """Marker separation must not shift the reader's whitespace-token indices."""
    text = 'Before<n:n1> the uncertain word.'
    result = vision_reading.align(text, [], [2], has_layer=False)
    assert [result['tokens'][i] for i in result['marked']] == ['uncertain']


def test_sparse_layer_outside_body_cannot_erase_unmapped_model_paragraph(tmp_path):
    doc = scanned_doc()
    display = SourceDisplay(doc, 0)
    source = [{'text': 'HEADER', 'bbox': [10, 5, 60, 15]}]
    text = 'This complete printed paragraph contains enough words to form a separate full line.'
    pkg = build_epub._Package(str(tmp_path / 'unmapped.epub'))
    try:
        published, report = vision_pipeline.compile_page(answer(block(text, bbox=(50, 300, 950, 600))),
                                                       source, display, 0, pkg, {})
        emitted = report['blocks'][0]['published_tokens']
        assert emitted == text.split(), 'an unrelated partial layer is not fallback text for this block'
        assert report['marks'] > 0, 'unmapped content needs an uncertainty disclosure'
        assert 'paragraph' in ''.join(published['body'])
    finally:
        pkg.abandon()
        doc.close()


@real_ocr
def test_converter_uses_reader_only_for_scanned_page_and_keeps_native_live(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((20, 50), 'A native sentence stays live.')
    page = doc.new_page(width=300, height=400)
    page.insert_image(page.rect, stream=jpeg())
    reader = SyntheticReader(answer(block('A scanned sentence becomes live.')))
    out = tmp_path / 'mixed.epub'
    result = vision_pipeline.convert(doc, str(out), reader)
    doc.close()
    assert reader.calls == 1
    assert result['native_pages'] == [0] and result['vision_pages'] == [1]
    assert result['pages'][0]['source'] == 'native' and result['pages'][1]['source'] == 'vision'
    assert build_epub.validate(str(out)) == []
    body = ' '.join(paragraphs(documents(out)))
    assert 'A native sentence stays live.' in body
    assert 'A scanned sentence becomes live.' in body
    assert not result['images']


def test_optical_corroborated_long_layer_correction_keeps_vision_words_disclosed():
    printed = 'The quiet river runs beside the old stone bridge under the distant blue mountain.'
    damaged = 'u1 v2 w3 m4 n5 k6 r7 p8 q9 t10 j11 h12 f13 g14 d15 z16'
    optical = 'The quiet river runs beside the old st0ne bridge under the distant blue mountain.'
    result = vision_reading.align(printed, words(damaged), pixel_words=words(optical))
    assert result['tokens'] == printed.split() and result['fallback'] is False
    assert result['marked'] == set(range(len(printed.split()))), 'optical corroboration cannot certify disagreements'


def test_uncorroborated_hallucinated_line_falls_back_even_with_optical_evidence():
    printed = 'The quiet river runs beside the old stone bridge under the distant blue mountain.'
    hallucination = 'Invisible dragons deliver golden umbrellas while purple machines sing imaginary songs.'
    result = vision_reading.align(hallucination, words(printed), pixel_words=words(printed))
    assert result['fallback'] is True and result['tokens'] == printed.split()
    assert result['marked']


def test_corroborated_prefix_cannot_admit_a_whole_added_hallucinated_line():
    """Overall optical coverage must not hide a contiguous invented line."""
    printed = 'The quiet river runs beside the old stone bridge under the distant blue mountain while a small boat carries three travelers slowly toward a sheltered village near the water.'
    extra = 'Invisible dragons deliver golden umbrellas while purple machines sing.'
    damaged = ' '.join('broken' + str(i) for i in range(len(printed.split())))
    result = vision_reading.align(printed + ' ' + extra, words(damaged), pixel_words=words(printed))
    assert result['fallback'] is True, 'a complete added line has no optical support despite a long correct prefix'
    assert result['tokens'] == damaged.split()


def test_contiguous_disagreement_has_one_resolving_disclosure_for_all_words(tmp_path):
    doc = scanned_doc()
    display = SourceDisplay(doc, 0)
    pkg = build_epub._Package(str(tmp_path / 'grouped.epub'))
    reading = 'The narrow path passes the bridge.'
    layer = words('The narrovv peth passes the bridge.')
    # The synthetic word boxes need to lie in the model block.
    for w in layer:
        w['bbox'][1:4:2] = [80, 90]
    try:
        published, report = vision_pipeline.compile_page(answer(block(reading, bbox=(0, 0, 1000, 600))),
                                                       layer, display, 0, pkg, {})
        root = ET.fromstring('<html xmlns:epub="http://www.idpf.org/2007/ops">' + ''.join(published['body'] + published['asides']) + '</html>')
        refs = [a for a in root.iter('a') if a.get('class') == 'source-disclosure-ref']
        assert len(refs) == 1 and report['marks'] == 1
        assert report['blocks'][0]['marked'] == [1, 2]
        target = refs[0].get('href')[1:]
        note = next(n for n in root.iter() if n.get('id') == target)
        assert 'narrow path' in ''.join(note.itertext())
        assert any(a.get('href') == '#' + refs[0].get('id') for a in note.iter('a'))
        assert list(root.iter('p'))[0].findall('.//img') == []
    finally:
        pkg.abandon()
        doc.close()


def test_unmapped_paragraph_ending_in_note_still_has_disclosure(tmp_path):
    doc = scanned_doc()
    display = SourceDisplay(doc, 0)
    pkg = build_epub._Package(str(tmp_path / 'note-ending.epub'))
    text = 'A complete printed paragraph ends with its note<n:n1>'
    try:
        published, report = vision_pipeline.compile_page(answer(
            block(text, bbox=(50, 300, 950, 600)),
            block('Printed note.', ident='n1', role='footnote', marker='1', bbox=(50, 700, 950, 900))),
            [{'text': 'HEADER', 'bbox': [10, 5, 60, 15]}], display, 0, pkg, {})
        assert 'class="noteref"' in ''.join(published['body'])
        assert 'class="source-disclosure-ref"' in ''.join(published['body']), 'note tokens cannot swallow disagreement disclosure'
    finally:
        pkg.abandon()
        doc.close()


def test_concurrent_same_reader_same_image_pays_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, release = Event(), Event()
    calls = []

    def post(*a, **k):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return reply()

    monkeypatch.setattr(requests, 'post', post)
    led = Ledger(tmp_path / 'paid.jsonl', .05)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', led)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(reader.read, jpeg())
        assert entered.wait(5)
        second = pool.submit(reader.read, jpeg())
        release.set()
        results = [first.result(timeout=5), second.result(timeout=5)]
    assert len(calls) == 1
    assert sorted(r['cached'] for r in results) == [False, True]
    assert Ledger(tmp_path / 'paid.jsonl', .05).spent() == pytest.approx(.0004)


def test_concurrent_distinct_pages_cannot_share_one_remaining_budget(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    from threading import Event
    entered, release = Event(), Event()
    calls = []

    def post(*a, **k):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return reply()

    monkeypatch.setattr(requests, 'post', post)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', Ledger(tmp_path / 'paid.jsonl', .009))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(reader.read, jpeg())
        assert entered.wait(5)
        second = pool.submit(reader.read, jpeg('gray'))
        try:
            done, _ = wait([second], timeout=3, return_when=FIRST_COMPLETED)
            assert done, 'second reservation must refuse while first paid request remains in flight'
            with pytest.raises(CapExceeded):
                second.result()
        finally:
            release.set()
        first.result(timeout=5)
    assert len(calls) == 1


def test_concurrent_reader_instances_sharing_cache_cannot_pay_for_same_page_twice(tmp_path, monkeypatch):
    """Two conversion owners sharing the persistent cache must not duplicate a bill."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, BrokenBarrierError
    led = Ledger(tmp_path / 'paid.jsonl', .05)
    barrier = Barrier(2)
    reserve = led.reserve_attempt
    calls = []

    def reserve_both_before_pending(*a, **k):
        result = reserve(*a, **k)
        try:
            barrier.wait(timeout=.25)
        except BrokenBarrierError:
            pass  # A correct cache-wide lock prevents a second reservation.
        return result

    monkeypatch.setattr(led, 'reserve_attempt', reserve_both_before_pending)
    monkeypatch.setattr(requests, 'post', lambda *a, **k: (calls.append(True), reply())[1])
    readers = [vision_reading.Reader('inert-key', tmp_path / 'cache', led) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(r.read, jpeg()) for r in readers]
        # Cleanup/result collection must occur even when the cache has a write race.
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=8))
            except Exception as exc:
                outcomes.append(exc)
    assert len(calls) == 1, 'shared cache publication must claim the page before dispatch'


def test_independent_pixel_words_keep_display_geometry_and_rebuild_without_recognition(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cps.services.reflow import vision_pixels
    calls = []
    tsv = ('level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
           '1\t1\t0\t0\t0\t0\t0\t0\t600\t800\t-1\t\n'
           '5\t1\t1\t1\t1\t1\t100\t200\t60\t20\t95\tRiver\n'
           '5\t1\t1\t1\t1\t2\t180\t200\t80\t20\t91\tbridge\n')
    image = b'synthetic-encoded-display-image'
    pix = SimpleNamespace(width=600, height=800, tobytes=lambda encoding: image)
    display = SimpleNamespace(rect=pymupdf.Rect(0, 0, 300, 400), pixmap=lambda **k: pix)
    monkeypatch.setattr(vision_pixels.shutil, 'which', lambda name: '/synthetic/tesseract')

    def run(command, **kwargs):
        calls.append(command)
        assert command[1:3] == ['stdin', 'stdout']
        assert kwargs['input'] == image
        return SimpleNamespace(returncode=0, stdout=tsv.encode(), stderr=b'')

    monkeypatch.setattr(vision_pixels.subprocess, 'run', run)
    first = vision_pixels.words(display, tmp_path / 'optical')
    rebuilt = vision_pixels.words(display, tmp_path / 'optical')
    assert len(calls) == 1 and rebuilt == first
    assert [(w['text'], w['bbox']) for w in first] == [
        ('River', [50, 100, 80, 110]), ('bridge', [90, 100, 130, 110])]


def test_optical_corroboration_checks_replacement_span_without_penalizing_agreed_context():
    before = 'The old road crossed several small streams beside our quiet village'
    corrected = 'before winding through a green valley toward the tall northern bridge'
    after = 'where travelers stopped to rest and watch the distant evening clouds'
    printed = before + ' ' + corrected + ' ' + after
    damaged = before + ' ' + ' '.join('broken' + str(i) for i in range(len(corrected.split()))) + ' ' + after
    result = vision_reading.align(printed, words(damaged), pixel_words=words(printed))
    assert result['fallback'] is False, 'agreed words outside the replacement span are not missing optical text'
    assert result['tokens'] == printed.split()
    assert {result['tokens'][i] for i in result['marked']} == set(corrected.split())


def test_missing_optical_paragraph_recovery_is_disclosed_and_figure_text_is_excluded(tmp_path):
    doc = scanned_doc()
    display = SourceDisplay(doc, 0)
    missing_text = 'This separately printed paragraph was omitted entirely by the page reading.'
    missing = words(missing_text)
    for w in missing:
        w['bbox'][1:4:2] = [170, 180]
    figure = words('A diagram label has several ordinary words beside its shapes')
    for w in figure:
        w['bbox'][1:4:2] = [310, 320]
    reading = answer(block('Opening paragraph.', bbox=(0, 50, 1000, 250)),
                     block('Genuine diagram', ident='figure', role='figure', bbox=(0, 700, 1000, 900)))
    recovered, identities = vision_pipeline.cover_missing_lines(reading, missing + figure, missing, display)
    assert len(identities) == 1
    added = next(b for b in recovered['blocks'] if b['id'] in identities)
    assert added['text'] == missing_text
    assert all('diagram label' not in b['text'] for b in recovered['blocks'])
    pkg = build_epub._Package(str(tmp_path / 'omission.epub'))
    try:
        published, report = vision_pipeline.compile_page(recovered, missing, display, 0, pkg, {}, missing + figure, identities)
        body = ''.join(published['body'])
        assert missing_text in ET.fromstring('<html xmlns:epub="http://www.idpf.org/2007/ops">' + body + '</html>').itertext()
        assert any(b['fallback'] and b['marked'] for b in report['blocks'] if b['id'] in identities)
        assert 'class="source-disclosure-ref"' in body
    finally:
        pkg.abandon()
        doc.close()


def test_independent_reader_ledgers_reload_shared_budget_before_second_dispatch(tmp_path, monkeypatch):
    """Stale Ledger objects must not each treat the same on-disk dollar as free."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, release = Event(), Event()
    calls = []
    path = tmp_path / 'paid.jsonl'
    readers = [vision_reading.Reader('inert-key', tmp_path / 'cache', Ledger(path, .009)) for _ in range(2)]

    def post(*a, **k):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return reply()

    monkeypatch.setattr(requests, 'post', post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(readers[0].read, jpeg())
        assert entered.wait(5)
        second = pool.submit(readers[1].read, jpeg('gray'))
        try:
            with pytest.raises(CapExceeded):
                second.result(timeout=3)
        finally:
            release.set()
        first.result(timeout=5)
    assert len(calls) == 1
    assert Ledger(path, .009).pending_usd() == 0


def test_invalid_geometry_rebinds_to_literal_optical_words_without_rewriting():
    from types import SimpleNamespace
    display = SimpleNamespace(rect=pymupdf.Rect(0, 0, 300, 400))
    text = 'The quiet river flows beside the old stone bridge.'
    optical = words(text)
    reading = answer(block(text, bbox=(40, 846, 448, 106)))
    repaired, ids = vision_pipeline.bind_geometry(reading, optical, display)
    assert ids == ['b1'] and repaired['blocks'][0]['text'] == text
    rect = vision_pipeline.block_rect(repaired['blocks'][0], display)
    assert all(rect[0] <= (w['bbox'][0] + w['bbox'][2]) / 2 <= rect[2]
               and rect[1] <= (w['bbox'][1] + w['bbox'][3]) / 2 <= rect[3] for w in optical)
    vision_reading.validate(repaired)
    assert reading['blocks'][0]['bbox'] == [40, 846, 448, 106], 'rebinding must not mutate cached source reading'


def test_invalid_geometry_without_literal_optical_support_rejects_publication():
    from types import SimpleNamespace
    display = SimpleNamespace(rect=pymupdf.Rect(0, 0, 300, 400))
    reading = answer(block('Imaginary dragons fly beyond an invented purple palace.', bbox=(40, 846, 448, 106)))
    with pytest.raises(ValueError, match='cannot be bound'):
        vision_pipeline.bind_geometry(reading, words('The ordinary bridge crosses a quiet river near the village.'), display)


@pytest.mark.parametrize('omits_paragraph', [True, False])
def test_optical_words_inside_claimed_body_recover_omission_despite_layer_agreement(tmp_path, omits_paragraph):
    """Rectangle coverage cannot conceal a whole optical paragraph absent from text."""
    doc = scanned_doc()
    display = SourceDisplay(doc, 0)
    first = 'The old road passes beside the quiet river near our village.'
    missing = 'A separate printed paragraph then describes the stone bridge after sunset.'
    layer = words(first)
    for w in layer:
        w['bbox'][1:4:2] = [60, 70]
    second = words(missing)
    for w in second:
        w['bbox'][1:4:2] = [150, 160]
    optical = layer + second
    model_text = first if omits_paragraph else first + ' ' + missing
    reading = answer(block(model_text, bbox=(0, 0, 1000, 1000)))
    # The first-stage region audit sees every optical word inside the rectangle.
    covered, recovered = vision_pipeline.cover_missing_lines(reading, optical, layer, display)
    assert recovered == set()
    pkg = build_epub._Package(str(tmp_path / 'inside-region.epub'))
    try:
        published, report = vision_pipeline.compile_page(covered, layer, display, 0, pkg, {}, optical)
        observed = report['blocks'][0]
        assert observed['published_tokens'] == (first + ' ' + missing).split()
        assert observed['fallback'] is omits_paragraph
        assert 'class="source-disclosure-ref"' in ''.join(published['body']), 'words missing from the original layer stay disclosed in either case'
        if omits_paragraph:
            assert any(g['kind'] == 'independent_pixel_line_fallback' for g in observed['guard'])
            assert observed['marked'], 'optical fallback must retain uncertainty disclosure'
        markup = ET.fromstring('<html xmlns:epub="http://www.idpf.org/2007/ops">' + ''.join(published['body']) + '</html>')
        assert missing in ''.join(markup.itertext())
        assert list(markup.iter('img')) == [], 'recovered prose must stay live text'
    finally:
        pkg.abandon()
        doc.close()


def test_disclosure_crop_tracks_actual_optical_ink_without_changing_layer_certainty():
    """A corrected word must link to its real pixels, not a damaged layer's blank box."""
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    text = 'The narrow path crosses a bridge.'
    page.insert_text((20, 80), text, fontsize=16)
    display = SourceDisplay(doc, 0)
    optical = [{'text': w[4], 'bbox': list(w[:4])} for w in page.get_text('words')]
    assert [w['text'] for w in optical] == text.split()
    damaged = words('The narrovv path crosses a bridge.')
    for w in damaged:
        w['bbox'][1:4:2] = [280, 290]

    class Images:
        def __init__(self):
            self.data = {}

        def image(self, name, data):
            self.data[name] = data

    legacy, current = Images(), Images()
    old_records, current_records = {}, {}
    try:
        _, before = vision_pipeline.compile_page(answer(block(text, bbox=(0, 0, 1000, 1000))),
                                                damaged, display, 0, legacy, old_records)
        _, after = vision_pipeline.compile_page(answer(block(text, bbox=(0, 0, 1000, 1000))),
                                               damaged, display, 0, current, current_records, optical)
        assert after['blocks'][0]['published_tokens'] == before['blocks'][0]['published_tokens'] == text.split()
        assert after['blocks'][0]['marked'] == before['blocks'][0]['marked'] == [1]
        assert after['blocks'][0]['fallback'] is before['blocks'][0]['fallback'] is False
        assert after['marks'] == before['marks'] == 1
        assert len(current_records) == 1
        name, evidence = next(iter(current_records.items()))
        crop = pymupdf.Rect(evidence['bbox'])
        original_word = pymupdf.Rect(optical[1]['bbox'])
        assert crop.contains(original_word), 'crop must contain the actual printed corrected word'
        assert crop.y1 < 100, 'garbled layer coordinates point to blank paper near the page bottom'
        assert Image.open(io.BytesIO(current.data[name])).convert('L').getextrema()[0] < 80
        old_name = next(iter(old_records))
        assert Image.open(io.BytesIO(legacy.data[old_name])).convert('L').getextrema()[0] > 240
    finally:
        doc.close()


def test_concurrent_independent_pixel_readings_recognize_once_and_share_valid_cache(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, BrokenBarrierError
    from types import SimpleNamespace
    from cps.services.reflow import vision_pixels
    calls = []
    barrier = Barrier(2)
    image = b'one-synthetic-display-image'
    pix = SimpleNamespace(width=600, height=800, tobytes=lambda encoding: image)
    display = SimpleNamespace(rect=pymupdf.Rect(0, 0, 300, 400), pixmap=lambda **k: pix)
    tsv = ('level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
           '5\t1\t1\t1\t1\t1\t100\t200\t60\t20\t95\tRiver\n')
    monkeypatch.setattr(vision_pixels.shutil, 'which', lambda name: '/synthetic/tesseract')

    def recognize(command, **kwargs):
        calls.append(True)
        try:
            barrier.wait(timeout=.25)
        except BrokenBarrierError:
            pass  # A correct per-image lock prevents the competing recognition.
        return SimpleNamespace(returncode=0, stdout=tsv.encode(), stderr=b'')

    monkeypatch.setattr(vision_pixels.subprocess, 'run', recognize)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(vision_pixels.words, display, tmp_path / 'optical') for _ in range(2)]
        results = []
        for future in futures:
            try:
                results.append(future.result(timeout=5))
            except Exception as exc:
                results.append(exc)
    assert len(calls) == 1, 'independent readers must not recognize/write the same image twice'
    assert results == [[{'text': 'River', 'bbox': [50, 100, 80, 110]}]] * 2
    assert vision_pixels.words(display, tmp_path / 'optical') == results[0]
    assert len(calls) == 1


def test_prepared_native_decorative_glyph_and_figure_package_once_with_source_metadata(tmp_path):
    """Native preparation may retain a source decorative glyph beside live prose."""
    from types import SimpleNamespace
    from cps.services.reflow import assemble
    from cps.services.reflow.native_text import descriptor
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    text = 'A native sentence retains its decorative mark.'
    page.insert_text((20, 50), text, fontsize=12)
    page.draw_polyline([(20, 100), (25, 95), (30, 100), (25, 105), (20, 100)], color=(0, 0, 0), width=1)
    page.draw_polyline([(80, 180), (160, 180), (120, 240), (80, 180)], color=(0, 0, 0), width=2)
    record = descriptor(0, (18, 93, 32, 107), 12)
    prose = assemble.Element('p', runs=[['t', text], ['glyph', '*', record]], pno=0, bbox=(20, 35, 285, 110))
    figure = assemble.Element('fig', pno=0, bbox=(78, 178, 162, 242))
    book = assemble.Book(elements=[prose, figure], pages={0: [prose, figure]},
                         figures=[{'pno': 0, 'bbox': (78, 178, 162, 242), 'found': 'native'}])
    reader = SyntheticReader()
    out = tmp_path / 'native-symbol.epub'
    try:
        assert vision_pipeline.clean_native(page) is True
        report = vision_pipeline.convert(doc, str(out), reader, native_prepared=SimpleNamespace(book=book))
        assert reader.calls == 0 and report['native_pages'] == [0]
        assert report['pages'][0]['source'] == 'native'
        assert {'glyph', 'native'} <= {image['kind'] for image in report['images'].values()}
        assert all(image['page'] == 0 and len(image['bbox']) == 4 for image in report['images'].values())
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            images = [name for name in names if name.startswith('OEBPS/images/')]
            assert len(images) == len(set(images))
            assert sum('/glyph_' in name for name in images) == 1
            assert sum('/fig_' in name for name in images) == 1
            assert all('OEBPS/' + src in names for src in report['images'])
        assert build_epub.validate(str(out)) == [], 'native evidence links and assets must remain usable'
        docs = documents(out)
        assert_links_resolve(docs)
        references = {src for src, image in report['images'].items() if image['kind'] == 'native_source_reference'}
        for name, root in docs.items():
            if name.startswith('OEBPS/ch'):
                assert not any(image.get('src') in references for image in root.findall('.//x:img', NS)), 'full original evidence belongs outside body flow'
        assert text in ' '.join(paragraphs(docs))
    finally:
        doc.close()


@pytest.mark.parametrize('flags,recovers', [([0, 1, 2, 3, 4], True), ([4], False)])
def test_saved_confidence_endpoint_recovery_preserves_all_real_flags_without_rebill(tmp_path, monkeypatch, flags, recovers):
    text = 'A short clear sentence.'
    reading = answer(block(text, low=flags))
    calls = []
    path = tmp_path / 'paid.jsonl'

    def post(*args, **kwargs):
        calls.append(True)
        return reply(reading)

    monkeypatch.setattr(requests, 'post', post)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', Ledger(path, .05))
    with pytest.raises(ValueError):
        reader.read(jpeg())
    assert Ledger(path, .05).spent() == pytest.approx(.0004)
    restarted = vision_reading.Reader('inert-key', tmp_path / 'cache', Ledger(path, .05))
    if recovers:
        recovered = restarted.read(jpeg())
        assert recovered['cached'] is True and recovered['new_cost_usd'] == 0
        assert recovered['answer']['blocks'][0]['text'] == text
        assert recovered['answer']['blocks'][0]['low_confidence'] == [0, 1, 2, 3]
        assert recovered['confidence_recovery'] == [{'block': 'b1', 'discarded_nonexistent_endpoint': 4}]
    else:
        with pytest.raises(vision_reading.model.UncertainBilling):
            restarted.read(jpeg())
    assert len(calls) == 1
    assert Ledger(path, .05).spent() == pytest.approx(.0004)


def test_cross_column_geometry_cannot_annex_unrelated_printed_paragraphs(tmp_path):
    """High lexical support alone cannot authorize a rectangle across both leaves."""
    doc = scanned_doc()
    display = SourceDisplay(doc, 0)
    left = 'Amber boats carry quiet travelers past the northern village after sunset'
    right = 'Silver birds follow winding rivers across the southern meadow before sunrise'
    unrelated = 'Several unrelated printed sentences discuss ordinary tools gardens roads and buildings'
    optical = []
    for text, x0, y0 in [(unrelated, 15, 60), (left, 15, 330),
                         (right, 165, 60), (unrelated, 165, 330)]:
        for i, token in enumerate(text.split()):
            # Six words per line; separate leaf regions each have a visible gap.
            x = x0 + (i % 6) * 18
            y = y0 + (i // 6) * 12
            optical.append({'text': token, 'bbox': [x, y, x + 16, y + 8]})
    text = left + ' ' + right
    reading = answer(block(text, bbox=(40, 846, 949, 302)))
    pkg = build_epub._Package(str(tmp_path / 'cross-column.epub'))
    try:
        try:
            repaired, identities = vision_pipeline.bind_geometry(reading, optical, display)
        except ValueError:
            # An ambiguous layout may fail closed rather than invent ownership.
            return
        published, report = vision_pipeline.compile_page(repaired, optical, display, 0, pkg, {}, optical)
        body_tokens = [t for b in report['blocks'] if b['role'] == 'body' for t in b['published_tokens']]
        assert body_tokens == text.split(), 'geometry repair must not annex other printed paragraphs'
        assert all(not b['fallback'] for b in report['blocks'] if b['role'] == 'body')
        assert 'unrelated printed sentences' not in ''.join(published['body'])
    finally:
        pkg.abandon()
        doc.close()


def _cross_column_optical_fixture(contaminated=False):
    left = 'Amber boats carry quiet travelers past the northern village after sunset tonight'
    right = 'Silver birds follow winding rivers across the southern meadow before sunrise'
    outside = 'Several unrelated printed sentences discuss ordinary tools gardens roads and buildings'
    optical = []
    for text, x0, y0 in [(outside, 15, 60), (left, 15, 330),
                         (right, 165, 60), (outside, 165, 330)]:
        segment = []
        for i, token in enumerate(text.split()):
            x, y = x0 + (i % 6) * 23, y0 + (i // 6) * 12
            segment.append({'text': token, 'bbox': [x, y, x + 16, y + 8]})
        if contaminated and text == left:
            intruders = [{'text': 'intruder' + str(i), 'bbox': [x0 + i * 10, 335, x0 + i * 10 + 8, 340]}
                         for i in range(10)]
            segment[6:6] = intruders
        optical.extend(segment)
    return left + ' ' + right, outside, optical


def test_disjoint_pixel_regions_publish_cross_column_paragraph_once_and_recover_outside_lines(tmp_path):
    doc = scanned_doc()
    display = SourceDisplay(doc, 0)
    text, outside, optical = _cross_column_optical_fixture()
    reading = answer(block(text, bbox=(40, 846, 949, 302), low=[13]))
    regions = {}
    repaired, ids = vision_pipeline.bind_geometry(reading, optical, display, regions)
    assert ids == ['b1'] and len(regions['b1']) == 2
    assert repaired['blocks'][0]['text'] == text and repaired['blocks'][0]['low_confidence'] == [13]
    assert reading['blocks'][0]['bbox'] == [40, 846, 949, 302]
    recovered, missing = vision_pipeline.cover_missing_lines(repaired, optical, optical, display, regions)
    assert len(missing) == 2, 'the bounding union cannot conceal unrepresented outside paragraphs'
    pkg = build_epub._Package(str(tmp_path / 'disjoint-regions.epub'))
    try:
        published, report = vision_pipeline.compile_page(recovered, optical, display, 0, pkg, {}, optical, missing, regions)
        original = next(b for b in report['blocks'] if b['id'] == 'b1')
        assert original['published_tokens'] == text.split() and original['fallback'] is False
        assert original['marked'] == [], 'literal source agreement retains the established certainty behavior'
        assert [b['published_tokens'] for b in report['blocks'] if b['id'] in missing] == [outside.split()] * 2
        markup = ET.fromstring('<html xmlns:epub="http://www.idpf.org/2007/ops">' + ''.join(published['body']) + '</html>')
        ps = markup.findall('p')
        assert len(ps) == 3, 'cross-column source regions must remain one published paragraph'
        assert len([p for p in ps if 'Amber boats' in ''.join(p.itertext())]) == 1
        assert all(not p.findall('.//img') for p in ps)
    finally:
        pkg.abandon()
        doc.close()


def test_disjoint_geometry_rejects_line_sized_unrelated_ink_inside_anchor_region():
    from types import SimpleNamespace
    display = SimpleNamespace(rect=pymupdf.Rect(0, 0, 300, 400))
    text, outside, optical = _cross_column_optical_fixture(contaminated=True)
    with pytest.raises(ValueError, match='Ambiguous|cannot be bound'):
        vision_pipeline.bind_geometry(answer(block(text, bbox=(40, 846, 949, 302))), optical, display, {})


def test_landscape_optical_tiles_keep_right_leaf_coordinates_and_avoid_old_cache(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import hashlib
    from cps.services.reflow import vision_pixels
    encoded = io.BytesIO()
    Image.new('RGB', (800, 400), 'white').save(encoded, format='PNG')
    image = encoded.getvalue()
    pix = SimpleNamespace(width=800, height=400, tobytes=lambda encoding: image)
    display = SimpleNamespace(rect=pymupdf.Rect(0, 0, 400, 200), pixmap=lambda **k: pix)
    old_cache = tmp_path / (hashlib.sha256(image).hexdigest() + '.json')
    old_cache.write_text(json.dumps([{'text': 'old-incomplete-leaf', 'bbox': [1, 1, 2, 2]}]))
    calls = []
    monkeypatch.setattr(vision_pixels.shutil, 'which', lambda name: '/synthetic/tesseract')
    def run(command, **kwargs):
        tile = Image.open(io.BytesIO(kwargs['input']))
        calls.append(tile.size)
        token = 'Left' if len(calls) == 1 else 'Right'
        tsv = ('level\tleft\ttop\twidth\theight\ttext\n'
               f'5\t40\t60\t20\t10\t{token}\n')
        return SimpleNamespace(returncode=0, stdout=tsv.encode(), stderr=b'')
    monkeypatch.setattr(vision_pixels.subprocess, 'run', run)
    optical = vision_pixels.words(display, tmp_path)
    assert calls == [(400, 400), (400, 400)]
    assert optical == [{'text': 'Left', 'bbox': [20, 30, 30, 35]},
                       {'text': 'Right', 'bbox': [220, 30, 230, 35]}]
    assert vision_pixels.words(display, tmp_path) == optical and len(calls) == 2
    assert json.loads(old_cache.read_text())[0]['text'] == 'old-incomplete-leaf'


@real_ocr
@pytest.mark.parametrize('role', ['figure', 'table', 'heading', 'caption'])
@pytest.mark.parametrize('at_left_end', [True, False])
def test_page_turn_continuation_flags_cannot_move_intervening_source_structure(tmp_path, role, at_left_end):
    left_text, right_text = 'The path reaches', 'Northern Bridge before sunset.'
    structure = block('Separate source structure', ident='structure', role=role, bbox=(600, 650, 850, 850))
    left = [block(left_text, next=True)]
    right = [block(right_text, previous=True)]
    if at_left_end:
        left.append(structure)
    else:
        right.insert(0, structure)
    doc = scanned_doc(2)
    out = tmp_path / ('flow-' + role + '.epub')
    try:
        result = vision_pipeline.convert(doc, str(out), SyntheticReader(answer(*left), answer(*right)))
    finally:
        doc.close()
    assert result['page_joins'] == 0, 'continuation flags cannot jump over actual figure, heading, table or caption'
    assert build_epub.validate(str(out)) == []
    docs = documents(out)
    ps = paragraphs(docs)
    assert left_text in ps and right_text in ps
    assert left_text + ' ' + right_text not in ps
    observed_roles = [b['role'] for b in result['pages'][0 if at_left_end else 1]['blocks']]
    assert role in observed_roles, 'published source structure must remain represented in alignment metadata'


def test_converter_passes_disjoint_source_regions_through_final_epub(tmp_path, monkeypatch):
    from cps.services.reflow import vision_pixels
    text, outside, optical = _cross_column_optical_fixture()
    monkeypatch.setattr(vision_pixels, 'words', lambda display, cache: optical)
    doc = scanned_doc()
    reader = SyntheticReader(answer(block(text, bbox=(40, 846, 949, 302))))
    out = tmp_path / 'region-routing.epub'
    try:
        result = vision_pipeline.convert(doc, str(out), reader)
    finally:
        doc.close()
    assert reader.calls == 1 and result['new_cost_usd'] == 0
    original = next(b for b in result['pages'][0]['blocks'] if b['id'] == 'b1')
    assert original['published_tokens'] == text.split() and original['fallback'] is False
    assert len(original['source_regions']) == 2
    assert len(result['pages'][0]['recovered_omitted_blocks']) == 2
    assert build_epub.validate(str(out)) == []
    docs = documents(out)
    assert_links_resolve(docs)
    chapter_roots = [root for name, root in docs.items() if name.endswith(result['page_homes'][0])]
    # Notes may contain the reading in evidence, so count actual chapter paragraphs.
    body = [p for root in chapter_roots for p in root.findall('.//x:p', NS)
            if not any(p is descendant for aside in root.findall('.//x:aside', NS) for descendant in aside.iter())]
    assert len(body) == 3
    assert sum('Amber boats' in ''.join(p.itertext()) for p in body) == 1
    assert sum('Several unrelated' in ''.join(p.itertext()) for p in body) == 2
    assert all(p.findall('.//x:img', NS) == [] for p in body)


@pytest.mark.parametrize('flags,recovers', [
    ([1, 2, 4, 5, 6], True),
    ([6], False),
    ([1, 2, 4, 6], False),
    ([5, 6, 6], False),
    ([5, 7], False),
    ([-1, 5, 6], False),
    ([True, 5, 6], False),
])
def test_saved_partial_endpoint_metadata_recovery_retains_real_uncertainty(tmp_path, monkeypatch, flags, recovers):
    text = 'Several quiet boats cross the river.'
    reading = answer(block(text, low=flags))
    calls = []
    path = tmp_path / 'paid.jsonl'
    def post(*args, **kwargs):
        calls.append(True)
        return reply(reading)
    monkeypatch.setattr(requests, 'post', post)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', Ledger(path, .05))
    with pytest.raises(ValueError):
        reader.read(jpeg())
    restarted = vision_reading.Reader('inert-key', tmp_path / 'cache', Ledger(path, .05))
    if recovers:
        recovered = restarted.read(jpeg())
        b = recovered['answer']['blocks'][0]
        assert b['text'] == text and b['low_confidence'] == [1, 2, 4, 5]
        assert b['bbox'] == reading['blocks'][0]['bbox']
        assert recovered['confidence_recovery'] == [{'block': 'b1', 'discarded_nonexistent_endpoint': 6}]
        assert recovered['cached'] is True and recovered['new_cost_usd'] == 0
    else:
        with pytest.raises(vision_reading.model.UncertainBilling):
            restarted.read(jpeg())
    assert len(calls) == 1 and Ledger(path, .05).spent() == pytest.approx(.0004)


@pytest.mark.parametrize('text,flags,expected', [
    ('Boat boat Boat. boat', ['boat'], [1, 3]),
    ('Boat boat Boat. boat', ['boat', 'boat'], [1, 3]),
    ('14 014 14. 14', ['14', '14.'], [0, 2, 3]),
    ('“river” river, river', ['river,'], [1]),
    ('road<n:n1> road<n:n1>', ['road<n:n1>'], [0, 1]),
    ('A clear sentence.', [], []),
])
def test_literal_confidence_decoder_matches_exact_whitespace_tokens_without_mutating_raw(tmp_path, text, flags, expected):
    raw = answer(block(text, low=flags))
    if '<n:n1>' in text:
        raw['blocks'].append(block('Printed note.', ident='n1', role='footnote', marker='1'))
    snapshot = json.loads(json.dumps(raw))
    reader = vision_reading.Reader(None, tmp_path / 'cache', Ledger(tmp_path / 'paid.jsonl', .05), token_confidence=True)
    normalized = reader._answer(raw)
    assert normalized['blocks'][0]['low_confidence'] == expected
    assert normalized['blocks'][0]['text'] == text
    assert raw == snapshot, 'raw literal annotations must remain available as evidence'
    vision_reading.validate(normalized)


@pytest.mark.parametrize('flags', [['ghost'], ['boat.'], ['BOAT'], [0], [True], ['boat river'], [''], None, 'boat'])
def test_literal_confidence_decoder_rejects_absent_changed_and_nonstring_annotations(tmp_path, flags):
    reader = vision_reading.Reader(None, tmp_path / 'cache', Ledger(tmp_path / 'paid.jsonl', .05), token_confidence=True)
    with pytest.raises(ValueError, match='confidence word'):
        reader._answer(answer(dict(block('Boat boat river'), low_confidence=flags)))


def test_literal_confidence_reader_cache_is_separate_from_default_and_rebuilds_for_zero(tmp_path, monkeypatch):
    import hashlib
    path = tmp_path / 'paid.jsonl'
    cache = tmp_path / 'cache'
    calls = []
    def post(*args, **kwargs):
        payload = kwargs['json']
        calls.append(payload)
        kind = payload['response_format']['json_schema']['schema']['properties']['blocks']['items']['properties']['low_confidence']['items']['type']
        return reply(answer(block('Boat boat boat.', low=['boat'] if kind == 'string' else [0])))
    monkeypatch.setattr(requests, 'post', post)
    default = vision_reading.Reader('inert-key', cache, Ledger(path, .05))
    first = default.read(jpeg())
    word = vision_reading.Reader('inert-key', cache, Ledger(path, .05), token_confidence=True)
    second = word.read(jpeg())
    assert first['config_sha256'] == hashlib.sha256((vision_reading.VERSION + vision_reading.MODEL + vision_reading.PROMPT + json.dumps(vision_reading.schema(), sort_keys=True)).encode()).hexdigest()
    assert default.api_schema['properties']['blocks']['items']['properties']['low_confidence']['items']['type'] == 'integer'
    assert first['image_sha256'] == second['image_sha256'] and first['config_sha256'] != second['config_sha256']
    assert first['answer']['blocks'][0]['low_confidence'] == [0]
    assert second['answer']['blocks'][0]['low_confidence'] == [1]
    assert second['confidence_words'] == {'b1': ['boat']}
    assert len(list(cache.glob('*.json'))) == 2
    for mode, expected in [(False, [0]), (True, [1])]:
        rebuilt = vision_reading.Reader(None, cache, Ledger(path, .05), token_confidence=mode).read(jpeg())
        assert rebuilt['cached'] is True and rebuilt['new_cost_usd'] == 0
        assert rebuilt['answer']['blocks'][0]['low_confidence'] == expected
        if mode:
            assert rebuilt['confidence_words'] == {'b1': ['boat']}
    assert len(calls) == 2 and Ledger(path, .05).spent() == pytest.approx(.0008)
    assert calls[0]['messages'][0]['content'][0]['text'] == vision_reading.PROMPT
    assert 'exact whitespace token STRING' in calls[1]['messages'][0]['content'][0]['text']


@pytest.mark.parametrize('flags', [['ghost'], ['boat.'], [0], None])
def test_charged_unusable_literal_confidence_remains_saved_and_cannot_retry(tmp_path, monkeypatch, flags):
    path = tmp_path / 'paid.jsonl'
    calls = []
    def post(*args, **kwargs):
        calls.append(True)
        return reply(answer(dict(block('Boat boat river'), low_confidence=flags)))
    monkeypatch.setattr(requests, 'post', post)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', Ledger(path, .05), token_confidence=True)
    with pytest.raises(ValueError, match='confidence word'):
        reader.read(jpeg())
    assert len(list((tmp_path / 'cache').glob('*.pending'))) == 1
    restarted = vision_reading.Reader('inert-key', tmp_path / 'cache', Ledger(path, .05), token_confidence=True)
    with pytest.raises(vision_reading.model.UncertainBilling):
        restarted.read(jpeg())
    assert len(calls) == 1
    assert Ledger(path, .05).spent() == pytest.approx(.0004) and Ledger(path, .05).pending_usd() == 0
    assert not list((tmp_path / 'cache').glob('*.json'))


def test_saved_complete_literal_confidence_decodes_without_rebill_after_interruption(tmp_path, monkeypatch):
    path = tmp_path / 'paid.jsonl'
    calls = []
    raw = answer(block('Boat boat river boat', low=['boat', 'river']))
    def post(*args, **kwargs):
        calls.append(True)
        return reply(raw)
    monkeypatch.setattr(requests, 'post', post)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', Ledger(path, .05), token_confidence=True)
    def interrupted_decode(value):
        raise RuntimeError('synthetic interpreter interruption after charged response was saved')
    monkeypatch.setattr(reader, '_answer', interrupted_decode)
    with pytest.raises(RuntimeError, match='interruption'):
        reader.read(jpeg())
    restarted = vision_reading.Reader(None, tmp_path / 'cache', Ledger(path, .05), token_confidence=True)
    recovered = restarted.read(jpeg())
    assert recovered['answer']['blocks'][0]['low_confidence'] == [1, 2, 3]
    assert recovered['answer']['blocks'][0]['text'] == raw['blocks'][0]['text']
    assert recovered['confidence_words'] == {'b1': ['boat', 'river']}
    assert recovered['cached'] is True and recovered['new_cost_usd'] == 0
    assert not list((tmp_path / 'cache').glob('*.pending'))
    assert len(calls) == 1 and Ledger(path, .05).spent() == pytest.approx(.0004)


@real_ocr
@pytest.mark.parametrize('disclosure_affix', [False, True])
def test_model_continued_hyphen_keeps_literal_hyphen_without_space_or_broken_disclosure(tmp_path, disclosure_affix):
    from cps.services.reflow import reading_flow
    left, right = 'The quiet bridge-', 'side leads into the meadow.'
    doc = scanned_doc(2)
    out = tmp_path / 'literal-hyphen.epub'
    reader = SyntheticReader(answer(block(left, next=True, low=[2] if disclosure_affix else [])),
                             answer(block(right, previous=True)))
    try:
        result = vision_pipeline.convert(doc, str(out), reader)
    finally:
        doc.close()
    assert result['page_joins'] == 1
    assert build_epub.validate(str(out)) == []
    docs = documents(out)
    assert_links_resolve(docs)
    chapter = next(root for name, root in docs.items() if name.endswith(result['page_homes'][0]))
    body = [p for p in chapter.findall('.//x:p', NS)
            if not any(p is descendant for aside in chapter.findall('.//x:aside', NS) for descendant in aside.iter())]
    assert len(body) == 1
    assert reading_flow.projection(body[0], {}).strip() == 'The quiet bridge-side leads into the meadow.', 'literal hyphen must survive without introducing a word-seam space'
    assert all('source-reading-annotation' not in ET.tostring(root, encoding='unicode') and 'reading-return-reading-' not in ET.tostring(root, encoding='unicode') for root in docs.values()), 'temporary source-control carriers must not leak into publication'
    refs = body[0].findall('.//x:a[@class="source-disclosure-ref"]', NS)
    assert len(refs) == int(disclosure_affix)
    assert body[0].findall('.//x:img', NS) == []
    if disclosure_affix:
        assert refs[0].get('href') and any('Return' == ''.join(a.itertext()) for root in docs.values() for a in root.findall('.//x:a', NS))


@pytest.mark.parametrize('role', ['figure', 'table'])
def test_descriptive_image_flags_remain_raw_receipt_without_assigning_body_indices(tmp_path, monkeypatch, role):
    path = tmp_path / 'paid.jsonl'
    raw = answer(block('An original diagram shows marked positions.', role=role, low=['"symbol"', 'unknown,']))
    calls = []
    def post(*args, **kwargs):
        calls.append(True)
        return reply(raw)
    monkeypatch.setattr(requests, 'post', post)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', Ledger(path, .05), token_confidence=True)
    result = reader.read(jpeg())
    assert result['answer']['blocks'][0]['role'] == role
    assert result['answer']['blocks'][0]['text'] == raw['blocks'][0]['text']
    assert result['answer']['blocks'][0]['low_confidence'] == []
    assert result['confidence_words'] == {'b1': ['"symbol"', 'unknown,']}
    rebuilt = vision_reading.Reader(None, tmp_path / 'cache', Ledger(path, .05), token_confidence=True).read(jpeg())
    assert rebuilt['confidence_words'] == result['confidence_words'] and rebuilt['new_cost_usd'] == 0
    assert len(calls) == 1 and Ledger(path, .05).spent() == pytest.approx(.0004)


@pytest.mark.parametrize('role', ['body', 'heading', 'caption', 'footnote', 'other', 'running_head', 'folio'])
def test_descriptive_flag_exception_never_relaxes_live_or_furniture_text(tmp_path, role):
    reader = vision_reading.Reader(None, tmp_path / 'cache', Ledger(tmp_path / 'paid.jsonl', .05), token_confidence=True)
    raw = answer(block('An exact source sentence.', role=role, marker='1' if role == 'footnote' else '', low=['"source"']))
    with pytest.raises(ValueError, match='confidence word'):
        reader._answer(raw)


@pytest.mark.parametrize('role', ['figure', 'table'])
@pytest.mark.parametrize('flags', [[0], [True], None, 'symbol'])
def test_descriptive_image_confidence_still_requires_a_string_array(tmp_path, role, flags):
    reader = vision_reading.Reader(None, tmp_path / 'cache', Ledger(tmp_path / 'paid.jsonl', .05), token_confidence=True)
    raw = answer(dict(block('An original diagram.', role=role), low_confidence=flags))
    with pytest.raises(ValueError, match='confidence word'):
        reader._answer(raw)


@real_ocr
def test_printed_note_reference_is_transparent_to_literal_hyphen_continuation(tmp_path):
    from cps.services.reflow import reading_flow
    doc = scanned_doc(2)
    reader = SyntheticReader(answer(
        block('The quiet bridge-<n:n1>', next=True),
        block('A short original note.', ident='n1', role='footnote', marker='1', bbox=(50, 700, 950, 850))),
        answer(block('side leads into the meadow.', previous=True)))
    out = tmp_path / 'printed-note-hyphen.epub'
    try:
        result = vision_pipeline.convert(doc, str(out), reader)
    finally:
        doc.close()
    assert result['page_joins'] == 1 and build_epub.validate(str(out)) == []
    docs = documents(out)
    assert_links_resolve(docs)
    chapter = next(root for name, root in docs.items() if name.endswith(result['page_homes'][0]))
    body = [p for p in chapter.findall('.//x:p', NS)
            if not any(p is descendant for aside in chapter.findall('.//x:aside', NS) for descendant in aside.iter())]
    assert len(body) == 1
    assert reading_flow.projection(body[0], {}).strip() == 'The quiet bridge-side leads into the meadow.'
    refs = body[0].findall('.//x:a[@class="noteref"]', NS)
    assert len(refs) == 1 and ''.join(refs[0].itertext()) == '1'
    assert any(a.get('href', '').endswith('#' + refs[0].get('id')) for root in docs.values() for a in root.findall('.//x:a', NS))
    assert all('source-reading-annotation' not in ET.tostring(root, encoding='unicode') and 'reading-return-reading-' not in ET.tostring(root, encoding='unicode') for root in docs.values())
    assert body[0].findall('.//x:img', NS) == []
