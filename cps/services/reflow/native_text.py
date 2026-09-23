"""Native character placement and explicit fallback for unmapped legacy fonts.

No character is guessed from its apparent language. A missing Unicode map on a
symbolic/custom symbol font keeps the printed pixels instead of claiming that
its character codes are ordinary Latin text.
"""
import hashlib
import json
import re
from dataclasses import replace


def unresolved_fonts(page):
    doc = page.parent
    result = set()
    for xref, _, _, name, _, _ in page.get_fonts():
        name = name.split('+', 1)[-1]
        if doc.xref_get_key(xref, 'ToUnicode')[0] != 'null':
            continue
        kind, flags = doc.xref_get_key(xref, 'FontDescriptor/Flags')
        symbolic = kind == 'int' and int(flags) & 4
        named_symbols = bool(re.search(r'symbol|dingbat|icon|ornament', name, re.I))
        # Standard PDF Symbol/ZapfDingbats have defined encodings understood by
        # MuPDF. Their absence of ToUnicode alone is not evidence of corruption.
        if name not in ('Symbol', 'ZapfDingbats') and (symbolic or named_symbols):
            result.add(name)
    return result


def mark_unmapped_words(spans, traces):
    """Preserve printed words when MuPDF exposes an invalid Unicode mapping.

    get_text may synthesize a plausible Latin character from a CID even though
    the PDF's ToUnicode maps it to U+FFFD. Text trace retains that evidence. The
    entire affected word/marker is untrusted, not an invented Unicode repair.
    """
    bad = [(trace.get('font', ''), char[3]) for trace in traces
           if trace.get('type') != 3 and trace.get('opacity', 1) != 0
           for char in trace.get('chars', ()) if char[0] == 0xfffd]
    if not bad:
        return spans
    result = []
    for span in spans:
        points = [((box[0]+box[2])/2, (box[1]+box[3])/2)
                  for font, box in bad if font == span.font
                  and span.bbox[0] <= (box[0]+box[2])/2 <= span.bbox[2]
                  and span.bbox[1] <= (box[1]+box[3])/2 <= span.bbox[3]]
        if not points or span.encoding_unresolved:
            result.append(span)
            continue
        if not span.char_boxes:
            result.append(replace(span, encoding_unresolved=True))
            continue
        for token in re.finditer(r'\S+|\s+', span.text):
            chars = [c for c in span.char_boxes if c[0] >= token.start() and c[1] <= token.end()]
            if not chars:
                result.append(replace(span, text=token.group(), encoding_unresolved=True))
                continue
            box = (min(c[2] for c in chars), min(c[3] for c in chars),
                   max(c[4] for c in chars), max(c[5] for c in chars))
            uncertain = any(box[0] <= x <= box[2] and box[1] <= y <= box[3] for x,y in points)
            result.append(replace(span, text=token.group(), bbox=box,
                encoding_unresolved=uncertain,
                char_boxes=tuple((c[0]-token.start(), c[1]-token.start(), *c[2:]) for c in chars)))
    return result


def descriptor(pno, bbox, size, font='', raised=False):
    return {'page': pno, 'bbox': [round(v, 4) for v in bbox],
            'size': round(size, 4), 'font': font, 'raised': bool(raised)}


def image_name(record):
    digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:20]
    return 'images/glyph_p%04d_%s.jpg' % (record['page'], digest)


def normalize_blocks(blocks):
    """Attach detached raised spans at measured character boundaries, then initials.

    Character boxes are the PDF's own positions; proportional string splitting
    and dictionary guesses are never used. Ambiguous placements stay unchanged.
    """
    from .extract import Line, Block
    lines = [ln for b in blocks for ln in b.lines]
    replacements, consumed = {}, set()
    for marker in lines:
        if len(marker.spans) != 1 or not re.fullmatch(r'(?:\d{1,3}|st|nd|rd|th)', marker.stripped):
            continue
        small = marker.spans[0]
        targets = []
        for line in lines:
            if line is marker or line.size < small.size / .72:
                continue
            if not (line.bbox[1] - line.size*.5 <= small.bbox[1] < line.bbox[1]+line.size*.2
                    and line.bbox[1] < small.bbox[3] < line.bbox[3]-line.size*.2):
                continue
            for i, span in enumerate(line.spans):
                for boundary in span.char_boxes:
                    start, end, x0, _, x1, _ = boundary
                    if abs(x1-small.bbox[0]) > line.size*.35:
                        continue
                    later = [c for c in span.char_boxes if c[0] >= end]
                    next_x = later[0][2] if later else (line.spans[i+1].bbox[0] if i+1<len(line.spans) else None)
                    if next_x is not None and next_x < small.bbox[2] - line.size*.15:
                        continue
                    targets.append((line, i, end))
        if len(targets) != 1:
            continue
        target, index, offset = targets[0]
        if id(target) in replacements:
            replacements[id(target)] = None
            continue
        span = target.spans[index]
        left = replace(span, text=span.text[:offset], char_boxes=())
        right = replace(span, text=span.text[offset:], char_boxes=())
        raised = replace(small, flags=small.flags | 1)
        spans = target.spans[:index]+[left, raised]+([right] if right.text else [])+target.spans[index+1:]
        replacements[id(target)] = (marker, Line(spans, target.bbox))
    for value in replacements.values():
        if value: consumed.add(id(value[0]))
    out=[]
    for block in blocks:
        kept=[replacements[id(ln)][1] if replacements.get(id(ln)) else ln
              for ln in block.lines if id(ln) not in consumed]
        if kept:out.append(Block(block.number, block.bbox, kept, block.kind))
        elif block.kind != 'text':out.append(block)
    # A single initial reaching over several following body lines is a drop cap,
    # not a standalone heading. Preserve its letter; decorative size is optional.
    lines=[ln for b in out for ln in b.lines];initials={};removed=set()
    for initial in lines:
        if len(initial.spans)!=1 or not re.fullmatch(r'[A-Z]', initial.stripped):continue
        cap=initial.spans[0];targets=[]
        for line in lines:
            if line is initial or not line.stripped[:1].islower() or cap.size<line.size*2:continue
            if abs(cap.bbox[1]-line.bbox[1])>line.size*.4 or not 0<=line.bbox[0]-cap.bbox[2]<=line.size*.4:continue
            followers=[ln for ln in lines if line.bbox[1]<ln.bbox[1]<cap.bbox[3]
                       and abs(ln.bbox[0]-line.bbox[0])<line.size*.4]
            if followers:targets.append(line)
        if len(targets)==1:
            target=targets[0];initials[id(target)]=Line([cap]+target.spans,
                (cap.bbox[0],target.bbox[1],target.bbox[2],target.bbox[3]));removed.add(id(initial))
    return [Block(b.number,b.bbox,[initials.get(id(ln),ln) for ln in b.lines if id(ln) not in removed],b.kind)
            for b in out if b.kind!='text' or any(id(ln) not in removed for ln in b.lines)]


def note_glyph_runs(region, text, pno):
    """Map exact native span intervals through whitespace-only normalization.

    Any other note repair retains a labelled whole-note crop. Repeated letter
    sequences in ordinary prose cannot steal a legacy glyph's source interval.
    """
    raw, intervals = '', []
    for line in region.lines:
        if raw:
            raw += ' '
        for span in line.spans:
            start = len(raw)
            raw += span.text
            if getattr(span, 'encoding_unresolved', False) and span.text.strip():
                intervals.append((start, len(raw), span))
    prefix = re.match(r'^\s*' + re.escape(str(region.number)) + r'[.)]?\s*', raw) if region.number is not None else None
    skip = prefix.end() if prefix else len(raw)-len(raw.lstrip())
    raw = raw[skip:]
    normalized, offsets = '', [0]
    for char in raw:
        if not char.isspace():
            normalized += char
        elif normalized and not normalized.endswith(' '):
            normalized += ' '
        offsets.append(len(normalized))
    normalized = normalized.rstrip()
    if normalized != text:
        return []
    runs, cursor = [], 0
    for start, end, span in intervals:
        if start < skip:
            return []
        lo, hi = offsets[start-skip], min(offsets[end-skip], len(text))
        if lo < cursor:
            return []
        runs.append(['t', text[cursor:lo]])
        runs.append(['glyph', text[lo:hi], descriptor(pno, span.bbox, span.size, span.font)])
        cursor = hi
    runs.append(['t', text[cursor:]])
    return [run for run in runs if run[1]]


def glyph_html(record, block=False):
    """Printed evidence stays inline; the link explains unavailable text encoding."""
    from html import escape
    style='max-width:100%;height:auto' if block else 'height:1em;width:auto;vertical-align:baseline'
    label='Original source text; Unicode encoding unavailable. Open original page.'
    html = ('<a class="source-glyph" href="original-p%04d.xhtml#page" title="%s">'
            '<img src="%s" alt="%s" style="%s"/></a>' %
            (record['page'],escape(label,quote=True),image_name(record),escape(label,quote=True),style))
    return '<sup>'+html+'</sup>' if record.get('raised') else html


def package_glyphs(book,page_html,doc,package,should_stop=None):
    from .source_display import SourceDisplay
    records={}
    for pno,elements in book.pages.items():
        if pno not in page_html:continue
        for element in elements:
            for run in element.runs:
                if run[0]=='glyph':records[image_name(run[2])]=run[2]
    for note in book.notes:
        if note.pno in page_html and getattr(note,'glyph_fallback',False):
            if getattr(note, 'glyph_runs', None):
                for run in note.glyph_runs:
                    if run[0] == 'glyph':
                        records[image_name(run[2])] = run[2]
            else:
                record=descriptor(note.pno,note.bbox,0,'note');records[image_name(record)]=record
    if records and doc is None:raise ValueError('Original PDF required for unmapped source glyphs')
    for name,record in records.items():
        if should_stop and should_stop():
            from .build_epub import BuildCancelled
            raise BuildCancelled('Source glyph rendering cancelled')
        display=SourceDisplay(doc,record['page'])
        rect=display.reading_rect(record['bbox'])
        if rect.is_empty:raise ValueError('Source glyph crop is outside page')
        package.image(name,display.jpeg(rect,scale=6,quality=95))
