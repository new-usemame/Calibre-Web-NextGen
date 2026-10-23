"""Require painted glyphs and original pixels before trusting source-script labels."""
import io
import math
import re
import unicodedata

from PIL import Image

from . import extract

VERSION = 'visible-source-scripts-1'
MAX_RASTER_PIXELS = 8_000_000
MAX_CANDIDATE_GLYPHS = 4096
# These letter shapes alone do not distinguish a non-Latin script in pixels.
# Requiring a distinctive glyph avoids turning identical capital/symbol forms
# into a demand for a different alphabet in otherwise Latin prose.
LATIN_CONFUSABLES = frozenset('ΑΒΕΖΗΙΚΜΝΟΡΤΥΧϹАВЕКМНОРСТХӀ')
SCRIPTS = frozenset({'GREEK', 'CYRILLIC', 'HEBREW', 'ARABIC', 'DEVANAGARI',
                     'HIRAGANA', 'KATAKANA', 'HANGUL', 'CJK'})


def _script(char):
    name = unicodedata.name(char, '')
    script = name.split(' ', 1)[0]
    return script if char.isalpha() and script in SCRIPTS else None


def _font_key(name):
    return re.sub('[^a-z0-9]', '', re.sub(r'^[A-Z]{6}\+', '', name or '').lower())


def _fonts(display):
    """Match actual embedded-font glyph IDs, independently of PDF ToUnicode."""
    fonts = {}
    for entry in display.page.get_fonts(full=True):
        try:
            name, extension, kind, buffer = display.doc.extract_font(entry[0])
            if not buffer:
                continue
            font = extract.pymupdf.Font(fontbuffer=buffer)
            for value in (entry[3], font.name):
                fonts.setdefault(_font_key(value), []).append(font)
        except (RuntimeError, ValueError, TypeError):
            continue
    return fonts


def _page_evidence(display):
    """Return source-script letters with positive glyph and pixel support.

    English OCR is deliberately not used: its Latin output cannot establish the
    absence of a visible passage in another script. Conversely a text-layer
    Unicode label alone proves neither glyph identity nor visibility. Unsupported
    fonts and Latin-confusable forms remain unproved rather than inventing a
    script requirement. This does not transcribe or replace any source text.
    """
    if hasattr(display, '_visible_script_evidence'):
        return display._visible_script_evidence
    rejected = {}; counts = {}; letters = []; candidates = []; glyphs = []
    def refuse(reason):
        rejected[reason] = rejected.get(reason, 0) + 1
    try:
        traces = display.page.get_texttrace()
    except (RuntimeError, AttributeError):
        traces = []
    for span in traces:
        for item in span.get('chars', ()):
            if not 0 <= item[0] <= 0x10ffff:
                continue
            char = chr(item[0]); script = _script(char)
            if not script:
                continue
            if len(candidates) >= MAX_CANDIDATE_GLYPHS:
                refuse('candidate_limit'); continue
            candidates.append((span, item, char, script))
    if candidates:
        fonts = _fonts(display)
        pix = display.pixmap(scale=3, gray=True, max_pixels=MAX_RASTER_PIXELS)
        picture = Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
        sx, sy = picture.width / display.rect.width, picture.height / display.rect.height
        for span, item, char, script in candidates:
            if span.get('type') not in (0, 1, 2) or span.get('opacity', 1) <= 0:
                refuse('not_painted'); continue
            if char in LATIN_CONFUSABLES:
                refuse('latin_confusable'); continue
            matches = fonts.get(_font_key(span.get('font')), ())
            # Trace names can collide across embedded font subsets. A glyph
            # cannot borrow a script mapping from just one ambiguous candidate.
            if not matches or not all(font.has_glyph(item[0], fallback=False) == item[1] and item[1] > 0 for font in matches):
                refuse('glyph_mapping_unproved'); continue
            rect = display.reading_rect(item[3])
            if rect.is_empty:
                refuse('outside_page'); continue
            box = (max(0, int(rect.x0*sx)), max(0, int(rect.y0*sy)),
                   min(picture.width, int(rect.x1*sx+.999)), min(picture.height, int(rect.y1*sy+.999)))
            if box[2] <= box[0] or box[3] <= box[1]:
                refuse('outside_page'); continue
            crop = picture.crop(box); histogram = crop.histogram()
            low, high = crop.getextrema()
            # A constant patch, including a white-on-white glyph, supplies no
            # visible shape. Ignore antialias noise occupying less than 1%.
            contrast = high-low
            ink = sum(histogram[:max(0, high-16)])
            paper = sum(histogram[min(255, low+16):])
            if contrast < 32 or min(ink, paper) < max(2, crop.width*crop.height*.01):
                refuse('no_pixel_contrast'); continue
            letters.append(char); counts[script] = counts.get(script, 0)+1
            glyphs.append(dict(text=char, script=script, bbox=list(rect)))
    result = dict(version=VERSION, text=''.join(letters), scripts=counts,
                  candidates=len(candidates), rejected=rejected, glyphs=glyphs)
    display._visible_script_evidence = result
    return result


def evidence(display, rectangles=None):
    """Scope proved glyphs to upright reading-space rectangles, if supplied.

    A disclosed source unit no longer requires a textual transcription from its
    retained siblings. Scope by glyph center so a bordering line does not lend
    its script to the adjacent block. Rejection diagnostics refer to the whole
    page; the returned glyphs, text and script counts refer only to this scope.
    """
    result = _page_evidence(display)
    if rectangles is None:
        return result
    boxes = [extract.pymupdf.Rect(box) for box in rectangles]
    for box in boxes:
        if box.is_empty or box.is_infinite or any(not math.isfinite(v) for v in box):
            raise ValueError('Invalid source-script scope rectangle')
    glyphs = []
    for glyph in result['glyphs']:
        left, top, right, bottom = glyph['bbox']
        if any(box.x0 <= (left+right)/2 <= box.x1 and box.y0 <= (top+bottom)/2 <= box.y1 for box in boxes):
            glyphs.append(glyph)
    counts = {}
    for glyph in glyphs:
        script = glyph['script']; counts[script] = counts.get(script, 0)+1
    return dict(result, text=''.join(g['text'] for g in glyphs), glyphs=glyphs,
                scripts=counts, scope='source_regions', rejected_scope='whole_page')


def source_text(display, rectangles=None):
    """Unicode source-script evidence suitable for script_mismatches(source, reading)."""
    return evidence(display, rectangles=rectangles)['text']
