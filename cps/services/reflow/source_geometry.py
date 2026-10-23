"""Corroborate source ownership using coherent, unique original word rows.

Accent folding is only a placement comparison. This module never changes the
chosen text or manufactures source words, glyphs, or confidence.
"""
from difflib import SequenceMatcher
import statistics
import unicodedata


def _key(value):
    value = unicodedata.normalize('NFKD', value.casefold())
    return ''.join(char for char in value
                   if not unicodedata.combining(char) and (char.isalnum() or char == '_'))


def unique_anchors(optical, available, candidate, *, minimum=5, complete_short=True):
    """Return a uniquely corroborated compact row window, or no proof.

    A distant common word cannot bridge unrelated entries. Adjacent printed
    rows may form one passage; a blank vertical interval or column gap starts
    another. Competing equally strong physical occurrences remain ambiguous.
    Returned indices identify original word instances, never interpolated boxes.
    """
    available = list(available)
    keys = [_key(token) for token in candidate]
    positions = {j for j, token in enumerate(keys) if token}
    if not available or not positions:
        return []
    source_keys = {i: _key(optical[i]['text']) for i in available}
    wanted = set(keys) - {''}
    height = max(1, statistics.median(
        optical[i]['bbox'][3] - optical[i]['bbox'][1] for i in available))
    rows = []
    center = lambda i: (optical[i]['bbox'][1] + optical[i]['bbox'][3]) / 2
    for index in sorted(available, key=lambda i: (center(i), optical[i]['bbox'][0])):
        row = next((row for row in reversed(rows)
                    if abs(row[0] - center(index)) <= height * .55), None)
        if row is None:
            rows.append([center(index), [index]])
        else:
            row[1].append(index)
            row[0] = statistics.median(center(i) for i in row[1])
    pieces = []
    for _, row in rows:
        part = []
        for index in sorted(row, key=lambda i: optical[i]['bbox'][0]):
            if part and optical[index]['bbox'][0] - optical[part[-1]]['bbox'][2] > height * 4:
                pieces.append(part)
                part = []
            part.append(index)
        if part:
            pieces.append(part)
    pieces.sort(key=lambda row: (min(optical[i]['bbox'][1] for i in row),
                                 optical[row[0]]['bbox'][0]))
    proofs = {}
    for start, first in enumerate(pieces):
        if not any(source_keys[i] in wanted for i in first):
            continue
        sequence = []
        last = first
        for part in pieces[start:]:
            left = min(optical[i]['bbox'][0] for i in part)
            right = max(optical[i]['bbox'][2] for i in part)
            if sequence:
                old_left = min(optical[i]['bbox'][0] for i in last)
                old_right = max(optical[i]['bbox'][2] for i in last)
                gap = min(optical[i]['bbox'][1] for i in part) - max(optical[i]['bbox'][3] for i in last)
                if gap < height * .25:
                    # A second column on the same row is a separate window.
                    continue
                if gap > height * 2.5 or left > old_right + height or right < old_left - height:
                    break
            sequence += part
            last = part
            if len(sequence) > max(20, len(keys) * 2):
                break
            if not any(source_keys[i] in wanted for i in part):
                continue
            matcher = SequenceMatcher(None, [source_keys[i] for i in sequence], keys,
                                      autojunk=False)
            anchors = [(sequence[i], j) for a, b, count in matcher.get_matching_blocks()
                       for i, j in zip(range(a, a + count), range(b, b + count)) if keys[j]]
            covered = {j for _, j in anchors}
            if len(covered) < min(minimum, len(positions)) or len(covered) / len(positions) < .6:
                continue
            # Complete short readings are required: common suffixes alone do
            # not locate an unfamiliar name or abbreviated heading.
            if complete_short and len(positions) <= 4 and covered != positions:
                continue
            identity = tuple(anchors)
            longest = max(count for _, _, count in matcher.get_matching_blocks())
            # Extra context in a larger window cannot defeat an equally exact
            # second occurrence. The physical anchor identity is the proof.
            source_start=min(sequence.index(i) for i,j in anchors)
            source_end=max(sequence.index(i) for i,j in anchors)
            extra=sum(bool(source_keys[i]) for i in sequence[source_start:source_end+1])-len({i for i,j in anchors})
            # Borrowing a common word across an unrelated entry adds physical
            # source words between shared anchors. Prefer the passage that
            # conserves the candidate's compact sequence. Identical repeated
            # passages still tie, irrespective of their hint or row wrapping.
            score = (len(covered), longest, -extra)
            if identity not in proofs or score > proofs[identity]:
                proofs[identity] = score
    if not proofs:
        return []
    best = max(proofs.values())
    winners = [identity for identity, score in proofs.items() if score == best]
    return list(winners[0]) if len(winners) == 1 else []


def local_note_openings(optical, available, marker, hint):
    """Return physical opening labels in the hint, excluding inline numerals."""
    import re
    label=lambda word:word['text'].strip().strip('().[] ')
    marker=marker.strip().strip('().[] ')
    if not re.fullmatch(r'\d{1,4}|[*†‡]',marker):return []
    available=list(available);result=[]
    height=max(1,statistics.median(optical[i]['bbox'][3]-optical[i]['bbox'][1]
                                  for i in available)) if available else 1
    center=lambda box:(box[1]+box[3])/2
    for index in available:
        box=optical[index]['bbox']
        if label(optical[index])!=marker or not (hint[0]-3<=(box[0]+box[2])/2<=hint[2]+3
                and hint[1]-3<=center(box)<=hint[3]+3):continue
        row=[optical[i]['bbox'] for i in available if i!=index
             and abs(center(optical[i]['bbox'])-center(box))<=height*.75]
        following=[b for b in row if 0<=b[0]-box[2]<=height*4]
        preceding=[b for b in row if 0<=box[0]-b[2]<=height*4]
        if following and not preceding:result.append(index)
    return result


def labelled_note_anchors(optical, available, candidate, marker, *, openings=None):
    """Locate repeated note wording only through its own printed opening label.

    A hint's proximity cannot choose between repeated citations. A literal
    source label must stand left of the first printed row, and the chosen
    passage must start on that row. The next aligned printed label bounds the
    available continuation. Unreadable or competing labels prove no placement.
    """
    import re
    available = list(available)
    marker = marker.strip().strip('().[] ')
    if not re.fullmatch(r'\d{1,4}|[*†‡]', marker) or not available:
        return None
    label_text = lambda word: word['text'].strip().strip('().[] ')
    sizes = [optical[i]['bbox'][3] - optical[i]['bbox'][1] for i in available]
    height = max(1, statistics.median(sizes))
    center = lambda box: (box[1] + box[3]) / 2
    proofs = []
    for index in available:
        if openings is not None and index not in openings:continue
        label = optical[index]
        if label_text(label) != marker:
            continue
        box = label['bbox']
        # Printed labels occupy the opening margin. The very next physical
        # word must be adjacent and share the original baseline.
        row = [i for i in available if i != index and
               abs(center(optical[i]['bbox']) - center(box)) <= height * .75 and
               optical[i]['bbox'][0] >= box[2]]
        row.sort(key=lambda i: optical[i]['bbox'][0])
        if not row or row[0] == index:
            continue
        if not 0 <= optical[row[0]]['bbox'][0] - box[2] <= height * 4:
            continue
        right = optical[row[0]]['bbox'][2]
        for previous, current in zip(row, row[1:]):
            if optical[current]['bbox'][0] - optical[previous]['bbox'][2] > height * 4:
                break
            right = optical[current]['bbox'][2]
        # Other units can own a label already; its physical boundary still
        # limits this note. Ownership filters which words may be claimed, not
        # which original source markers exist.
        endings = [center(optical[i]['bbox']) for i in range(len(optical)) if i != index and
                   re.fullmatch(r'\d{1,4}|[*†‡]', label_text(optical[i])) and
                   abs(optical[i]['bbox'][0] - box[0]) <= height and
                   center(optical[i]['bbox']) > center(box) + height * .8]
        bottom = min(endings, default=float('inf')) - height * .5
        pool = [i for i in available if i != index and
                center(box) - height * .75 <= center(optical[i]['bbox']) < bottom and
                optical[i]['bbox'][0] >= (box[2] if
                    abs(center(optical[i]['bbox'])-center(box))<=height*.75 else box[0]) and
                optical[i]['bbox'][2] <= right + height * 4]
        anchors = unique_anchors(optical, pool, candidate, minimum=4)
        placement_only = False
        mismatched = []
        if not anchors and 3 <= len(candidate) <= 5:
            # Short damaged citations can have fewer lexical anchors than a
            # paragraph. Their complete labelled physical row may locate the
            # slots, while mismatched spellings still require a pixel read.
            opening = [i for i in row if optical[i]['bbox'][2] <= right]
            preceding = [i for i in available if i != index and
                abs(center(optical[i]['bbox']) - center(box)) <= height * .75 and
                0 <= box[0] - optical[i]['bbox'][2] <= height * 4]
            matches = [j for j, i in enumerate(opening)
                       if j < len(candidate) and _key(optical[i]['text']) == _key(candidate[j])]
            distinctive = any(any(c.isdigit() for c in candidate[j]) or
                              len(_key(candidate[j])) >= 6 for j in matches)
            if (not preceding and len(opening) == len(candidate) and
                    len(matches) >= 2 and len(matches) / len(candidate) >= .6 and distinctive):
                anchors = list(zip(opening, range(len(candidate))))
                placement_only = True
                mismatched = [j for j in range(len(candidate)) if j not in matches]
        if not anchors:
            continue
        first = min(anchors, key=lambda pair: pair[1])
        if first[1] > 2 or abs(center(optical[first[0]]['bbox']) - center(box)) > height * .75:
            continue
        proofs.append(dict(anchors=anchors, marker_bbox=list(box),
                           first_word_bbox=list(optical[row[0]]['bbox']),
                           placement_only=placement_only, mismatched_positions=mismatched))
    return proofs[0] if len(proofs) == 1 else None


def native_pixel_anchors(native, optical, available, candidate):
    """Locate a complete native sequence on independently visible pixel ink.

    This is placement evidence only. Native spellings never certify chosen
    words. A literal divided token can establish two original ink occurrences;
    a short reading with one interior mismatch needs agreement of both source
    views on every source word. Neither path trusts the supplied rectangle.
    """
    if not native or not optical or not 3<=len(candidate)<=40:return []
    available=list(available)
    # Font metric boxes can overlap adjacent baselines. Coherent-row testing
    # uses their central band; returned ownership still uses original ink.
    geometric=[]
    for i,w in enumerate(native):
        a=w['bbox'];h=max(1,a[3]-a[1]);cy=(a[1]+a[3])/2
        geometric.append(dict(w,bbox=[a[0],cy-h*.3,a[2],cy+h*.3],source_indices=[i]))
    base=unique_anchors(geometric,range(len(native)),candidate,
                        minimum=3,complete_short=False)
    covered={j for _,j in base}
    if len(covered)<3:return []
    height=max(1,statistics.median(w['bbox'][3]-w['bbox'][1] for w in native))
    boxes=[native[i]['bbox'] for i,j in base]
    envelope=[min(b[0] for b in boxes),min(b[1] for b in boxes),
              max(b[2] for b in boxes),max(b[3] for b in boxes)]
    pool=[i for i,w in enumerate(native) if w['bbox'][0]>=envelope[0]-height*2
          and w['bbox'][0]<=envelope[2]+height*3
          and w['bbox'][1]>=envelope[1]-height
          and w['bbox'][3]<=envelope[3]+height*2.5]
    units=[dict(w) for w in geometric]
    # An internal divided word is still one candidate token. Keep both source
    # instances, and require its second atom to open the next physical row.
    for i in pool:
        a=native[i];box=a['bbox']
        if not a['text'].rstrip(',;:').endswith(('-', '‐', '–')):continue
        next_row=[k for k in pool if height*.3<native[k]['bbox'][1]-box[1]<=height*2]
        if not next_row:continue
        top=min(native[k]['bbox'][1] for k in next_row)
        row=[k for k in next_row if abs(native[k]['bbox'][1]-top)<height*.5]
        k=min(row,key=lambda k:native[k]['bbox'][0])
        joined=a['text'].rstrip(',;:').rstrip('-‐–')+native[k]['text']
        if _key(joined) not in {_key(t) for t in candidate}:continue
        units[i]=dict(geometric[i],text=joined,source_indices=[i,k])
    proof=unique_anchors(units,pool,candidate)
    variant=False
    if {j for _,j in proof}!=set(range(len(candidate))):
        # A differing interior word cannot locate itself. Three exact anchors
        # and complete native/pixel agreement may locate its physical slot.
        missing=set(range(len(candidate)))-covered
        if len(candidate)>4 or len(missing)!=1:return []
        j=next(iter(missing))
        if j in (0,len(candidate)-1):return []
        variants=[]
        for i in pool:
            if len(_key(native[i]['text']))<6:continue
            alternate=list(candidate);alternate[j]=native[i]['text']
            found=unique_anchors(geometric,range(len(native)),alternate)
            if {n for _,n in found}==set(range(len(candidate))):variants.append(found)
        identities={tuple(found) for found in variants}
        if len(identities)!=1:return []
        proof=list(next(iter(identities)));variant=True
        units=[dict(w) for w in geometric]
    result=[];exact=set();claimed={}
    for index,j in proof:
        for source_index in units[index]['source_indices']:
            a=native[source_index];box=a['bbox'];matches=[]
            for i in available:
                b=optical[i]['bbox'];h=max(1,box[3]-box[1],b[3]-b[1])
                overlap=min(box[2],b[2])-max(box[0],b[0])
                if abs((box[1]+box[3]-b[1]-b[3])/2)>h*.65:continue
                if overlap<min(box[2]-box[0],b[2]-b[0])*.5:continue
                matches.append(i)
            if not matches:return []
            same=[i for i in matches if _key(optical[i]['text'])==_key(a['text'])]
            if variant and len(same)!=1:return []
            if same:exact.add(j)
            for i in matches:
                if i in claimed and claimed[i]!=j:return []
                claimed[i]=j;result.append((i,j))
    if len(exact)<3:return []
    return sorted(set(result),key=lambda pair:(pair[1],optical[pair[0]]['bbox'][1],optical[pair[0]]['bbox'][0]))
