"""Native character placement and explicit fallback for unmapped legacy fonts.

No character is guessed from its apparent language. A missing Unicode map on a
symbolic/custom symbol font keeps the printed pixels instead of claiming that
its character codes are ordinary Latin text.
"""
import hashlib
import json
import re
from functools import lru_cache
from dataclasses import replace

from . import unit_recovery


def text_layer_overpainted(page, traces, *, word_overlap=False):
    """A later unmasked page image prevents native font codes proving glyphs.

    This is conservative paint-order provenance, not an OCR confidence score.
    Transparent/masked images cannot establish it; text drawn after the image
    stays native. No character identity is inferred from the covered layer.
    """
    if not traces:
        return False
    log = page.get_bboxlog()
    images = [(i, box) for i,(kind,box) in enumerate(log) if kind == 'fill-image']
    if word_overlap:
        # Native word authority requires its own ink to remain unobscured.
        # A later masked image is also insufficient positive paint proof.
        # Ignore only sub-millipoint coordinate round trips at touching edges.
        return any(seq > trace.get('seqno',len(log)) and
            min(box[2],trace['bbox'][2])-max(box[0],trace['bbox'][0])>1e-3 and
            min(box[3],trace['bbox'][3])-max(box[1],trace['bbox'][1])>1e-3
            for seq,box in images for trace in traces)
    info = page.get_image_info()
    if len(info) != len(images):
        return False
    carriers = [(seq,box) for (seq,box),record in zip(images,info)
                if record.get('has-mask') is False]
    return bool(carriers) and all(any(seq > trace.get('seqno', len(log))
        and box[0] <= trace['bbox'][0] and box[1] <= trace['bbox'][1]
        and box[2] >= trace['bbox'][2] and box[3] >= trace['bbox'][3]
        for seq,box in carriers) for trace in traces)


@lru_cache(maxsize=64)
def _has_word_glyph_map(data):
    """Whole-word glyph labels are optical hypotheses, not letter identity.

    Reconstructed vector text can assign arbitrary strings to one ink shape.
    A ToUnicode table then helps extraction but cannot independently certify
    its own transcription. Ordinary typographic ligatures are unaffected.
    """
    ligatures={'ff','fi','fl','ffi','ffl','st','ct','Th','ij','IJ'}
    for section in re.findall(rb'beginbfchar(.*?)endbfchar',data,re.S):
        for target in re.findall(rb'<[^>]+>\s*<([0-9a-fA-F]+)>',section):
            try:value=bytes.fromhex(target.decode('ascii')).decode('utf-16-be')
            except (ValueError,UnicodeError):return True
            if sum(c.isalpha()for c in value)>1 and value not in ligatures:
                return True
    return False


def _text_font_authorities(page, *, optical_maps=False):
    bad=unresolved_fonts(page);faces={}
    for font in page.get_fonts():
        name=font[3].split('+',1)[-1]
        kind,target=page.parent.xref_get_key(font[0],'ToUnicode')
        mapped=kind!='null'
        optical=False
        if mapped and kind=='xref' and not optical_maps and hasattr(page.parent,'xref_stream'):
            optical=_has_word_glyph_map(page.parent.xref_stream(int(target.split()[0])) or b'')
        standard=bool(re.fullmatch(r'(?:Times(?:NewRoman)?|Arial|Helvetica|Courier)(?:[-,](?:Roman|Bold|Italic|Oblique|BoldItalic|BoldOblique)|PS(?:MT|-BoldMT|-ItalicMT|-BoldItalicMT)?|MT)?',name,re.I))
        faces[name]=(mapped or standard) and name not in bad and not optical
    return faces


def _face_authority(faces,name):
    # Embedded resource names and PostScript trace names may differ only in
    # separators. Every colliding resource must have the same reliable map.
    # Type-0 resource BaseFont names may carry their CMap encoding suffix,
    # while MuPDF's paint trace reports only the embedded face. Strip only
    # these exact standard encoding suffixes; all aliases must still agree
    # on character-map authority, including colliding unmapped resources.
    canonical=lambda value:re.sub(r'[^a-z0-9]','',
        re.sub(r'-Identity-[HV]$', '', value, flags=re.I).casefold())
    name=canonical(name)
    matches=[value for key,value in faces.items()if canonical(key)==name]
    if matches:return all(matches)
    # MuPDF may also abbreviate an embedded PostScript face in text traces.
    matches=[value for key,value in faces.items()if len(name)>=15 and canonical(key).startswith(name)]
    return bool(matches) and all(matches)


def _inside_character(char, boxes):
    return any(box[0]-.2 <= (char[3][0]+char[3][2])/2 <= box[2]+.2 and
               box[1]-.2 <= (char[3][1]+char[3][3])/2 <= box[3]+.2 for box in boxes)


def fraction_word_view(page, words):
    """Bind separately drawn fraction atoms only at their measured positions."""
    from collections import Counter
    from . import extract
    words=list(words)
    if not any('/'in w['text']for w in words) or not any(w['text'].isdigit()for w in words):return words
    raw=extract.read_page(page.parent,page.number,keep_char_boxes=True)
    fractions=[part for block in raw.blocks for line in block.lines for span in line.spans
               for part in getattr(span,'source_fraction_parts',())]
    proposals=[]
    for num,den,a,slash,b in fractions:
        parts=[dict(text=num,bbox=a),dict(text='/',bbox=slash),dict(text=den,bbox=b)]
        boxes=[a,slash,b];indexes=[]
        for i,word in enumerate(words):
            box=word['bbox']
            if any(box[0]-.2<=(part[0]+part[2])/2<=box[2]+.2 and
                   box[1]-.2<=(part[1]+part[3])/2<=box[3]+.2 for part in boxes):indexes.append(i)
        if Counter(''.join(words[i]['text']for i in indexes))!=Counter(num+'/'+den):continue
        value=dict(text=num+'/'+den,native_parts=parts,
                   bbox=(min(p[0]for p in boxes),min(p[1]for p in boxes),
                         max(p[2]for p in boxes),max(p[3]for p in boxes)))
        if not drawn_word_authority(page,[value]):continue
        proposals.append((indexes,value))
    consumed=set();groups={}
    for indexes,value in proposals:
        if any(sum(i in ids for ids,_ in proposals)!=1 for i in indexes):continue
        first=min(indexes);groups[first]=value;consumed.update(indexes)
    return [groups[i]if i in groups else w for i,w in enumerate(words)if i not in consumed or i in groups]


def lexical_word_view(page, words, marker_prefixes=()):
    """Bind font fragments into printed words without changing their letters.

    Decorative initials can be serialized after their paragraph, and raised
    ordinal suffixes can be separate words. Their original component boxes
    remain separate: the bounding rectangle must not borrow a following row.
    """
    words=fraction_word_view(page,words);traces=page.get_texttrace();pairs=[]
    expanded=[]
    for word in words:
        match=re.fullmatch(r'(.+[A-Za-z].*?)(\d{1,4})',word['text'])
        if not match or match[1]not in marker_prefixes:
            expanded.append(word);continue
        chars=sorted([c for trace in traces for c in trace.get('chars',())
                      if c[0]>32 and _inside_character(c,[word['bbox']])],key=lambda c:c[2][0])
        if (match and match[1]in marker_prefixes and
                ''.join(chr(c[0])for c in chars)==word['text']):
            before,after=chars[:-len(match[2])],chars[-len(match[2]):]
            def bounds(group):
                return [min(c[3][0]for c in group),min(c[3][1]for c in group),
                        max(c[3][2]for c in group),max(c[3][3]for c in group)]
            a,b=bounds(before),bounds(after);height=a[3]-a[1]
            if b[3]-b[1]<height*.9 and b[3]<a[3]-height*.15:
                # Reserve independently raised ink for the existing marker
                # verifier; neither its identity nor note owner is proved here.
                expanded.extend([dict(word,text=match[1],bbox=a),dict(word,text=match[2],bbox=b,native_reference_ink=True)])
                continue
        expanded.append(word)
    words=expanded
    def bound(word):
        import unicodedata
        chars=[c for trace in traces for c in trace.get('chars',())
               if c[0]>32 and _inside_character(c,[word['bbox']])]
        value=''.join(chr(c[0])for c in sorted(chars,key=lambda c:c[2][0]))
        return unicodedata.normalize('NFKC',value)==unicodedata.normalize('NFKC',word['text'])
    def separator(a,b):
        return any(c[0]==32 and a[2] <= (c[3][0]+c[3][2])/2 <= b[0] and
            min(a[3],b[3],c[3][3])>max(a[1],b[1],c[3][1])
            for trace in traces for c in trace.get('chars',()))
    for i,head in enumerate(words):
        if not re.fullmatch(r'[A-Z]|[([{\"‘“]*[0-9]+',head['text']) or not bound(head):continue
        a=head['bbox'];height=a[3]-a[1];targets=[]
        for j,tail in enumerate(words):
            if i==j:continue
            b=tail['bbox'];small=b[3]-b[1]
            cap=(re.fullmatch('[A-Z]',head['text']) and tail['text'][:1].islower()
                 and height>=small*2 and abs(a[1]-b[1])<=small*.4 and
                 0<=b[0]-a[2]<=small*.4 and any(
                     b[1]<w['bbox'][1]<a[3] and abs(w['bbox'][0]-b[0])<small*.4
                     for k,w in enumerate(words)if k not in(i,j)))
            ordinal=(bool(re.fullmatch(r'[([{\"‘“]*[0-9]+',head['text'])) and tail['text']in('st','nd','rd','th')
                     and small<height*.9 and b[3]<a[3]-height*.15 and
                     min(a[3],b[3])>max(a[1],b[1]) and
                     0<=b[0]-a[2]<=small*.4)
            if (cap or ordinal) and not separator(a,b) and bound(tail):targets.append(j)
        if len(targets)==1:pairs.append((i,targets[0]))
    used={};groups={}
    for i,j in pairs:
        if sum(i in pair or j in pair for pair in pairs)!=1:continue
        parts=[words[i],words[j]];boxes=[p['bbox']for p in parts]
        combined=dict(words[j],text=''.join(p['text']for p in parts),
            bbox=(min(b[0]for b in boxes),min(b[1]for b in boxes),
                  max(b[2]for b in boxes),max(b[3]for b in boxes)),
            native_parts=[dict(text=p['text'],bbox=p['bbox'])for p in parts])
        at=min(i,j);groups[at]=combined;used[i]=used[j]=at
    return [groups[i]if i in groups else word for i,word in enumerate(words)
            if i not in used or used[i]==i]


def unproved_legacy_word(page,word):
    """A visible custom code without a character map needs original glyph ink."""
    faces=_text_font_authorities(page,optical_maps=True);boxes=[p['bbox']for p in word.get('native_parts',[word])]
    return any(trace.get('type')in(0,1) and trace.get('opacity',1)>=.99 and
        not _face_authority(faces,trace.get('font','').split('+',1)[-1]) and
        any(char[0]>32 and _inside_character(char,boxes)for char in trace.get('chars',()))
        for trace in page.get_texttrace())


def word_prefix_ink(page,word,suffix):
    """Locate original prefix ink without copying a separately proved action.

    Exact character order binds geometry only; it does not certify the custom
    font's decoded spelling. Ambiguous or transformed character inventories
    decline this cut rather than duplicate or delete a reference label.
    """
    boxes=[p['bbox']for p in word.get('native_parts',[word])]
    chars=sorted([c for trace in page.get_texttrace()for c in trace.get('chars',())
        if c[0]>32 and _inside_character(c,boxes)],key=lambda c:c[2][0])
    text=''.join(chr(c[0])for c in chars)
    if not suffix or text!=word['text'] or not text.endswith(suffix) or len(text)<=len(suffix):return None
    def bounds(group):
        return (min(c[3][0]for c in group),min(c[3][1]for c in group),
            max(c[3][2]for c in group),max(c[3][3]for c in group))
    return bounds(chars[:-len(suffix)]),bounds(chars[-len(suffix):])


def drawn_word_authority(page, words):
    """Every occurrence must have reliable, visible native character proof."""
    return bool(words) and all(drawn_word_authorities(page,words))


def drawn_word_authorities(page, words):
    """Return reliable visible character proof for each native word."""
    return _drawn_word_authorities(page,words,require_mapping=True)


def visible_word_ink(page, words):
    """Prove original glyph placement, without certifying decoded letters."""
    return _drawn_word_authorities(page,words,require_mapping=False)


def _drawn_word_authorities(page, words, *, require_mapping):
    """Per-word native paint proof, with optional decoded-letter authority.

    A custom face without ToUnicode can deliberately draw a symbol at a Latin
    character code even when its descriptor claims nonsymbolic text. Only an
    explicit Unicode map or a recognized standard text face earns authority.
    Invisible, overpainted, replacement and private-use characters never do.
    A failed word does not transfer its uncertainty to reliable neighbors.
    """
    import unicodedata
    traces=page.get_texttrace()
    faces=_text_font_authorities(page)
    def safe(text):
        return all(c!='\ufffd' and unicodedata.category(c) not in ('Co','Cs','Cn')for c in text)
    result=[]
    for word in words:
        boxes=[p['bbox']for p in word.get('native_parts',[word])];chars=[];owners=[];valid=True
        for trace in traces:
            own=[char for char in trace.get('chars',())if char[0]>32 and _inside_character(char,boxes)]
            if not own:continue
            if (trace.get('type')not in(0,1) or trace.get('opacity',1)<.99 or
                    (require_mapping and not _face_authority(faces,trace.get('font','').split('+',1)[-1]))):valid=False
            owners.append(dict(trace,bbox=(min(c[3][0]for c in own),min(c[3][1]for c in own),
                max(c[3][2]for c in own),max(c[3][3]for c in own))))
            chars.extend(own)
        value=''.join(chr(char[0])for char in sorted(chars,key=lambda char:char[2][0]))
        literal=''.join(c for c in word['text']if not c.isspace())
        identity=(safe(value+literal) and unicodedata.normalize('NFKC',value)==unicodedata.normalize('NFKC',literal)
                  if require_mapping else len(chars)==len(literal) and all(c[1]>0 for c in chars))
        # Missing character mappings may decode as replacement characters even
        # while the glyph IDs and placements are intact. Only image placement
        # may use that evidence; it never authorizes the decoded spelling.
        result.append(bool(valid and owners and not text_layer_overpainted(page,owners,word_overlap=True)
            and identity))
    return result


def synthetic_spacing_uncertain(spans):
    """Repeated inserted spaces in short native glyph groups are not word proof.

    Encoded whitespace and font/size/baseline changes end a group. We preserve
    pixels, never remove spaces or infer a dictionary word. A conservative false
    positive therefore changes presentation without inventing transcription.
    """
    groups, chars, previous = [], [], None
    for span in spans:
        size = span.get('size', 0)
        origin = span.get('origin', (0, 0))
        if previous is not None:
            same = (span.get('font') == previous.get('font') and size > 0
                    and abs(size-previous.get('size', 0)) <= .05*size
                    and abs(origin[1]-previous.get('origin', (0,0))[1]) <= .15*size
                    and -.15*size <= span['bbox'][0]-previous['bbox'][2] <= .6*size)
            if not same:
                groups.append(chars); chars = []
        for char in span.get('chars', ()):
            if char.get('c', '').isspace() and not char.get('synthetic', False):
                groups.append(chars); chars = []
            else:
                chars.append(char)
        previous = span
    groups.append(chars)
    for group in groups:
        text = ''.join(c.get('c', '') for c in group)
        inserted = sum(c.get('synthetic', False) and c.get('c', '').isspace() for c in group)
        pieces = text.split()
        if (inserted >= 3 and 4 <= sum(c.isalpha() for c in text) <= 80
                and pieces and max(map(len, pieces)) <= 3):
            return True
    return False


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
        return _whole_source_words(spans)
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
    return _whole_source_words(result)


def _whole_source_words(spans):
    """Join an unresolved word across native font/style span boundaries.

    Whitespace, a column-sized gap, or a different baseline ends the unit. No
    word spelling or intended Unicode character participates in this proof.
    """
    if not any(span.encoding_unresolved or span.transcription_uncertain for span in spans):
        return spans
    pieces = []
    for span in spans:
        if not span.char_boxes:
            pieces.append(span)
            continue
        for token in re.finditer(r'\S+|\s+', span.text):
            chars = [c for c in span.char_boxes if c[0] >= token.start() and c[1] <= token.end()]
            if not chars:
                pieces.append(replace(span, text=token.group(),
                    encoding_unresolved=span.encoding_unresolved and bool(token.group().strip()),
                    transcription_uncertain=span.transcription_uncertain and bool(token.group().strip())))
                continue
            box = (min(c[2] for c in chars), min(c[3] for c in chars),
                   max(c[4] for c in chars), max(c[5] for c in chars))
            pieces.append(replace(span, text=token.group(), bbox=box,
                encoding_unresolved=span.encoding_unresolved and bool(token.group().strip()),
                transcription_uncertain=span.transcription_uncertain and bool(token.group().strip()),
                char_boxes=tuple((c[0]-token.start(), c[1]-token.start(), *c[2:]) for c in chars)))
    result, group = [], []

    def flush():
        if len(group) < 2 or not any(s.encoding_unresolved or s.transcription_uncertain for s in group):
            result.extend(group)
            return
        box = (min(s.bbox[0] for s in group), min(s.bbox[1] for s in group),
               max(s.bbox[2] for s in group), max(s.bbox[3] for s in group))
        offsets, chars, flags = 0, [], group[0].flags
        for span in group:
            chars.extend((c[0]+offsets, c[1]+offsets, *c[2:]) for c in span.char_boxes)
            offsets += len(span.text)
            flags &= span.flags
        result.append(replace(group[0], text=''.join(s.text for s in group), bbox=box,
            font=group[0].font if len({s.font for s in group}) == 1 else 'mixed-native-fonts',
            flags=flags, encoding_unresolved=any(s.encoding_unresolved for s in group),
            transcription_uncertain=any(s.transcription_uncertain for s in group),char_boxes=tuple(chars)))

    for piece in pieces:
        if group:
            previous = group[-1]
            em = min(previous.size, piece.size)
            adjacent = (em > 0 and not any(c.isspace() for c in previous.text+piece.text)
                and -.15*em <= piece.bbox[0]-previous.bbox[2] <= .25*em
                and abs(piece.bbox[3]-previous.bbox[3]) <= .2*em
                and abs(piece.size-previous.size) <= .15*em)
            if not adjacent:
                flush()
                group = []
        group.append(piece)
    flush()
    return result


def descriptor(pno, bbox, size, font='', raised=False, reason='encoding'):
    return {'page': pno, 'bbox': [round(v, 4) for v in bbox],
            'size': round(size, 4), 'font': font, 'raised': bool(raised),
            **({'reason':reason} if reason in ('transcript','layout') else {})}


def image_name(record):
    digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:20]
    return 'images/glyph_p%04d_%s.jpg' % (record['page'], digest)


def _attach_stacked_fractions(blocks):
    """Read a PDF's separately drawn numerator/slash/denominator as one token.

    Both small atoms must bracket the slash horizontally and vertically. A
    denominator may be a single variable; a lone raised digit remains available
    to the ordinary note/ordinal handling.
    Ambiguous matches remain separate source atoms rather than guessed text.
    """
    from .extract import Line, Block
    lines = [line for block in blocks for line in block.lines]
    parts = [Line([span],span.bbox) for line in lines for span in line.spans
             if re.fullmatch(r'(?:\d{1,3}|[A-Za-z])',span.text.strip())
             and not (span.encoding_unresolved or span.transcription_uncertain)]
    # Discover every plausible owner before changing any line. Otherwise the
    # first slash hides shared digits from later slashes and extraction order
    # silently decides which prose receives the fraction.
    proposals, owners = [], {}
    for line in lines:
        for span_index, span in enumerate(line.spans):
            if ('/' not in span.text or not span.char_boxes or
                    span.encoding_unresolved or span.transcription_uncertain):
                continue
            for start, end, x0, y0, x1, y1 in span.char_boxes:
                if span.text[start:end] != '/':
                    continue
                mid = (y0 + y1) / 2
                nearby = [part for part in parts if part is not line
                          and .5 <= part.size / line.size <= .8]
                upper = [part for part in nearby
                         if part.stripped.isdigit()
                         and abs(part.bbox[2] - x0) <= line.size * .18
                         and part.bbox[1] < y0 <= part.bbox[3]
                         and part.bbox[3] <= mid + line.size * .06]
                lower = [part for part in nearby
                         if abs(part.bbox[0] - x1) <= line.size * .18
                         and mid - line.size * .06 <= part.bbox[1] <= y1
                         and part.bbox[3] > y1]
                proposal = len(proposals)
                proposals.append((line, span_index, start, end, (x0,y0,x1,y1), upper, lower))
                for part in upper + lower:
                    owners.setdefault(id(part), set()).add(proposal)
    edits_by_line, consumed = {}, set()
    for proposal, (line, span_index, start, end, box, upper, lower) in enumerate(proposals):
        if len(upper) != 1 or len(lower) != 1 or upper[0] is lower[0]:
            continue
        num, den = upper[0], lower[0]
        if owners[id(num)] != {proposal} or owners[id(den)] != {proposal}:
            continue
        edits_by_line.setdefault(id(line), {}).setdefault(span_index, []).append(
            (start, end, num, den, box))
        consumed.update((id(num.spans[0]), id(den.spans[0])))
    replacements = {}
    for line in lines:
        edits = edits_by_line.get(id(line), {})
        if not edits:
            continue
        changed=[]
        for index, span in enumerate(line.spans):
            if id(span) in consumed:continue
            if index not in edits:
                changed.append(span)
                continue
            text=span.text
            boxes=list(span.char_boxes)
            for start,end,num,den,slash_box in sorted(edits[index],reverse=True):
                text=text[:start]+num.stripped+'/'+den.stripped+text[end:]
                added=len(num.stripped)+len(den.stripped)
                boxes=[(a+(added if a>=end else 0),b+(added if a>=end else 0),*box)
                       for a,b,*box in boxes if b<=start or a>=end]
                left=tuple(num.bbox);right=tuple(den.bbox)
                slash=(slash_box[0],slash_box[1],slash_box[2],slash_box[3])
                new=[left]*len(num.stripped)+[slash]+[right]*len(den.stripped)
                boxes.extend((start+j,start+j+1,*box) for j,box in enumerate(new))
            value=replace(span,text=text,char_boxes=tuple(sorted(boxes)))
            value.source_fraction_parts=tuple((num.stripped,den.stripped,
                tuple(num.bbox),tuple(slash_box),tuple(den.bbox))
                for _,_,num,den,slash_box in edits[index])
            changed.append(value)
        # A small denominator can cause MuPDF to flag the remaining baseline
        # as raised. Only the independently established slash baseline and full
        # body size can clear that flag; actual small raised spans stay raised.
        baselines=[line.spans[i]for i in edits]
        for span in changed:
            if any(abs(span.size-base.size)<.01 and span.origin_y and
                   abs(span.origin_y-base.origin_y)<.01 for base in baselines):
                span.flags &= ~1
        replacements[id(line)]=Line(changed,line.bbox,
                                    spacing_uncertain=line.spacing_uncertain,
                                    transcription_uncertain=line.transcription_uncertain)
    return [Block(block.number,block.bbox,
                  [replacements.get(id(line),Line([s for s in line.spans if id(s)not in consumed],
                     line.bbox,spacing_uncertain=line.spacing_uncertain,
                     transcription_uncertain=line.transcription_uncertain))
                   for line in block.lines if any(id(s)not in consumed for s in line.spans)],
                  block.kind)
            for block in blocks if block.kind != 'text' or
            any(id(s)not in consumed for line in block.lines for s in line.spans)]


def normalize_blocks(blocks):
    """Attach detached raised spans at measured character boundaries, then initials.

    Character boxes are the PDF's own positions; proportional string splitting
    and dictionary guesses are never used. Ambiguous placements stay unchanged.
    """
    from .extract import Line, Block
    blocks = _attach_stacked_fractions(blocks)
    lines = [ln for b in blocks for ln in b.lines]
    replacements, consumed, proposals = {}, set(), {}
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
                    later = [c for c in span.char_boxes if c[0] >= end
                             and span.text[c[0]:c[1]].strip()]
                    following = [s for s in line.spans[i+1:] if s.text.strip()]
                    next_x = later[0][2] if later else (following[0].bbox[0] if following else None)
                    if next_x is not None and next_x < small.bbox[2] - line.size*.15:
                        continue
                    if small.text in ('st','nd','rd','th') and not span.text[:end][-1:].isdigit():
                        continue
                    targets.append((line, i, end))
        if len(targets) != 1:
            continue
        target, index, offset = targets[0]
        proposals.setdefault(id(target), (target, []))[1].append((marker, index, offset))
    for key, (target, additions) in proposals.items():
        positions=[(index,offset) for _,index,offset in additions]
        if len(set(positions)) != len(positions):
            continue
        spans=[]
        for index, span in enumerate(target.spans):
            cursor=0
            for marker, _, offset in sorted((item for item in additions if item[1]==index), key=lambda item:item[2]):
                if offset>cursor:
                    spans.append(replace(span,text=span.text[cursor:offset],char_boxes=()))
                small=marker.spans[0]
                spans.append(replace(small,flags=small.flags | 1))
                cursor=offset
            if cursor<len(span.text):
                spans.append(replace(span,text=span.text[cursor:],char_boxes=()))
        replacements[key]=Line(spans,target.bbox)
        consumed.update(id(marker) for marker,_,_ in additions)
    out=[]
    for block in blocks:
        kept=[replacements[id(ln)] if replacements.get(id(ln)) else ln
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
            if (getattr(span, 'encoding_unresolved', False) or getattr(span,'transcription_uncertain',False)) and span.text.strip():
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
        runs.append(['glyph', text[lo:hi], descriptor(pno, span.bbox, span.size, span.font,reason='transcript' if getattr(span,'transcription_uncertain',False) else 'encoding')])
        cursor = hi
    runs.append(['t', text[cursor:]])
    return [run for run in runs if run[1]]


def note_descriptor(note):
    return descriptor(note.pno,note.bbox,0,'note',
                      reason='layout' if getattr(note,'source_layout',False) else 'encoding')


def glyph_html(record, block=False):
    """Printed evidence stays inline; the link explains unavailable text encoding."""
    from html import escape
    style='max-width:100%;height:auto' if block else 'height:1em;width:auto;vertical-align:baseline'
    label=('Original source text; transcription uncertain. Open original page.' if record.get('reason')=='transcript' else 'Original source text; Unicode encoding unavailable. Open original page.')
    if record.get('reason')=='layout':label='Original printed note layout. Open original page.'
    html = ('<a class="source-glyph" href="original-p%04d.xhtml#page" title="%s">'
            '<img src="%s" alt="%s" style="%s"/></a>' %
            (record['page'],escape(label,quote=True),image_name(record),escape(label,quote=True),style))
    return '<sup>'+html+'</sup>' if record.get('raised') else html


def package_glyphs(book,page_html,doc,package,should_stop=None):
    from . import extract
    from .source_display import SourceDisplay
    records={}
    for pno,elements in book.pages.items():
        if pno not in page_html:continue
        with unit_recovery.guard(pno, "native_glyph_collection"):
            for element_index, element in enumerate(elements):
                bid = "native-element-" + str(element_index)
                controller = unit_recovery.current()
                if controller and bid in controller.blocked(pno):continue
                with unit_recovery.guard(pno, "native_element_glyph_collection", block=bid):
                    for run in element.runs:
                        if run[0]=='glyph':records[image_name(run[2])]=dict(run[2], recovery_unit=bid, recovery_page=pno)
    note_indices = {}
    for note in book.notes:
        if note.pno not in page_html:continue
        index = note_indices.get(note.pno, 0);note_indices[note.pno] = index + 1
        bid = "native-note-" + str(index)
        controller = unit_recovery.current()
        if controller and bid in controller.blocked(note.pno):continue
        with unit_recovery.guard(note.pno, "native_note_glyph_collection", block=bid):
            if note.uses_source_image:
                if getattr(note, 'glyph_runs', None):
                    for run in note.glyph_runs:
                        if run[0] == 'glyph':
                            records[image_name(run[2])] = dict(run[2], recovery_unit=bid, recovery_page=note.pno)
                else:
                    record=note_descriptor(note);records[image_name(record)]=dict(record, recovery_unit=bid, recovery_page=note.pno)
    if records and doc is None:raise ValueError('Original PDF required for unmapped source glyphs')
    display=None
    display_key=None
    for name,record in sorted(records.items(),key=lambda item:item[1]['page']):
        if should_stop and should_stop():
            from .build_epub import BuildCancelled
            raise BuildCancelled('Source glyph rendering cancelled')
        controller = unit_recovery.current()
        physical_page = record["page"]
        collecting_page = record.get("recovery_page")
        collecting_unit = record.get("recovery_unit")
        units = None
        if controller:
            controller.check_units([dict(page=physical_page, block=None)])
            controller.check_units([dict(page=collecting_page, block=collecting_unit)])
            if physical_page != collecting_page:
                # Both owners come from the actual glyph descriptor/Book entry,
                # never an ordinal transplanted onto the other source page.
                units = [dict(page=physical_page, block=None),
                         dict(page=collecting_page, block=collecting_unit)]
            if physical_page in controller.source_pages():
                if collecting_page not in controller.source_pages():
                    with unit_recovery.guard(collecting_page, 'native_glyph_source_dependency', block=collecting_unit):
                        raise ValueError('Native fragment depends on glyph source retained as a whole original page')
                continue
        # A continued note may contain a glyph on another original page. Its
        # collecting ordinal cannot name a block on that physical source page.
        bid = record.get("recovery_unit") if record.get("recovery_page") == physical_page else None
        with unit_recovery.guard(physical_page, "native_glyph_image", block=bid, units=units):
            geometry = (getattr(book, 'source_inventory', {}).get(record['page'], {})
                        .get('source_geometry', {}))
            reading_layout = record.get('reason') == 'layout' and geometry.get('space') == 'reading'
            provenance = dict(geometry, layer='ocr') if reading_layout else None
            key = (record['page'], reading_layout)
            if display is None or display_key != key:
                display=SourceDisplay(doc,record['page'],provenance)
                display_key=key
            # Source-layout notes inherit the measured scan reading frame. Native
            # glyphs and encoding notes retain their unrotated PDF coordinates,
            # including when they share a page with a scan-layout note.
            rect=(extract.pymupdf.Rect(record['bbox']) if reading_layout
                  else display.reading_rect(record['bbox']))
            if rect.is_empty:raise ValueError('Source glyph crop is outside page')
            package.image(name,display.source_image(rect,scale=6,quality=95))
