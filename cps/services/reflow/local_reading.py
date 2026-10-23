"""Bounded, explicitly unverified OCR readings tied to original source pixels.

Engine scores are not probabilities. Every locally transcribed word remains
uncertain, including words that agree with an existing OCR text layer. Paragraph
identities and word rectangles allow independent source disclosure on rejection.
"""
import copy
import csv
import hashlib
import io
import json
import math
import os
import re
from pathlib import Path
import tempfile

from . import ocr, vision_reading

VERSION = 'local-reading-4'
MAX_PIXELS = 8_000_000
TIMEOUT = 120


def partition_units(units, repairs):
    """Carry source occurrences through explicitly proved paragraph cuts.

    The paragraph splitter records whitespace-token offsets. They identify the
    original occurrences even when several words have identical spellings; a
    text search cannot establish that ownership. Raw OCR receipts stay intact.
    """
    result = copy.deepcopy(units)
    identities = [unit['id'] for unit in result]
    if (any(not isinstance(ident, str) or not ident for ident in identities)
            or len(set(identities)) != len(identities)):
        raise ValueError('Local OCR source owners must be unique.')
    methods = {'original_indented_paragraph_partition',
               'original_delimited_hanging_paragraph_partition'}
    for repair in repairs:
        original = repair.get('block')
        owners = {unit['id']: index for index, unit in enumerate(result)}
        if original not in owners:
            raise ValueError('Local OCR paragraph partition has no source owner.')
        at = owners[original]
        unit = result[at]
        words = unit['words']
        derived = repair.get('derived_blocks')
        cuts = repair.get('whitespace_token_cuts')
        if (repair.get('method') not in methods
                or not isinstance(derived, (list, tuple)) or len(derived) < 2
                or any(not isinstance(ident, str) or not ident for ident in derived)
                or derived[0] != original or len(set(derived)) != len(derived)
                or any(ident in owners for ident in derived[1:])
                or not isinstance(cuts, (list, tuple))
                or len(cuts) != len(derived) - 1
                or any(type(cut) is not int or not 0 < cut < len(words) for cut in cuts)
                or list(cuts) != sorted(set(cuts))
                or any(not isinstance(word.get('text'), str)
                       or word['text'].split() != [word['text']] for word in words)):
            raise ValueError('Local OCR paragraph cuts have no unique source ownership.')
        origin = unit.get('source_unit', original)
        span = unit.get('source_word_range', [0, len(words)])
        if (not isinstance(origin, str) or not origin
                or not isinstance(span, (list, tuple)) or len(span) != 2
                or any(type(value) is not int for value in span)
                or span[0] < 0 or span[1] - span[0] != len(words)):
            raise ValueError('Local OCR source occurrence range is invalid.')
        boundaries = [0, *cuts, len(words)]
        parts = []
        for ident, begin, end in zip(derived, boundaries, boundaries[1:]):
            owned = words[begin:end]
            confidence = [word['confidence'] for word in owned]
            if any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) or not 0 <= value <= 100
                   for value in confidence):
                raise ValueError('Local OCR source confidence is invalid.')
            parts.append(dict(unit, id=ident, words=owned, source_unit=origin,
                source_word_range=[span[0] + begin, span[0] + end],
                mean_confidence=sum(confidence) / len(confidence),
                low_confidence_tokens=[index for index, value in enumerate(confidence)
                                       if value < 85]))
        result[at:at + 1] = parts
    return result


def _reading_scale(display):
    """Avoid enlarging a low-resolution page image beyond useful sampling.

    A full-page raster below 108 dpi has no extra glyph detail at 216 dpi;
    enlarging its existing stair steps can create false recognition strokes.
    Keep the established 144-dpi sampling there. Higher-resolution scans and
    vector pages retain the denser sampling needed for small printed words.
    """
    page = getattr(display, 'page', None)
    images = getattr(page, 'get_image_info', None)
    if images is not None:
        page_area = display.rect.width * display.rect.height
        for image in images():
            x0, y0, x1, y1 = image['bbox']
            area = max(0, x1-x0) * max(0, y1-y0)
            if (area >= page_area*.9 and image['width'] > 0 and image['height'] > 0
                    and image['width'] * image['height'] / area < 1.5**2):
                return 2
    return 3


def recognize(display, cache):
    """Recognize at bounded source sampling, at most 8M pixels and 120s/tile."""
    executable, version, data = ocr._engine('eng')
    pix = display.pixmap(scale=_reading_scale(display), max_pixels=MAX_PIXELS)
    image = pix.tobytes('png')
    identity = dict(adapter=VERSION, image=hashlib.sha256(image).hexdigest(),
                    engine=version, data=data, rect=list(display.rect))
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    root = Path(cache)
    root.mkdir(parents=True, exist_ok=True)
    target = root / (key + '.json')
    if target.exists() and target.stat().st_size <= ocr.MAX_OUTPUT_BYTES:
        try:
            saved = json.loads(target.read_text())
            if saved['identity'] == identity and saved['sha256'] == hashlib.sha256(
                    json.dumps(saved['words'], sort_keys=True).encode()).hexdigest():
                _validate_words(saved['words'], display.rect)
                return saved['words'], dict(identity, cached=True)
        except (ValueError, KeyError, TypeError):
            pass
    tiles = [(image, 0, pix.width)]
    if pix.width >= pix.height * 1.2:
        from PIL import Image
        picture = Image.open(io.BytesIO(image))
        middle = pix.width // 2
        # Aspect ratio does not prove two independent leaves. A midpoint cut
        # through ink can split a word into separate paragraph owners and lose
        # its middle letters. Tile only across a positively blank source gutter;
        # the complete raster is already bounded by MAX_PIXELS otherwise.
        gutter=picture.convert('L').crop((max(0,middle-2),0,min(pix.width,middle+2),pix.height))
        if gutter.getextrema()[0]>=235:
            tiles = []
            for left, right in ((0, middle), (middle, pix.width)):
                buffer = io.BytesIO()
                picture.crop((left, 0, right, pix.height)).save(buffer, format='PNG')
                tiles.append((buffer.getvalue(), left, right-left))
    output = []
    sx, sy = display.rect.width / pix.width, display.rect.height / pix.height
    for tile_index, (encoded, offset, width) in enumerate(tiles):
        with tempfile.TemporaryDirectory(prefix='local-ocr-', dir=root) as temporary:
            directory = Path(temporary)
            source, table = directory / 'page.png', directory / 'result.tsv'
            source.write_bytes(encoded)
            status, _, _ = ocr._invoke([executable, str(source), str(directory / 'result'),
                '-l', 'eng', '--psm', '3', 'tsv'], directory, timeout=TIMEOUT,
                should_stop=None, watched=(table,))
            if status or not table.exists():
                raise ocr.OCRFailed('Local page recognition failed.')
            for row in csv.DictReader(io.StringIO(table.read_text()), delimiter='\t', quoting=csv.QUOTE_NONE):
                if row['level'] != '5' or not row['text'].strip():
                    continue
                x, y, w, h = [int(row[name]) for name in ('left', 'top', 'width', 'height')]
                if not (0 <= x < x+w <= width and 0 <= y < y+h <= pix.height):
                    raise ocr.OCRFailed('Local recognition returned invalid source geometry.')
                output.append(dict(text=row['text'], bbox=[(x+offset)*sx, y*sy,
                    (x+offset+w)*sx, (y+h)*sy], confidence=float(row['conf']),
                    paragraph=[tile_index, int(row['block_num']), int(row['par_num'])],
                    line=int(row['line_num'])))
                if len(output) > ocr.MAX_WORDS:
                    raise ocr.OCRFailed('Local recognition exceeded its word limit.')
    _validate_words(output, display.rect)
    saved = dict(identity=identity, words=output,
        sha256=hashlib.sha256(json.dumps(output, sort_keys=True).encode()).hexdigest())
    encoded = json.dumps(saved, ensure_ascii=False)
    if len(encoded.encode()) > ocr.MAX_OUTPUT_BYTES:
        raise ocr.OCRFailed('Local recognition exceeded its receipt limit.')
    with tempfile.NamedTemporaryFile(mode='w', dir=root, prefix='.receipt-', delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return output, dict(identity, cached=False)


def _validate_words(words, rect):
    if not isinstance(words, list) or len(words) > ocr.MAX_WORDS:
        raise ValueError('Invalid local recognition word inventory')
    for word in words:
        if not isinstance(word['text'], str) or not word['text'].strip():
            raise ValueError('Invalid local recognition text')
        box = word['bbox']
        if (len(box) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box)
                or not 0 <= box[0] < box[2] <= rect.width
                or not 0 <= box[1] < box[3] <= rect.height
                or not math.isfinite(word['confidence']) or not 0 <= word['confidence'] <= 100
                or len(word['paragraph']) != 3
                or any(type(v) is not int for v in word['paragraph']) or type(word['line']) is not int):
            raise ValueError('Invalid local recognition evidence')


ORIENTATION_VERSION = 'local-orientation-2'
ORIENTATION_MAX_PIXELS = 8_000_000
ORIENTATION_TIMEOUT = 30
ORIENTATION_MIN_CONFIDENCE = 10


def orientation(display, cache):
    """Bounded OSD evidence, used only after a primary reading is unavailable."""
    executable, version, data = ocr._engine('eng')
    pix = display.pixmap(scale=4, max_pixels=ORIENTATION_MAX_PIXELS)
    image = pix.tobytes('png')
    from .unit_recovery import page_fingerprint
    from .extract import pymupdf
    identity = dict(adapter=ORIENTATION_VERSION, renderer=pymupdf.VersionBind,
        image=hashlib.sha256(image).hexdigest(), engine=version, data=data,
        source_page=page_fingerprint(display.doc, display.pno),
        max_pixels=ORIENTATION_MAX_PIXELS, scale=4, timeout=ORIENTATION_TIMEOUT,
        min_confidence=ORIENTATION_MIN_CONFIDENCE, recognition_adapter=VERSION,
        rect=list(display.rect), source_orientation=display.angle)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    root = Path(cache)
    root.mkdir(parents=True, exist_ok=True)
    target = root / ('orientation-' + key + '.json')
    if target.exists() and target.stat().st_size <= 16_384:
        try:
            saved = json.loads(target.read_text())
            if (saved['identity'] == identity and type(saved['clockwise']) is int
                    and saved['clockwise'] in (0, 90, 180, 270)
                    and type(saved['confidence']) in (int, float)
                    and math.isfinite(saved['confidence']) and saved['confidence'] >= 0
                    and (saved['clockwise'] == 0 or saved['confidence'] >= ORIENTATION_MIN_CONFIDENCE)):
                return dict(saved, cached=True)
        except (ValueError, KeyError, TypeError):
            pass
    with tempfile.TemporaryDirectory(prefix='local-osd-', dir=root) as temporary:
        directory = Path(temporary)
        source = directory / 'page.png'
        source.write_bytes(image)
        status, output, error = ocr._invoke(
            [executable, str(source), 'stdout', '-l', 'osd', '--psm', '0'],
            directory, timeout=ORIENTATION_TIMEOUT, should_stop=None)
    angle = re.search(r'^Rotate:\s*(0|90|180|270)\s*$', output, re.M)
    score = re.search(r'^Orientation confidence:\s*([\d.]+)', output, re.M)
    confidence = float(score.group(1)) if score else 0.
    if not math.isfinite(confidence):
        raise ocr.OCRFailed('Local orientation returned invalid evidence.')
    if status and 'Too few characters' not in output + error:
        raise ocr.OCRFailed('Local page orientation failed.')
    clockwise = int(angle.group(1)) if status == 0 and angle and confidence >= ORIENTATION_MIN_CONFIDENCE else 0
    saved = dict(identity=identity, clockwise=clockwise, confidence=confidence,
                 uncertain=not (status == 0 and angle and confidence >= ORIENTATION_MIN_CONFIDENCE))
    with tempfile.NamedTemporaryFile(mode='w', dir=root, prefix='.orientation-', delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(saved, handle)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return dict(saved, cached=False)


def reading_display(display, reading):
    """Restore one local reading's established space for guards and source crops."""
    evidence = reading.get('local_ocr_orientation')
    if not reading.get('local_ocr'):
        return display
    from .unit_recovery import SourceIntegrityError, page_fingerprint
    if (not isinstance(evidence, dict) or not isinstance(evidence.get('identity'), dict)
            or evidence['identity'].get('adapter') != ORIENTATION_VERSION):
        raise SourceIntegrityError('Local reading orientation receipt is missing or obsolete.')
    identity = evidence['identity']
    from .extract import pymupdf
    # This controller-owned checkpoint is bound to the complete current source
    # and renderer/configuration. The orientation disk cache separately verifies
    # the rendered SHA before use; restoring preparation need not render again.
    if (not isinstance(identity.get('image'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', identity['image'])
            or type(identity.get('source_orientation')) is not int):
        raise SourceIntegrityError('Invalid local orientation source identity.')
    _, version, data = ocr._engine('eng')
    if (identity.get('engine') != version or identity.get('data') != data
            or identity.get('renderer') != pymupdf.VersionBind
            or identity.get('max_pixels') != ORIENTATION_MAX_PIXELS
            or identity.get('scale') != 4 or identity.get('timeout') != ORIENTATION_TIMEOUT
            or identity.get('min_confidence') != ORIENTATION_MIN_CONFIDENCE
            or identity.get('recognition_adapter') != VERSION
            or identity.get('rect') != list(display.rect)
            or identity.get('source_page') != page_fingerprint(display.doc, display.pno)):
        raise SourceIntegrityError('Local reading orientation belongs to another source or engine configuration.')
    relative = evidence.get('clockwise')
    confidence = evidence.get('confidence')
    if (type(relative) is not int or relative not in (0, 90, 180, 270)
            or type(confidence) not in (int, float) or not math.isfinite(confidence)
            or confidence < 0 or type(evidence.get('uncertain')) is not bool
            or (relative and (confidence < ORIENTATION_MIN_CONFIDENCE or evidence['uncertain']))):
        raise SourceIntegrityError('Invalid local reading orientation.')
    if identity['source_orientation'] != display.angle:
        raise SourceIntegrityError('Local reading orientation belongs to another source space.')
    if not relative:
        return display
    from .source_display import SourceDisplay
    provenance = dict(display.provenance, layer='ocr',
        orientation=(display.angle + relative) % 360,
        source_rotation=display.page.rotation, page_rect=list(display.page.rect))
    if provenance.get('original_frame') is not None:
        # Verify the optional higher resolution lineage before selecting only
        # already-proved prepared pixels. That optimization has no rotated ROI
        # mapping; preserving it would mix two coordinate spaces.
        display.roi_pixmap(scale=.01, max_pixels=16)
        provenance.pop('original_frame')
        provenance['local_ocr_detail_source'] = 'proved_prepared_pixels'
    return SourceDisplay(display.doc, display.pno, provenance)


def read_oriented(display, cache, reason):
    """Keep the primary reading's identity, then orient only its local fallback."""
    evidence = orientation(display, cache)
    upright = reading_display(display, dict(local_ocr=True, local_ocr_orientation=evidence))
    reading = read(upright, cache, reason)
    reading['local_ocr_orientation'] = evidence
    reading['local_ocr_evidence']['orientation'] = evidence
    if upright.provenance.get('local_ocr_detail_source'):
        reading['local_ocr_evidence']['detail_source'] = upright.provenance['local_ocr_detail_source']
    return reading


def read(display, cache, reason):
    words, receipt = recognize(display, cache)
    # TSV fields can carry boundary whitespace. Preserve the engine's literal
    # value while binding each rendered whitespace token to one occurrence.
    canonical = []
    for word in words:
        text = word['text'].strip()
        if len(text.split()) != 1:
            raise ocr.OCRFailed('Local recognition returned a non-atomic word.')
        canonical.append(dict(word, text=text, recognition_text=word['text'])
                         if text != word['text'] else dict(word))
    words = canonical
    if not words:
        raise ocr.OCRFailed('Local recognition found no readable words.')
    paragraphs = {}
    for word in words:
        paragraphs.setdefault(tuple(word['paragraph']), []).append(word)
    blocks, units = [], []
    for index, owned in enumerate(paragraphs.values()):
        ident = 'ocr' + str(index)
        box = vision_reading.union([word['bbox'] for word in owned])
        text = ' '.join(word['text'] for word in owned)
        blocks.append(dict(id=ident, role='body', text=text, marker='',
            bbox=[box[0]/display.rect.width*1000, box[1]/display.rect.height*1000,
                  box[2]/display.rect.width*1000, box[3]/display.rect.height*1000],
            low_confidence=list(range(len(text.split()))),
            continues_previous=False, continues_next=False))
        units.append(dict(id=ident, words=owned, mean_confidence=sum(w['confidence'] for w in owned)/len(owned),
                          low_confidence_tokens=[i for i,w in enumerate(owned) if w['confidence'] < 85]))
    return dict(answer=vision_reading.validate({'blocks': blocks}), local_ocr=True,
        local_ocr_evidence=receipt, local_ocr_units=units, local_ocr_reason=reason,
        cached=receipt['cached'], new_cost_usd=0., unverified_source_reading=True)
