# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservative native separator and unfinished-source evidence.

This does not join or rewrite prose. A possible continuation is sufficient to
withhold a complete-quotation claim; moving text into notes additionally requires
a native separator and an identified preceding note.
"""
import re
from . import extract

_END = re.compile(r'[.!?](?:[\"\u201d\u2019\)\]]*|[\"\u201d\u2019]+\d{1,3})\s*$')


def may_continue(previous, following):
    previous, following = previous.rstrip(), following.lstrip()
    if not previous or not following or _END.search(previous):
        return False
    # Editorial insertions can begin the continuation: "an equal [number]".
    first = re.sub(r'^\[([^\]]+)\]', r'\1', following, count=1)
    return first[:1].islower() or first[:1] in ',;'


def _normalize(block):
    """Bind a geometrically raised opening digit even when PDF order is reversed."""
    lines = list(block.lines)
    for marker in list(lines):
        if not re.fullmatch(r'\d{1,3}', marker.stripped):
            continue
        targets = [ln for ln in lines if ln is not marker and ln.size >= marker.size / .7
                   and 0 <= ln.bbox[0]-marker.bbox[2] <= ln.size
                   and marker.bbox[1] <= ln.bbox[1] <= marker.bbox[3]
                   and ln.bbox[3] > marker.bbox[3]]
        if len(targets) != 1:
            continue
        target = targets[0]
        bbox = (marker.bbox[0], min(marker.bbox[1], target.bbox[1]),
                target.bbox[2], target.bbox[3])
        joined = extract.Line(marker.spans + target.spans, bbox)
        lines = [joined if ln is target else ln for ln in lines if ln is not marker]
    return extract.Block(block.number, block.bbox, lines, block.kind)


def ruled_region(raw):
    """Return normalized below-rule blocks only for an isolated native short rule.

    A divider is merely a region boundary. It is never by itself proof of a note.
    OCR, dense grids, vertical dividers, and mixed-column regions abstain.
    """
    if raw.is_page_scan or getattr(raw, 'source_geometry', {}).get('space') == 'reading':
        return None
    blocks = raw.text_blocks
    if not blocks:
        return None
    em = max((b.size for b in blocks), default=0)
    if not em:
        return None
    left = min(b.bbox[0] for b in blocks)
    right = max(b.bbox[2] for b in blocks)
    width = right-left
    rects = raw.drawing_rects
    rules = [r for r in rects if 0 <= r[3]-r[1] <= em*.2
             and width*.2 <= r[2]-r[0] <= width*.8
             and abs(r[0]-left) <= em*.75
             and raw.height*.2 <= r[1] <= raw.height*.85]
    if len(rules) != 1:
        return None
    rule = rules[0]
    if any(r[3]-r[1] > em*2 and r[2] >= rule[0] and r[0] <= right
           and r[3] >= rule[1] for r in rects):
        return None
    below, outside = [], []
    text_bottom = max((b.bbox[3] for b in blocks if not re.fullmatch(r'\d{1,4}', b.text.strip())), default=0)
    for b in blocks:
        # A standalone folio left of the note text is not part of the note.
        if b.bbox[1] <= rule[3] or (re.fullmatch(r'\d{1,4}', b.text.strip())
                and (b.bbox[0] <= left+em*.5 or b.bbox[1]-text_bottom >= em*2)):
            outside.append(b)
        else:
            below.append(_normalize(b))
    if not below or not any(b.bbox[3] < rule[1] for b in outside):
        return None
    if below[0].bbox[1]-rule[3] > em*3:
        return None
    if any(b.bbox[0] < left-em*.5 or b.bbox[2] > right+em*.5 for b in below):
        return None
    return outside, below, tuple(rule)


def pixel_separated_note(raw, body_size, pixel_probe):
    """A native N. opening, isolated painted separator, and unique callout.

    Some native PDFs embed the note divider as a narrow bitmap rather than a
    drawing. Number equality alone cannot establish a note: all three source
    witnesses and the entire lower-page territory must agree. Raw text and
    image primitives are never edited by this proof.
    """
    if (pixel_probe is None or not body_size or raw.is_page_scan
            or raw.drawings or raw.drawing_rects
            or getattr(raw,'source_geometry',{}).get('space')=='reading'
            or getattr(raw,'transcript_unverified',False)):
        return None
    candidates=[]
    for block in raw.text_blocks:
        if len(block.lines)<2 or block.bbox[1]<raw.height*.65:continue
        opening,following=block.lines[:2]
        literal=re.fullmatch(r'([1-9]\d{0,2})\.',opening.stripped)
        if (not literal or len(opening.spans)!=1 or len(following.stripped.split())<3
                or not following.stripped[:1].isupper()
                or following.size>body_size*.95
                or not 0<=following.bbox[0]-opening.bbox[2]<=body_size*3
                or not opening.bbox[1]<=following.bbox[1]<=opening.bbox[3]
                or following.bbox[3]<opening.bbox[3]-.25*body_size):continue
        if any(sp.font=='ocr' or sp.uncertain or sp.encoding_unresolved or sp.transcription_uncertain
               for ln in block.lines for sp in ln.spans):continue
        candidates.append((block,int(literal[1])))
    if len(candidates)!=1:return None
    note,number=candidates[0]
    outside=[b for b in raw.text_blocks if b is not note]
    prose=[b for b in outside if not re.fullmatch(r'\d{1,4}',b.text.strip())]
    if (not prose or any(b.bbox[3]>note.bbox[1] for b in outside)):
        return None
    left=min(b.bbox[0] for b in prose);right=max(b.bbox[2] for b in prose)
    if (right-left<body_size*12 or abs(note.bbox[0]-left)>body_size
            or note.bbox[2]>right+body_size*.5):return None
    top=max(b.bbox[3] for b in prose)
    if not body_size<=note.bbox[1]-top<=body_size*4:return None
    callouts=[]
    for block in prose:
        for ln in block.lines:
            for sp in ln.spans:
                if sp.text.strip()!=str(number):continue
                if (sp.font=='ocr' or sp.uncertain or sp.encoding_unresolved or sp.transcription_uncertain
                        or sp.size>ln.size*.7):continue
                height=ln.bbox[3]-ln.bbox[1]
                if height>0 and sp.bbox[3]<ln.bbox[3]-.25*height:
                    callouts.append(sp)
    if len(callouts)!=1:return None
    gap=(left,top,right,note.bbox[1])
    rules=pixel_probe.rules(gap)
    if len(rules)!=1:return None
    if (not hasattr(pixel_probe,'thin_separator') or
            pixel_probe.thin_separator(gap,min(2,body_size*.25)) is None):return None
    ink=pixel_probe.ink_bounds(gap)
    if (not ink or ink[2]-ink[0]<(right-left)*.6 or ink[3]-ink[1]>body_size*.75
            or not ink[1]<=rules[0]<=ink[3]
            or ink[1]-top<body_size*.35):return None
    return outside,note,number,ink


def raised_opening(block):
    if not block.lines:
        return False
    spans = [s for s in block.lines[0].spans if s.text.strip()]
    return bool(len(spans)>1 and re.fullmatch(r'\d{1,3}', spans[0].text.strip())
                and spans[0].size <= block.size*.7
                and not any(s.uncertain for s in spans))


def bind_callouts(blocks, numbers):
    """Reattach a detached native raised digit at a uniquely bounded span edge.

    The numeral must name an actually extracted note. No character positions are
    estimated and no span text is split: ambiguous interior positions stay intact.
    """
    lines = [ln for b in blocks for ln in b.lines]
    replacements, consumed = {}, set()
    for marker in lines:
        if not re.fullmatch(r'\d{1,3}', marker.stripped) or int(marker.stripped) not in numbers:
            continue
        targets = []
        for ln in lines:
            if ln is marker or ln.size < marker.size/.7:
                continue
            em = ln.bbox[3]-ln.bbox[1]
            if not (ln.bbox[1]-em*.5 <= marker.bbox[1] and
                    marker.bbox[3] < ln.bbox[3]-em*.25 and marker.bbox[3] > ln.bbox[1]):
                continue
            for i,sp in enumerate(ln.spans):
                if abs(sp.bbox[2]-marker.bbox[0]) > em*.5:
                    continue
                if i+1<len(ln.spans) and ln.spans[i+1].bbox[0]<marker.bbox[2]:
                    continue
                targets.append((ln,i+1))
        if len(targets)!=1:
            continue
        target,position=targets[0]
        # Multiple detached digits at the same text line ask for richer ordering
        # evidence, not a guess based on whichever block was visited first.
        if id(target) in replacements:
            replacements[id(target)]=None
            continue
        replacements[id(target)]=(marker,position)
    for value in replacements.values():
        if value:consumed.add(id(value[0]))
    out=[]
    for block in blocks:
        kept=[]
        for ln in block.lines:
            if id(ln) in consumed:continue
            replacement=replacements.get(id(ln))
            if replacement:
                marker,position=replacement
                spans=ln.spans[:position]+marker.spans+ln.spans[position:]
                ln=extract.Line(spans,(min(ln.bbox[0],marker.bbox[0]),min(ln.bbox[1],marker.bbox[1]),
                    max(ln.bbox[2],marker.bbox[2]),max(ln.bbox[3],marker.bbox[3])))
            kept.append(ln)
        if kept:out.append(extract.Block(block.number,block.bbox,kept,block.kind))
    return out
