"""Explicit vision conversion lane; deterministic native pages retain their path.

No book IDs or page-specific furniture rules enter this implementation. Reading
coordinates use the same upright SourceDisplay for alignment and evidence crops.
"""
import copy
import hashlib
import json
import re
import uuid
import unicodedata
from datetime import datetime, timezone
from html import escape, unescape
from pathlib import Path
from . import assemble, build_epub, extract, pipeline, reading_flow, vision_reading
from .source_display import SourceDisplay
from . import unit_recovery, text_quality, visible_scripts, local_reading


_MARKER_GROUP_AMBIGUOUS = 'multiple_original_glyphs_for_one_placeholder'
_MARKER_GROUP_INCOMPLETE = 'complete_original_marker_group_not_proved'
_MARKER_SLOT_UNBOUND = 'original_marker_slot_not_uniquely_bound'
_MARKER_ROLE_UNPROVED = 'numeric_original_marker_role_unproved'


def clean_native(page, raw=None):
    """A painted, Unicode-readable native layer with no page-sized scan carrier."""
    if not page.get_bboxlog():
        return True
    if not page.get_text('text').strip():
        return False
    if any(kind == 'ignore-text' for kind, *_ in page.get_bboxlog()):
        return False
    area=page.rect.get_area()
    if any(extract.pymupdf.Rect(i['bbox']).get_area() >= area*.6 for i in page.get_image_info()):
        return False
    text=page.get_text('text')
    if '\ufffd' in text or any(ord(c)<32 and c not in '\n\r\t' for c in text):
        return False
    if text_quality.inspect(text)['needs_read']:
        return False
    raw=raw or extract.read_page(page.parent,page.number)
    return not any(getattr(s,k,False) for b in raw.text_blocks for l in b.lines for s in l.spans
                   for k in ('encoding_unresolved','transcription_uncertain','uncertain'))


def _native_wrap_vocabulary(doc, native):
    """Use physical words from selected clean-native pages as wrap evidence.

    Cached Book vocabularies have no per-page provenance. Neither those fields
    nor OCR/model text can earn deletion or retention of a printed hyphen.
    Whole physical source compounds take precedence over a whole closed word.
    """
    words=set()
    compounds=set()
    for n in native:
        with unit_recovery.guard(n,'native_seam_vocabulary'):
            for record in doc[n].get_text('words'):
                token=record[4]
                # Delimiters can surround a physical word. A printed trailing dash
                # is incomplete evidence, and lexical apostrophes remain part of it.
                def delimiter(char):
                    category=unicodedata.category(char)
                    return (category.startswith('P') and category!='Pd'
                            and char not in "'’" and char not in assemble.HYPHENS)
                while token and delimiter(token[0]):token=token[1:]
                while token and delimiter(token[-1]):token=token[:-1]
                if re.fullmatch(r"[A-Za-z][A-Za-z'’]*",token):
                    words.add(token.casefold())
                elif re.fullmatch(r"[A-Za-z][A-Za-z'’]*-[A-Za-z][A-Za-z'’]*",token):
                    compounds.add(token.casefold())
    return words,compounds


def block_rect(block,display):
    x=block['bbox'];r=display.rect
    return [r.width*x[0]/1000,r.height*x[1]/1000,r.width*x[2]/1000,r.height*x[3]/1000]


def layer_words(doc,pno,display):
    """Use the PDF's OWN layer; never substitute recognition or model words."""
    result=[]
    for word in doc[pno].get_text('words',sort=True):
        rect=display.reading_rect(word[:4])
        if not rect.is_empty:
            result.append({'text':word[4],'bbox':list(rect),'line':(word[5],word[6]),'ordinal':word[7]})
    # Columns are already separated by each model block's rectangle.
    return sorted(result,key=lambda w:(round(w['bbox'][1]/3),w['bbox'][0]))


def owned_words(words,box):
    # Center admission tolerates source-region and model bbox rounding.
    return [w for w in words if box[0]-3 <= (w['bbox'][0]+w['bbox'][2])/2 <= box[2]+3
            and box[1]-3 <= (w['bbox'][1]+w['bbox'][3])/2 <= box[3]+3]


def _pixel_lines(words):
    """Group ink within one source territory, independent of OCR block order.

    Italic fragments can be returned after later lines by the recognizer. Their
    Physical vertical centers put them beside words on the same line, including
    short tokens whose tall ink boxes cross their neighbors' bottom baseline.
    This changes guard evidence order, never model/source-layer certainty.
    """
    import statistics
    if not words:return []
    # The recognizer sees curved printed baselines that horizontal center
    # sorting cannot represent. Retain its line instances only when their
    # original word boxes form coherent, moderately sloped physical rows.
    labelled={};valid=True
    for word in words:
        key=word.get('optical_line')
        if (not isinstance(key,(list,tuple)) or len(key)!=5
                or any(type(v)is not int for v in key) or key[0]<0 or min(key[1:])<=0):
            valid=False;break
        labelled.setdefault(tuple(key),[]).append(word)
    if valid:
        rows=[]
        for key,row in sorted(labelled.items()):
            row=sorted(row,key=lambda w:w['bbox'][0])
            height=statistics.median(w['bbox'][3]-w['bbox'][1]for w in row)
            centers=[((w['bbox'][0]+w['bbox'][2])/2,(w['bbox'][1]+w['bbox'][3])/2)for w in row]
            width=centers[-1][0]-centers[0][0]
            slope=(centers[-1][1]-centers[0][1])/width if width>0 else 0
            intercept=statistics.median(y-slope*x for x,y in centers)
            if abs(slope)>.15 or any(abs(y-slope*x-intercept)>max(2,height*.8)for x,y in centers):
                valid=False;break
            rows.append(row)
        if valid:
            # OCR may put a narrow number column in a separate block. Two
            # labelled groups sharing one adjacent physical baseline are one
            # row, not a later continuation after the paragraph's last word.
            for i,left in enumerate(rows):
                for right in rows[i+1:]:
                    height=statistics.median(w['bbox'][3]-w['bbox'][1]for w in left+right)
                    a=vision_reading.union([w['bbox']for w in left])
                    b=vision_reading.union([w['bbox']for w in right])
                    if (abs((a[1]+a[3]-b[1]-b[3])/2)<=max(2,height*.55)
                            and max(a[0],b[0])-min(a[2],b[2])<=2*height):
                        valid=False;break
                if not valid:break
        if valid:return rows
    tolerance=max(2,statistics.median(w['bbox'][3]-w['bbox'][1] for w in words)*.55)
    lines=[]
    center=lambda w:(w['bbox'][1]+w['bbox'][3])/2
    for word in sorted(words,key=lambda w:(center(w),w['bbox'][0])):
        middle=center(word)
        row=next((row for row in reversed(lines) if abs(row[0]-middle)<=tolerance),None)
        if row is None:lines.append([middle,[word]])
        else:
            row[1].append(word)
            row[0]=statistics.median(center(w) for w in row[1])
    return [sorted(row,key=lambda w:w['bbox'][0]) for _,row in lines]


def _pixel_line_order(words):
    return [word for row in _pixel_lines(words) for word in row]


def _source_instance(word):
    return (word['text'],tuple(word['bbox']))


def _verify_note_glyph(display,box,reader,receipts,pno,kind):
    """Read only an independently located original glyph, never its context."""
    if reader is None:return None
    crop=list(box);crop[0]-=1;crop[1]-=1;crop[2]+=1;crop[3]+=1
    crop=list(extract.pymupdf.Rect(crop)&display.rect)
    receipt=reader(display.roi_jpeg(crop,scale=12,quality=95))
    receipts.setdefault(pno,[]).append(dict(receipt,source_bbox=crop,kind=kind,
        original_frame=dict(display.provenance.get('original_frame') or {})))
    return receipt.get('label') if not receipt.get('uncertain') else None


def _source_note_boundaries(answer,optical,display,regions,body_owners,reader,receipts,pno):
    """Partition an incorrectly merged note at its own printed interior label.

    The unlabelled head can continue an unfinished note in the other column.
    Preserve existing tokens and their confidence positions; a model label or
    an OCR plus alone cannot establish the physical partition.
    """
    import statistics
    from . import vision_pixels
    lexical=lambda t:re.sub(r'[^\w]','',vision_reading.normalized(t).casefold())
    repairs=[];blocks=answer['blocks'];partition_territories={};partition_instances={}
    for at,note in enumerate(blocks):
        if note['role']!='footnote' or not note['marker'] or not at:continue
        previous=next((b for b in reversed(blocks[:at])
            if b['role'] not in ('running_head','folio')),None)
        if previous is None or previous['role']!='footnote':continue
        if not assemble.continues(previous['text'],note['text']):continue
        tokens=note['text'].split();prior=previous['text'].split()
        if any(t.startswith('<n:') for t in tokens+prior):continue
        owned=_owned_block_words(note,answer,optical,display,regions,body_owners,ordered=True)
        earlier=_owned_block_words(previous,answer,optical,display,regions,body_owners,ordered=True)
        if not owned or not earlier:continue
        matches=_geometry_anchors(owned,list(range(len(owned))),[lexical(t) for t in tokens])
        positions={j:owned[i] for i,j in matches}
        if 0 not in positions:continue
        _,tail=_published_pixel_edges(prior,earlier)
        if tail is None:continue
        height=max(1,statistics.median(w['bbox'][3]-w['bbox'][1] for w in owned))
        head=positions[0]['bbox'];last=tail['bbox']
        if (head[1]<display.rect.height*.6 or last[1]<display.rect.height*.6 or
            not (head[0]>max(w['bbox'][2] for w in earlier)+3 or
                 head[2]<min(w['bbox'][0] for w in earlier)-3) or
            head[1]>=last[1]-height):continue
        label=re.sub(r'^[\s([.]+|[\s).\]]+$','',note['marker'])
        territory=vision_reading.union(regions.get(note['id'],[block_rect(note,display)]))
        glyphs=[w for w in owned_words(optical,territory)
            if re.fullmatch(r'[([]?(?:\d{1,4}|[*†‡+])[.)\]]?',w['text'])
            and regions.get('_instances',{}).get(_source_instance(w),note['id'])==note['id']]
        def before(glyph,word):
            a,b=glyph['bbox'],word['bbox']
            return (abs((a[1]+a[3]-b[1]-b[3])/2)<=height*.7
                    and 0<=b[0]-a[2]<=3*height)
        if any(before(w,positions[0]) for w in glyphs):continue
        candidates=[]
        for glyph in glyphs:
            actual=re.sub(r'^[\s([.]+|[\s).\]]+$','',glyph['text'])
            if actual!=label and not (actual=='+' and label=='†'):continue
            next_positions=[j for j,w in positions.items() if j>0 and before(glyph,w)]
            if next_positions:candidates.append((min(next_positions),glyph,actual))
        if len(candidates)!=1:continue
        split,glyph,actual=candidates[0]
        if split-1 not in positions or len([j for j in positions if j<split])/split<.85:continue
        prefix=[positions[j] for j in sorted(positions) if j<split]
        rows=_pixel_lines(prefix)
        if len(rows)>1 and min(w['bbox'][0] for w in rows[0])>statistics.median(
                min(w['bbox'][0] for w in row) for row in rows[1:4])+3:continue
        if (glyph['bbox'][1]+glyph['bbox'][3])/2<(
                positions[split-1]['bbox'][1]+positions[split-1]['bbox'][3])/2+height*.25:continue
        if actual!=label:
            ink=vision_pixels.ink_bounds(display,glyph['bbox'])
            if not ink or _verify_note_glyph(display,ink,reader,receipts,pno,'interior_note_boundary')!=label:continue
        # The partition was proved in two physical columns. Retain that
        # ordered evidence through rebinding rather than replacing it with a
        # union that permits row-interleaved optical publication.
        bottom=max((w['bbox'][1]+w['bbox'][3])/2 for w in prefix)
        printed_prefix=[w for w in owned if (w['bbox'][1]+w['bbox'][3])/2<=bottom+height*.25]
        partition_territories[previous['id']]=list(regions.get(previous['id'],[block_rect(previous,display)]))+[
            vision_reading.union([w['bbox'] for w in row]) for row in _pixel_lines(printed_prefix)]
        partition_instances[previous['id']]=earlier+printed_prefix
        previous['text']=' '.join(prior+tokens[:split])
        previous['low_confidence']+= [len(prior)+i for i in note['low_confidence'] if i<split]
        note['text']=' '.join(tokens[split:])
        note['low_confidence']=[i-split for i in note['low_confidence'] if i>=split]
        bound=vision_reading.union([block_rect(previous,display)]+[w['bbox'] for w in prefix])
        r=display.rect
        previous['bbox']=[bound[0]/r.width*1000,bound[1]/r.height*1000,
                          bound[2]/r.width*1000,bound[3]/r.height*1000]
        repairs.append(dict(method='source_interior_note_partition',previous=previous['id'],
            block=note['id'],moved_token_count=split,source_marker_bbox=glyph['bbox']))
    if repairs:
        fresh={};owners={}
        answer,rebinding=bind_geometry(answer,optical,display,fresh,owners)
        for bid,territories in partition_territories.items():
            fresh[bid]=territories
            for word in partition_instances[bid]:fresh.setdefault('_instances',{})[_source_instance(word)]=bid
        regions.clear();regions.update(fresh);body_owners.clear();body_owners.update(owners)
        repairs.extend(rebinding)
    return answer,repairs


def _note_pixel_anchors(optical,available,candidate,hint,*,exact_complete=False):
    """Corroborate a complete note in physical row or column reading order.

    A coarse hint can omit an internal row edge or the top of the next column.
    Compare exact word instances in nearby ink; never concatenate rectangles
    across two independently authored notes or infer their transcription.
    """
    import statistics
    local=[i for i in available if owned_words([optical[i]],hint)]
    sizes=[optical[i]['bbox'][3]-optical[i]['bbox'][1] for i in local or available]
    if not sizes:return []
    height=max(1,statistics.median(sizes))
    pool=[i for i in available if hint[1]-8*height<=optical[i]['bbox'][1]
          and optical[i]['bbox'][3]<=hint[3]+8*height]
    lookup={id(optical[i]):i for i in pool};columns=[]
    pieces=[]
    for row in _pixel_lines([optical[i] for i in pool]):
        part=[]
        for word in row:
            if part and word['bbox'][0]-part[-1]['bbox'][2]>4*height:
                pieces.append(part);part=[]
            part.append(word)
        if part:pieces.append(part)
    for part in sorted(pieces,key=lambda p:p[0]['bbox'][0]):
        bounds=vision_reading.union([w['bbox'] for w in part])
        column=next((c for c in columns if bounds[0]<c['right']+3
                     and bounds[2]>c['left']-3),None)
        if column is None:
            column=dict(left=bounds[0],right=bounds[2],pieces=[]);columns.append(column)
        column['left']=min(column['left'],bounds[0]);column['right']=max(column['right'],bounds[2])
        column['pieces'].append(part)
    columns.sort(key=lambda c:c['left'])
    orders=[local,pool,[lookup[id(w)] for w in _pixel_line_order([optical[i] for i in pool])]]
    for sequence in (columns,list(reversed(columns))):
        orders.append([lookup[id(w)] for c in sequence
            for part in sorted(c['pieces'],key=lambda p:p[0]['bbox'][1]) for w in part])
    # A real gutter can be narrower than four character heights. Admit an
    # additional column hypothesis only along a persistent empty vertical
    # territory, then let exact source correspondence choose it over row order.
    def gutter_orders(indices):
        rows=_pixel_lines([optical[i] for i in indices])
        gaps=[(left['bbox'][2],right['bbox'][0],row_index)
              for row_index,row in enumerate(rows) for left,right in zip(row,row[1:])
              if right['bbox'][0]-left['bbox'][2]>=height*.75]
        splits=set()
        for left,right,_ in gaps:
            middle=(left+right)/2
            if len({row for a,b,row in gaps if a<=middle<=b})<2:continue
            if any(optical[i]['bbox'][0]<middle<optical[i]['bbox'][2] for i in indices):continue
            splits.add(middle)
        for middle in sorted(splits):
            sides=[[optical[i] for i in indices if (optical[i]['bbox'][0]+optical[i]['bbox'][2])/2<middle],
                   [optical[i] for i in indices if (optical[i]['bbox'][0]+optical[i]['bbox'][2])/2>=middle]]
            for sequence in (sides,list(reversed(sides))):
                yield [lookup[id(w)] for side in sequence for w in _pixel_line_order(side)]
    orders.extend(gutter_orders(pool))
    exact_orders=list(orders)
    matches=[_geometry_anchors(optical,order,candidate) for order in orders]
    score=lambda pairs:(len({j for _,j in pairs}),sum(i in local for i,j in pairs))
    best=max(matches,key=score,default=[])
    # The nearby pool can contain a caption above a footer that legitimately
    # crosses its gutter. Establish the note's row band from corroborated word
    # instances before testing that gutter; unrelated rows cannot veto it.
    # Keep every source word within the band, including unmatched continuations.
    if best and len({j for _,j in best})>=min(5,len(candidate)):
        anchored=[optical[i] for i,j in best]
        top=min(w['bbox'][1] for w in anchored)-height*.5
        bottom=max(hint[3],max(w['bbox'][3] for w in anchored)+height*.5)
        band=[i for i in pool if top<=(optical[i]['bbox'][1]+optical[i]['bbox'][3])/2<=bottom]
        band_orders=list(gutter_orders(band))
        exact_orders.extend(band_orders)
        matches.extend(_geometry_anchors(optical,order,candidate) for order in band_orders)
    if exact_complete:
        # A complete literal occurrence inside the supplied territory may span
        # a proved persistent gutter. Enumerate identities rather than letting
        # one partial row window win merely because it is compact.
        key=lambda text:re.sub(r'[^\w]','',vision_reading.normalized(text).casefold())
        proofs={}
        if len(candidate)<5:return []
        for order in exact_orders:
            sequence=[i for i in order if key(optical[i]['text'])]
            keys=[key(optical[i]['text'])for i in sequence]
            for at in range(len(keys)-len(candidate)+1):
                if keys[at:at+len(candidate)]==candidate:
                    indices=tuple(sequence[at:at+len(candidate)])
                    if len(set(indices)&set(local))>=min(3,len(candidate)):
                        proofs[indices]=list(zip(indices,range(len(candidate))))
        return next(iter(proofs.values())) if len(proofs)==1 else []
    return max(matches,key=score,default=[])


def _reserve_object_instances(answer,optical,display,regions):
    """Exact text instances outrank a broad paragraph or illustration hint."""
    from . import vision_pixels
    instances=regions.setdefault('_instances',{})
    keys=lambda text:[re.sub(r'[^\w]','',vision_reading.normalized(t).casefold())
                      for t in re.sub(r'<n:[^>]+>',' ',text).split()]
    for block in answer['blocks']:
        if block['role']!='body':continue
        local=_pixel_line_order([w for w in optical if any(owned_words([w],box)
            for box in regions.get(block['id'],[block_rect(block,display)]))])
        candidate=keys(block['text'])
        matches=_geometry_anchors(local,list(range(len(local))),candidate,body=True)
        if len({j for _,j in matches})>=min(5,len(candidate)) and len(matches)/max(1,len(candidate))>=.6:
            for i,j in matches:instances.setdefault(_source_instance(local[i]),block['id'])
    # Exact text equality alone is not source-region ownership. Independently
    # bound prose rows fence the original graphic components before a remote
    # object description can claim an omitted paragraph's matching words.
    prose_rows=[]
    for block in answer['blocks']:
        if block['role'] in ('figure','table'):continue
        local=[w for w in optical if instances.get(_source_instance(w))==block['id']]
        if not local:
            local=_pixel_line_order([w for w in optical if any(owned_words([w],box)
                for box in regions.get(block['id'],[block_rect(block,display)]))])
            matches=_geometry_anchors(local,list(range(len(local))),keys(block['text']))
            local=[local[i] for i,j in matches]
        prose_rows.extend(vision_reading.union([w['bbox'] for w in row]) for row in _pixel_lines(local))
    for role in ('caption','figure','table'):
        for block in answer['blocks']:
            if block['role']!=role:continue
            candidate=keys(block['text'])
            available=[i for i,w in enumerate(optical)
                       if instances.get(_source_instance(w),block['id'])==block['id']]
            if role=='caption':
                # A damaged caption is still attached to its printed source
                # territory. Equal words elsewhere do not establish ownership.
                available=[i for i in available if any(owned_words([optical[i]],box)
                    for box in regions.get(block['id'],[block_rect(block,display)]))]
            if role in ('figure','table'):
                seed=block_rect(block,display)
                extent=vision_pixels.figure_ink_bounds(display,list(display.rect),seed,prose_rows=prose_rows)
                if extent is None:continue
                available=[i for i in available if owned_words([optical[i]],extent)]
            local=[i for i in available if any(owned_words([optical[i]],box)
                for box in regions.get(block['id'],[block_rect(block,display)]))]
            matches=_geometry_anchors(optical,local,candidate)
            if role=='caption':
                punctuated=_caption_punctuation_anchors(optical,local,block['text'])
                if len({j for i,j in punctuated})>len({j for i,j in matches}):matches=punctuated
            if len({j for _,j in matches})/max(1,len(candidate))<.6:
                ordered=_pixel_line_order([optical[i] for i in available])
                lookup={id(optical[i]):i for i in available}
                matches=_geometry_anchors(optical,[lookup[id(w)] for w in ordered],candidate)
            if len({j for _,j in matches})<min(5,len(candidate)) or len(matches)/max(1,len(candidate))<.6:continue
            for i,j in matches:instances.setdefault(_source_instance(optical[i]),block['id'])


def _reserve_layer_instances(answer,words,display,regions):
    """Rebind unique lexical occurrences to this inventory's physical identities.

    A dotted leader and its distant page number do not form prose anchors.
    Locate the lexical reading first, then bind the original punctuation and
    numeric tail only on those same printed rows. This grants ownership only.
    """
    from .source_geometry import unique_anchors
    key=lambda text:re.sub(r'[^\w]','',vision_reading.normalized(text).casefold())
    instances=regions.setdefault('_instances',{})
    # Recognition inventories can divide the same printed word differently.
    # Transfer only an already proved physical owner, never its transcription.
    # Competing owners or a word spanning two owners cannot establish a claim.
    prior=list(instances.items())
    for word in words:
        occurrence=_source_instance(word)
        if occurrence in instances:continue
        x=word['bbox'];cx=(x[0]+x[2])/2;cy=(x[1]+x[3])/2
        owners=set()
        for (_,box),owner in prior:
            height=min(x[3]-x[1],box[3]-box[1])
            if height<=0:continue
            overlap_x=max(0,min(x[2],box[2])-max(x[0],box[0]))
            overlap_y=max(0,min(x[3],box[3])-max(x[1],box[1]))
            if (overlap_x>=.5*min(x[2]-x[0],box[2]-box[0])
                    and overlap_y>=.6*height
                    and abs(cy-(box[1]+box[3])/2)<=.5*max(x[3]-x[1],box[3]-box[1])
                    and (box[0]-.5<=cx<=box[2]+.5 or x[0]<= (box[0]+box[2])/2 <=x[2])):
                owners.add(owner)
        if len(owners)==1:instances[occurrence]=next(iter(owners))
        elif len(owners)>1:
            # A merged OCR column can cover several separately owned labels.
            # It cannot be printed inline as another word in any of those rows;
            # publication conservation retains its complete original context.
            regions.setdefault('_ambiguous_instances',set()).add(occurrence)
    # Short numbered rows can have a vertically clipped reader hint. Before a
    # crop reread can replace it with the preceding row, locate the complete
    # lexical row in a bounded neighbourhood. This moves geometry only when
    # exactly one physical row proves every lexical anchor; repeated rows and
    # already owned neighbouring occurrences cannot establish a new placement.
    for block in answer['blocks']:
        tokens=block['text'].split()
        lexical=[key(t)for t in tokens if any(c.isalpha()for c in key(t))]
        boxes=regions.get(block['id'],[block_rect(block,display)])
        if (block['role']!='body' or not re.match(r'^\d{1,3}[.)]\s',block['text'])
                or not 2<=len(lexical)<=4 or len(tokens)>6 or len(boxes)!=1):continue
        box=boxes[0];pad=2*max(1,box[3]-box[1])
        search=[box[0],max(display.rect.y0,box[1]-pad),box[2],min(display.rect.y1,box[3]+pad)]
        candidates=[]
        for row in _pixel_lines([w for w in words if owned_words([w],search)]):
            matches=unique_anchors(row,range(len(row)),lexical,minimum=2)
            if {j for i,j in matches}!=set(range(len(lexical))):continue
            own=[row[i]for i,j in matches]
            if any(instances.get(_source_instance(w),block['id'])!=block['id']for w in own):continue
            candidates.append(own)
        if len(candidates)!=1:continue
        bounds=vision_reading.union([w['bbox']for w in candidates[0]])
        # Preserve space for this row's adjacent printed number. Its spelling
        # still has to pass the ordinary native/pixel identity checks.
        regions[block['id']]=[[min(box[0],bounds[0]),bounds[1]-.01,
                              max(box[2],bounds[2]),bounds[3]+.01]]
        for word in candidates[0]:instances[_source_instance(word)]=block['id']
    proposals={};proved_rows={};proved_row_words={}
    # A compact index entry may have only one ordinary lexical word plus an
    # ordinal and page number. Its complete printed row still proves ownership
    # against a larger neighbor that borrowed that same row as a tail.
    for block in answer['blocks']:
        if block['role'] not in ('body','other','caption','heading'):continue
        candidate=[key(token)for token in block['text'].split()]
        if (not 3<=len(candidate)<=6 or not all(candidate) or
                not any(token.isdigit()for token in candidate)):continue
        boxes=regions.get(block['id'],[block_rect(block,display)])
        nearby=[w for w in words if any(owned_words([w],box)for box in boxes)]
        if not nearby:continue
        import statistics
        pad=3*statistics.median(w['bbox'][3]-w['bbox'][1]for w in nearby)
        search=[[box[0],box[1],min(display.rect.x1,box[2]+pad),box[3]]for box in boxes]
        local=[w for w in words if any(owned_words([w],box)for box in search)]
        rows=[]
        for row in _pixel_lines(local):
            # An index's numeric tail cannot borrow the next lexical column.
            numeric=False;bounded=[]
            for word in row:
                token=key(word['text'])
                reference=any(c.isdigit()for c in token)or bool(re.fullmatch(r'[ivxlcdm]+',token,re.I))
                if numeric and any(c.isalpha()for c in token)and not reference:break
                bounded.append(word)
                numeric|=reference
            row=bounded
            if not row or key(row[0]['text'])!=candidate[0]:continue
            # This is one measured index row inside the unit's original
            # territory. Its tab gap is intentional; the generic prose anchor
            # finder would incorrectly split label and numeric columns here.
            from difflib import SequenceMatcher
            matcher=SequenceMatcher(None,[key(w['text'])for w in row],candidate,autojunk=False)
            anchors=[(i,j)for a,b,count in matcher.get_matching_blocks()
                     for i,j in zip(range(a,a+count),range(b,b+count))]
            covered={j for _,j in anchors}
            # One damaged reference must not transfer its row's label/tail
            # into a preceding entry. Three literal own-row anchors and the
            # first label establish placement, never the uncertain spelling.
            if 0 in covered and len(covered)>=3 and len(covered)/len(candidate)>=.75:
                rows.append(row[min(i for i,_ in anchors):max(i for i,_ in anchors)+1])
        if len(rows)!=1:continue
        for word in rows[0]:
            occurrence=_source_instance(word)
            proved_rows.setdefault(occurrence,set()).add(block['id'])
            proved_row_words[occurrence]=word
    for block in answer['blocks']:
        if block['role'] not in ('body','other','caption','heading','footnote'):continue
        candidate=[key(token)for token in re.sub(r'<n:[^>]+>',' ',block['text']).split()]
        lexical=[token for token in candidate if any(c.isalpha()for c in token)]
        short_list=(block['role']=='body' and
                    bool(re.match(r'^\d{1,3}[.)]\s',block['text'])) and
                    2<=len(lexical)<=4 and len(candidate)<=6)
        if len(lexical)<3 and not short_list:continue
        boxes=regions.get(block['id'],[block_rect(block,display)])
        if block['role']=='footnote' and block.get('marker'):
            heights=[w['bbox'][3]-w['bbox'][1]for w in words
                     if any(owned_words([w],box)for box in boxes)]
            if heights:
                import statistics
                hint=block_rect(block,display);pad=3*statistics.median(heights)
                boxes=boxes+[[max(display.rect.x0,hint[0]-pad),hint[1],
                              min(display.rect.x1,hint[2]+pad),hint[3]]]
        all_local=_pixel_line_order([word for word in words if any(owned_words([word],box)for box in boxes)])
        local=[word for word in all_local if any(c.isalpha()for c in key(word['text']))]
        anchors=unique_anchors(local,range(len(local)),lexical,minimum=2 if short_list else 3)
        covered={j for i,j in anchors}
        complete=covered==set(range(len(lexical)))
        if short_list and len(_pixel_lines([local[i]for i,j in anchors]))!=1:continue
        if not complete and not(block['role']=='footnote'and len(covered)>=6
                                 and len(covered)/len(lexical)>=.8):continue
        selected={id(local[i])for i,j in anchors}
        # Ancillary dot leaders cannot become a second row's lexical bridge.
        # Numbers bind only when their literal is unique on this anchored row.
        for row in _pixel_lines(all_local):
            row_anchors=[w for w in row if id(w)in selected]
            if not row_anchors:continue
            for number in {t for t in candidate if t.isdigit()}:
                hits=[w for w in row if key(w['text'])==number]
                if len(hits)==1:selected.add(id(hits[0]));row_anchors+=hits
            left=min(w['bbox'][0]for w in row_anchors);right=max(w['bbox'][2]for w in row_anchors)
            selected.update(id(w)for w in row if not key(w['text'])
                            and left<=w['bbox'][0]and w['bbox'][2]<=right)
        for word in all_local:
            if id(word)not in selected:continue
            proposals.setdefault(_source_instance(word),set()).add(block['id'])
            if block['role']=='footnote':
                bound=regions.setdefault(block['id'],[block_rect(block,display)])
                if not any(owned_words([word],box)for box in bound):
                    bound.append(list(word['bbox']))
                    additions=regions.setdefault('_row_additions',{}).setdefault(block['id'],[])
                    if not isinstance(additions,list):
                        additions=list(additions);regions['_row_additions'][block['id']]=additions
                    additions.append(_source_instance(word))
    instances=regions.setdefault('_instances',{})
    for occurrence,owners in proposals.items():
        if len(owners)==1:instances.setdefault(occurrence,next(iter(owners)))
    # OCR can omit a list's narrow number column entirely. Its independently
    # owned lexical row locates the adjacent original label without certifying
    # the label's spelling. A preceding row's label cannot join this row.
    for block in answer['blocks']:
        if block['role']!='body' or not re.match(r'^\d{1,3}[.)]\s',block['text']):continue
        own=[w for w in words if instances.get(_source_instance(w))==block['id']]
        for row in _pixel_lines(own):
            if len(row)<2:continue
            import statistics
            left=min(w['bbox'][0]for w in row)
            middle=statistics.median((w['bbox'][1]+w['bbox'][3])/2 for w in row)
            height=statistics.median(w['bbox'][3]-w['bbox'][1]for w in row)
            labels=[w for w in words if _source_instance(w)not in instances and
                    re.fullmatch(r'\d{1,3}',key(w['text'])) and
                    0<=left-w['bbox'][2]<=2*height and
                    abs((w['bbox'][1]+w['bbox'][3])/2-middle)<=.5*height]
            if len(labels)==1:instances[_source_instance(labels[0])]=block['id']
    for occurrence,owners in proved_rows.items():
        if len(owners)!=1:continue
        owner=next(iter(owners));instances[occurrence]=owner
        word=proved_row_words[occurrence]
        bound=regions.setdefault(owner,[block_rect(next(b for b in answer['blocks']if b['id']==owner),display)])
        if not any(owned_words([word],box)for box in bound):
            bound.append(list(word['bbox']))
            additions=regions.setdefault('_row_additions',{}).setdefault(owner,[])
            if not isinstance(additions,list):
                additions=list(additions);regions['_row_additions'][owner]=additions
            additions.append(occurrence)


def _native_lexical_view(display,words,marker_prefixes=()):
    from .native_text import lexical_word_view
    if not display.page.get_texttrace():return words
    def physical(word):
        return dict(word,bbox=list(display.source_rect(word['bbox'])*display.page.derotation_matrix))
    grouped=lexical_word_view(display.page,[physical(word)for word in words],marker_prefixes)
    result=[]
    for word in grouped:
        value=dict(word,bbox=list(display.reading_rect(word['bbox'])))
        if 'native_parts'in word:
            value['native_parts']=[dict(p,bbox=list(display.reading_rect(p['bbox'])))for p in word['native_parts']]
        result.append(value)
    return result


def _native_wrap_view(words,tokens):
    """Conserve native letters across a geometrically proved line-end wrap."""
    from difflib import SequenceMatcher
    proposed=[t for t in tokens if not t.startswith('<n:')]
    key=vision_reading.normalized
    changes={}
    for kind,a,z,b,y in SequenceMatcher(None,[key(w['text'])for w in words],
                                      [key(t)for t in proposed],autojunk=False).get_opcodes():
        if kind!='replace' or z-a!=2 or y-b!=1:continue
        left,right=words[a:z];x,q=left['bbox'],right['bbox']
        height=min(x[3]-x[1],q[3]-q[1])
        joined=left['text'][:-1]+right['text']
        if (left['text'].endswith('-') and key(proposed[b])in(key(joined),key(left['text']+right['text']))
                and (q[1]+q[3]-x[1]-x[3])/2>height*.5 and q[0]<=x[0]+height*.5):
            changes[a]=dict(left,text=proposed[b],bbox=vision_reading.union([x,q]),
                native_parts=[dict(text=w['text'],bbox=w['bbox'])for w in (left,right)])
    return [changes[i]if i in changes else word for i,word in enumerate(words)if i-1 not in changes]


def _native_ink_regions(territories,words):
    """Cover only already-owned, positively proved native word components."""
    boxes=[list(box)for box in territories]
    for word in words:
        for part in word.get('native_parts',[word]):
            ink=part['bbox']
            if any(b[0]<=ink[0] and b[1]<=ink[1] and b[2]>=ink[2] and b[3]>=ink[3]for b in boxes):continue
            enlarged=[vision_reading.union([box,ink])for box in boxes]
            index=min(range(len(boxes)),key=lambda i:(enlarged[i][2]-enlarged[i][0])*(enlarged[i][3]-enlarged[i][1])-
                      (boxes[i][2]-boxes[i][0])*(boxes[i][3]-boxes[i][1]))
            boxes[index]=enlarged[index]
    return boxes


def _complete_note_head_territory(block,answer,words,display,regions,local):
    """Locate an exact reader opening on adjacent, otherwise unclaimed source ink.

    This widens a crop, not transcription authority. The original source and
    independent recognition still adjudicate every added word normally.
    """
    if block.get('role')!='footnote' or len(local)<6 or '<n:'in block['text']:return None
    import statistics
    from difflib import SequenceMatcher
    key=lambda text:''.join(c for c in vision_reading.normalized(text).casefold()if c.isalnum())
    model=block['text'].split();suffix=key(''.join(w['text']for w in local))
    if len(model)<=len(local) or not suffix:return None
    splits=[i for i in range(1,len(model)-5)
            if abs(len(key(''.join(model[i:])))-len(suffix))<=2
            and SequenceMatcher(None,suffix,key(''.join(model[i:]))).ratio()>=.95]
    if not splits:return None
    rank=lambda i:(abs(len(key(''.join(model[i:])))-len(suffix)),
                   -SequenceMatcher(None,suffix,key(''.join(model[i:]))).ratio())
    splits.sort(key=rank)
    if len(splits)>1 and rank(splits[0])==rank(splits[1]):return None
    head=key(''.join(model[:splits[0]]))
    if not head or splits[0]>20:return None
    height=statistics.median(w['bbox'][3]-w['bbox'][1]for w in local)
    first=min((w['bbox'][1]+w['bbox'][3])/2 for w in local)
    left=min(w['bbox'][0]for w in local)-3*height
    right=max(w['bbox'][2]for w in local)+3*height
    existing={_source_instance(w)for w in local}
    preceding=_pixel_line_order([w for w in words if _source_instance(w)not in existing
        and left<=(w['bbox'][0]+w['bbox'][2])/2<=right
        and first-2.5*height<=(w['bbox'][1]+w['bbox'][3])/2<first-.5*height])
    selected=[];literal=''
    for word in reversed(preceding):
        selected.insert(0,word);literal=key(word['text'])+literal
        if not head.endswith(literal):return None
        if literal==head:break
    if literal!=head:return None
    for word in selected:
        claim=(regions or {}).get('_instances',{}).get(_source_instance(word))
        if claim is not None and claim!=block['id']:return None
        x=(word['bbox'][0]+word['bbox'][2])/2;y=(word['bbox'][1]+word['bbox'][3])/2
        for other in answer['blocks']:
            if other['id']==block['id']:continue
            if any(b[0]<=x<=b[2]and b[1]<=y<=b[3]
                   for b in (regions or {}).get(other['id'],[block_rect(other,display)])):return None
    return selected+list(local)



def _complete_note_tail_territory(block,answer,words,display,regions,local):
    """Locate an exact reader tail on adjacent, otherwise unclaimed source ink.

    This widens a crop, not transcription authority. The original source and
    independent recognition still adjudicate every added word normally.
    """
    if block.get('role')!='footnote' or len(local)<6 or '<n:'in block['text']:return None
    import statistics
    from difflib import SequenceMatcher
    key=lambda text:''.join(c for c in vision_reading.normalized(text).casefold()if c.isalnum())
    model=block['text'].split();prefix=key(''.join(w['text']for w in local))
    if not prefix or len(key(''.join(model)))<=len(prefix):return None
    splits=[i for i in range(1,len(model))
            if abs(len(key(''.join(model[:i])))-len(prefix))<=2
            and SequenceMatcher(None,prefix,key(''.join(model[:i]))).ratio()>=.95]
    if not splits:return None
    rank=lambda i:(abs(len(key(''.join(model[:i])))-len(prefix)),
                   -SequenceMatcher(None,prefix,key(''.join(model[:i]))).ratio())
    splits.sort(key=rank)
    if len(splits)>1 and rank(splits[0])==rank(splits[1]):return None
    tail=key(''.join(model[splits[0]:]))
    if not tail or len(model)-splits[0]>20:return None
    height=statistics.median(w['bbox'][3]-w['bbox'][1]for w in local)
    last=max((w['bbox'][1]+w['bbox'][3])/2 for w in local)
    left=min(w['bbox'][0]for w in local)-3*height
    right=max(w['bbox'][2]for w in local)+3*height
    existing={_source_instance(w)for w in local}
    terminal=_pixel_line_order(local)[-1]
    terminal_right=terminal['bbox'][2]
    following=_pixel_line_order([w for w in words if _source_instance(w)not in existing
        and left<=(w['bbox'][0]+w['bbox'][2])/2<=right
        and ((last+.5*height<(w['bbox'][1]+w['bbox'][3])/2<=last+2.5*height)
             or (abs((w['bbox'][1]+w['bbox'][3])/2-last)<=.5*height
                 and w['bbox'][0]>=terminal_right-.1*height))])
    selected=[];literal=''
    for word in following:
        selected.append(word);literal+=key(word['text'])
        if not tail.startswith(literal):return None
        if literal==tail:break
    if literal!=tail:return None
    for word in selected:
        claim=(regions or {}).get('_instances',{}).get(_source_instance(word))
        if claim is not None and claim!=block['id']:return None
        x=(word['bbox'][0]+word['bbox'][2])/2;y=(word['bbox'][1]+word['bbox'][3])/2
        for other in answer['blocks']:
            if other['id']==block['id']:continue
            if any(b[0]<=x<=b[2]and b[1]<=y<=b[3]
                   for b in (regions or {}).get(other['id'],[block_rect(other,display)])):return None
    return list(local)+selected


def _complete_optical_row_openings(block,answer,pixels,display,regions,local):
    """Locate unclaimed row openings from unique following source anchors.

    A first-line indent cannot cut later lines at the same horizontal edge.
    Only already recognized, exact, adjacent source instances may enlarge the
    region. Their geometry supplies no new spelling authority.
    """
    from difflib import SequenceMatcher
    key=lambda text:''.join(c for c in vision_reading.normalized(text).casefold()if c.isalnum())
    model=[key(t)for t in re.sub(r'<n:[^>]+>',' ',block['text']).split()]
    source=[key(w['text'])for w in local]
    known={_source_instance(w)for w in local};added=[]
    for kind,a,z,b,y in SequenceMatcher(None,source,model,autojunk=False).get_opcodes():
        if kind!='insert' or a>=len(local) or not 1<=y-b<=20:continue
        anchor=model[y:y+3]
        if (len(anchor)!=3 or source[z:z+3]!=anchor
                or sum(model[i:i+3]==anchor for i in range(len(model)-2))!=1
                or sum(source[i:i+3]==anchor for i in range(len(source)-2))!=1):continue
        right=local[a]['bbox'];height=right[3]-right[1]
        if height<=0:continue
        center=(right[1]+right[3])/2
        if (a and abs(sum(local[a-1]['bbox'][1::2])/2-center)<=.5*height
                or any(abs(sum(w['bbox'][1::2])/2-center)>.5*height for w in local[a:a+3])):continue
        candidates=[]
        for word in pixels:
            box=word['bbox'];x=(box[0]+box[2])/2;cy=(box[1]+box[3])/2
            if (_source_instance(word)in known or box[2]>right[0]+.1*height
                    or abs(cy-center)>.5*height or not .5*height<=box[3]-box[1]<=1.8*height):continue
            claim=(regions or {}).get('_instances',{}).get(_source_instance(word))
            if claim is not None and claim!=block['id']:continue
            if any(other['id']!=block['id']and any(r[0]<=x<=r[2]and r[1]<=cy<=r[3]
                   for r in (regions or {}).get(other['id'],[block_rect(other,display)]))
                   for other in answer['blocks']):continue
            candidates.append(word)
        candidates.sort(key=lambda word:word['bbox'][0]);selected=candidates[-(y-b):]
        if (len(selected)!=y-b
                or [key(word['text'])for word in selected]!=model[b:y]):continue
        following=right
        for word in reversed(selected):
            box=word['bbox'];gap=following[0]-box[2]
            if not -.1*height<=gap<=2*height:break
            following=box
        else:
            added.extend(selected);known.update(_source_instance(word)for word in selected)
    return added


def _complete_optical_row_territory(block,answer,pixels,display,regions,body_owners,source_words=()):
    """Recover exact unclaimed words at any clipped physical row edge.

    Page recognition locates the missing instances before crop adjudication.
    Model rectangles are hints: an adjacent proved row may extend beyond one.
    Other owners, distant ink and ambiguous model anchors remain excluded.
    Widening a territory confers no spelling authority.
    """
    if block['role'] not in ('body','other','caption','footnote') or not pixels:return []
    local=_pixel_line_order(_owned_block_words(block,answer,pixels,display,regions,body_owners,ordered=True))
    if len(local)<6:return []
    import statistics
    from difflib import SequenceMatcher
    key=lambda t:''.join(c for c in vision_reading.normalized(t).casefold()if c.isalnum())
    model=[key(t)for t in re.sub(r'<n:[^>]+>',' ',block['text']).split()]
    source=[key(w['text'])for w in local]
    alignment=SequenceMatcher(None,source,model,autojunk=False)
    if sum(n for _,_,n in alignment.get_matching_blocks())<6:return []
    known={_source_instance(w)for w in local};result=[]
    for kind,a,z,b,y in alignment.get_opcodes():
        divided=(kind=='replace' and z-a==y-b==1 and source[a]
            and len(model[b])>len(source[a]) and model[b].endswith(source[a]))
        if (kind!='insert' and not divided) or a==0 or not 1<=y-b<=20:continue
        anchor=model[max(0,b-3):b]
        if len(anchor)<3 or sum(model[i:i+3]==anchor for i in range(len(model)-2))!=1:continue
        left=local[a-1]['bbox'];height=left[3]-left[1]
        if height<=0:continue
        middle=(left[1]+left[3])/2
        next_box=local[a]['bbox']if a<len(local)else None
        same_next=next_box is not None and abs((next_box[1]+next_box[3])/2-middle)<=.5*height
        following=[]
        for word in pixels:
            box=word['bbox'];x=(box[0]+box[2])/2;y0=(box[1]+box[3])/2
            if (_source_instance(word)in known or box[0]<left[2]-.1*height
                    or abs(y0-middle)>.5*height or not .5*height<=box[3]-box[1]<=1.8*height
                    or same_next and box[2]>next_box[0]+.1*height):continue
            claim=(regions or {}).get('_instances',{}).get(_source_instance(word))
            if claim is not None and claim!=block['id']:continue
            if any(other['id']!=block['id']and any(r[0]<=x<=r[2]and r[1]<=y0<=r[3]
                   for r in (regions or {}).get(other['id'],[block_rect(other,display)]))
                   for other in answer['blocks']):continue
            following.append(word)
        following.sort(key=lambda w:w['bbox'][0]);selected=following[:y-b]
        if divided:
            # A clipped row-ending prefix and the owned next-row tail are one
            # source occurrence only when their printed division and unique
            # anchors prove it. This extends geometry, never spelling proof.
            if (source[max(0,a-3):a]!=anchor or next_box is None
                    or not .5*height<(next_box[1]+next_box[3])/2-middle<=2.5*height
                    or next_box[0]>=left[0] or len(selected)!=1):continue
            prefix=selected[0];box=prefix['bbox']
            # Page OCR may misread a stroke in the divided prefix. A unique
            # co-located source occurrence can still locate that same ink; the
            # enlarged upright crop must subsequently prove its letters.
            from .pixel_adjudication import _complete_word_occurrence
            mapped_prefix=[w for w in source_words
                if w['text'].rstrip().endswith(('-', '­', '‐'))
                and key(w['text'])+source[a]==model[b]
                and _complete_word_occurrence(w,prefix)]
            if (not prefix['text'].rstrip().endswith(('-', '­', '‐'))
                    or key(prefix['text'])+source[a]!=model[b] and len(mapped_prefix)!=1
                    or box[0]-left[2]>2*height):continue
            tail_middle=(next_box[1]+next_box[3])/2
            if any(next_box[0]-.5*height<=(w['bbox'][0]+w['bbox'][2])/2<=box[2]+.5*height
                   and middle+.5*height<(w['bbox'][1]+w['bbox'][3])/2<tail_middle-.3*height
                   for w in pixels):continue
            result.append(prefix);known.add(_source_instance(prefix))
            continue
        # A bad rectangle may end before the final printed row altogether.
        # Three unique preceding source/model anchors and an exact next-row
        # prefix locate that same ending; neither a distant word nor a foreign
        # owner can enlarge the territory. It still earns no spelling proof.
        if (len(selected)!=y-b and a==len(local) and y==len(model)
                and source[max(0,a-3):a]==anchor
                and sum(source[i:i+3]==anchor for i in range(len(source)-2))==1):
            prefix=list(selected)if all(key(w['text'])==expected for w,expected in zip(selected,model[b:y]))else[]
            if prefix and (prefix[0]['bbox'][0]-left[2]>2*height
                    or any(right['bbox'][0]-left_word['bbox'][2]>2*height for left_word,right in zip(prefix,prefix[1:]))):prefix=[]
            territory=[r for r in (regions or {}).get(block['id'],[])
                if r[0]<=sum(left[::2])/2<=r[2] and r[1]<=middle<=r[3]]
            if territory and (not selected or prefix):
                lo=min(r[0]for r in territory);hi=max(r[2]for r in territory)
                below=[w for w in pixels if lo-.5*height<=(w['bbox'][0]+w['bbox'][2])/2<=hi+.5*height
                    and middle+.5*height<(w['bbox'][1]+w['bbox'][3])/2<=middle+2.5*height
                    and .5*height<=w['bbox'][3]-w['bbox'][1]<=1.8*height]
                rows=_pixel_lines(below)
                if rows:
                    first=sorted(rows[0],key=lambda w:w['bbox'][0]);candidate=first[:y-b-len(prefix)]
                    def available(word):
                        r=word['bbox'];x=(r[0]+r[2])/2;cy=(r[1]+r[3])/2
                        return (_source_instance(word)not in known
                            and (regions or {}).get('_instances',{}).get(_source_instance(word),block['id'])==block['id']
                            and not any(other['id']!=block['id']and any(v[0]<=x<=v[2]and v[1]<=cy<=v[3]
                                for v in (regions or {}).get(other['id'],[block_rect(other,display)]))for other in answer['blocks']))
                    if len(candidate)+len(prefix)==y-b and all(available(w)for w in prefix+candidate):
                        selected=prefix+candidate
                        previous_box=None
                        for word in selected:
                            if previous_box and abs(sum(word['bbox'][1::2])-sum(previous_box[1::2]))<=height and word['bbox'][0]-previous_box[2]>2*height:break
                            previous_box=word['bbox']
                        else:
                            if all(key(w['text'])==expected for w,expected in zip(selected,model[b:y])):
                                result.extend(selected);known.update(_source_instance(w)for w in selected)
                                continue
                        selected=[]
        if len(selected)!=y-b:continue
        def located(word,expected):
            if key(word['text'])==expected:return True
            # An original text occurrence can locate the same printed word
            # when page OCR spells it differently. It supplies geometry only;
            # the widened original crop still adjudicates both readings.
            from .pixel_adjudication import _complete_word_occurrence
            matches=[w for w in source_words if key(w['text'])==expected
                and _complete_word_occurrence(w,word)]
            return len(matches)==1
        if not all(located(w,expected)for w,expected in zip(selected,model[b:y])):continue
        previous=left
        for word in selected:
            if word['bbox'][0]-previous[2]>2*height:break
            previous=word['bbox']
        else:
            result.extend(selected);known.update(_source_instance(w)for w in selected)
    for word in _complete_optical_row_openings(block,answer,pixels,display,regions,local):
        if _source_instance(word)not in known:
            result.append(word);known.add(_source_instance(word))
    return result


def _complete_closing_mark_territory(block,answer,words,pixel_words,display,regions,local):
    """Include one adjacent, independently recognized closing mark in its word."""
    if not local or not pixel_words:return None
    from . import pixel_adjudication
    model=[t for t in block['text'].split()if not t.startswith('<n:')]
    if not model or model[-1][-1:]not in ('"','”','’'):return None
    last=_pixel_line_order(local)[-1]
    existing={_source_instance(w)for w in local};found=[]
    for word in words:
        if (_source_instance(word)in existing or not word['text']
                or not all(c in '"”’'for c in word['text'])):continue
        joined=pixel_adjudication._terminal_word_view([model[-1]],[last,word])
        if len(joined)!=1 or pixel_adjudication._key(joined[0]['text'])!=pixel_adjudication._key(model[-1]):continue
        box=word['bbox']
        visible=[w for w in pixel_words
            if pixel_adjudication._key(w['text'])==pixel_adjudication._key(word['text'])
            and w.get('confidence',0)>=50
            and box[0]-1<=(w['bbox'][0]+w['bbox'][2])/2<=box[2]+1
            and box[1]-1<=(w['bbox'][1]+w['bbox'][3])/2<=box[3]+1]
        if len(visible)!=1:continue
        claim=(regions or {}).get('_instances',{}).get(_source_instance(word))
        if claim is not None and claim!=block['id']:continue
        x=(box[0]+box[2])/2;y=(box[1]+box[3])/2
        if any(other['id']!=block['id']and any(b[0]<=x<=b[2]and b[1]<=y<=b[3]
            for b in (regions or {}).get(other['id'],[block_rect(other,display)]))for other in answer['blocks']):continue
        found.append(word)
    return list(local)+found if len(found)==1 else None


def _complete_native_reading_territory(block,answer,words,display,regions,body_owners,local):
    """Restore a clipped optical territory only from complete original print."""
    if (not local or len(local)>=len(block['text'].split()) or '<n:'in block['text']
            or block.get('role')not in ('body','heading','caption')):return None
    from .native_text import drawn_word_authorities,unproved_legacy_word,visible_word_ink
    def original(items):
        return [dict(w,bbox=list(display.source_rect(w['bbox'])*display.page.derotation_matrix),
            **({'native_parts':[dict(p,bbox=list(display.source_rect(p['bbox'])*display.page.derotation_matrix))for p in w['native_parts']]}if 'native_parts'in w else{}))for w in items]
    if sum(drawn_word_authorities(display.page,original(local)))<2:return None
    proposed=dict(regions or {});proposed[block['id']]=[block_rect(block,display)]
    candidate=_native_lexical_view(display,_owned_block_words(block,answer,words,display,
        proposed,body_owners,ordered=True))
    tokens=block['text'].split()
    if not len(local)<len(candidate)==len(tokens):return None
    keys=[_source_instance(w)for w in candidate]
    if len(set(keys))!=len(keys):return None
    positions={key:i for i,key in enumerate(keys)}
    prior=[positions.get(_source_instance(w),-1)for w in local]
    if -1 in prior or prior!=sorted(set(prior)):return None
    raw=original(candidate);known=drawn_word_authorities(display.page,raw);matched=0;changed=0
    for word,token,proved,physical in zip(candidate,tokens,known,raw):
        if proved:
            if vision_reading.normalized(word['text'])==vision_reading.normalized(token):matched+=1
            else:changed+=1
        elif not(unproved_legacy_word(display.page,physical)and visible_word_ink(display.page,[physical])[0]):
            return None
    # One reader error can be adjudicated from mapped print only in a complete
    # occurrence with six unchanged words; a reworded passage earns no binding.
    if changed>1 or changed and matched<6:return None
    return candidate


def _remove_foreign_word_runs(result,bid,answer,optical,regions):
    """Remove a borrowed run only at a proved own-to-foreign source boundary.

    Recovery can reserve the rest of a printed row as a new unit. A model's
    unbound continuation must not publish those same occurrences again. Equal
    words elsewhere, absent geometry and isolated unplaced words prove no copy.
    """
    if not regions or not optical:return
    from .pixel_adjudication import _complete_word_occurrence, _key
    tokens=result['tokens'];boxes=result['boxes'];removed=set();runs=[]
    start=0
    while start<len(tokens):
        if start in boxes or tokens[start].startswith('<n:'):
            start+=1;continue
        end=start+1
        while end<len(tokens)and end not in boxes and not tokens[end].startswith('<n:'):end+=1
        if end-start>=2 and start>0 and start-1 in boxes:
            literal=[_key(t)for t in tokens[start:end]];matches=[]
            for offset in range(1,len(optical)-len(literal)+1):
                prior=optical[offset-1]
                if (_key(prior['text'])!=_key(tokens[start-1])
                        or not _complete_word_occurrence(prior,dict(bbox=boxes[start-1]))):continue
                words=optical[offset:offset+len(literal)]
                if [_key(w['text'])for w in words]!=literal:continue
                owners=[]
                for word in words:
                    own=regions.get('_instances',{}).get(_source_instance(word))
                    if own is None:
                        candidates=[b['id']for b in answer['blocks']if b['id']!=bid
                            and any(owned_words([word],r)for r in regions.get(b['id'],[]))]
                        own=candidates[0]if len(candidates)==1 else None
                    owners.append(own)
                if owners and len(set(owners))==1 and owners[0] not in (None,bid):
                    matches.append((words,owners[0]))
            if len(matches)==1:
                words,owner=matches[0];removed.update(range(start,end))
                runs.append(dict(indices=list(range(start,end)),words=tokens[start:end],owner=owner,
                    source_boxes=[w['bbox']for w in words]))
        start=end
    if not removed:return
    mapping={old:new for new,old in enumerate(i for i in range(len(tokens))if i not in removed)}
    result['tokens']=[t for i,t in enumerate(tokens)if i not in removed]
    for key in ('boxes','word_notes'):
        result[key]={mapping[i]:v for i,v in result.get(key,{}).items()if i in mapping}
    result['marked']={mapping[i]for i in result['marked']if i in mapping}
    result['verified_unchanged']=[mapping[i]for i in result.get('verified_unchanged',[])if i in mapping]
    result.setdefault('guard',[]).append(dict(kind='foreign_owned_run_published_once',runs=runs))


def _present_print_only_images(text,display,pno,bid,territories,package,images,extras):
    """Declined word previews keep their own print at block scale.

    Move existing images and their reciprocal controls only. Combining their
    rectangles would blur independent occurrence ownership, add a duplicate
    disclosure, and could reintroduce unrelated ink between the source parts.
    """
    flags=[m.span()for m in re.finditer(r'<span class="reflow-flagged-reading">.*?</a></span>',text,re.S)]
    pattern=re.compile(r'<a class="source-word-image"[^>]*href="#([^"]+)"[^>]*><img src="([^"]+)"[^>]*/></a>'
        r'(?P<retained>(?:<a\b[^>]*class="noteref"[^>]*>.*?</a>)?)'
        r'(?P<closing>(?:\s*</(?:sup|sub|em|strong|i|b|span)>\s*)*)'
        r'(?P<actions>(?:\s*<a id="[^"]+" class="source-disclosure-ref" epub:type="noteref" href="#[^"]+"><sup>†</sup></a>)+)')
    candidates=[m for m in pattern.finditer(text)if not any(a<=m.start()<b for a,b in flags)]
    selected=[m for m in candidates if images.get(m[2],{}).get('kind')!='inline_print_preview']
    offsets={m.start()for m in selected};compact={m.start()for m in candidates}-offsets
    def replace(match):
        if match.start()in offsets:return match['retained']+match['closing']
        if match.start()in compact:
            core=match[0][:match.start('retained')-match.start()]
            return ('<span class="reflow-print-control">'+core+match['retained']+match['actions']
                +'</span>'+match['closing'])
        return match[0]
    text=pattern.sub(replace,text)
    if not selected:return text,''
    prints=[]
    for match in selected:
        # A multirow inspection has no credible one-line em metrics. Its
        # original resource and masked source parts stay untouched.
        core=match[0][:match.start('retained')-match.start()]
        original=re.sub(r' style="[^"]*"','',core)+match['actions']
        prints.append('<span class="reflow-print-control">'+original+'</span>')
    return text,'<div class="reflow-unit-print">'+'<br/>'.join(prints)+'</div>'


def _present_flagged_prints(text,display,pno,bid,territories,package,images,extras,*,evidence_words=None,heading=False,protected_inspections=()):
    """Declined word previews and dense assistance use one legible unit print.

    An inspection asset may be a complete page or several physical rows. It
    never supplies inline word metrics. The live hypotheses remain underlined;
    their shared print follows the paragraph, without duplicating every phrase.
    """
    text,print_only_html=_present_print_only_images(text,display,pno,bid,territories,package,images,extras)
    pattern=re.compile(r'<span class="reflow-flagged-reading"><span class="reflow-plausible">(.*?)</span>'
        r'<a class="source-word-image"(?: title="Inspect original print")? href="#([^"]+)"><img src="([^"]+)"[^>]*?/></a></span>',re.S)
    matches=list(pattern.finditer(text))
    if not matches:return text,print_only_html
    from . import inline_print
    from html import unescape
    plain=lambda value:unescape(re.sub(r'<[^>]+>',' ',value))
    lex=lambda value:re.findall(r"[^\W_]+(?:['’\-][^\W_]+)*",plain(value),re.UNICODE)
    def record_presentation(src,record):
        # Encoded whole-page evidence is shared by its physical owner. A new
        # presentation of those bytes must not replace the admitted source
        # receipt's kind or coverage with optional-preview metadata.
        canonical=getattr(package,'aliases',{}).get(src,src)
        if canonical!=src and canonical in images:
            images[canonical].setdefault('presentation_uses',[]).append(record)
        else:images[src]=record
    def context_image(src,bounds,record):
        for existing,admitted in images.items():
            box=admitted.get('bbox',[])
            if (admitted.get('page')==pno and len(box)==4 and
                    all(abs(a-b)<.01 for a,b in zip(box,bounds)) and
                    admitted.get('kind')in ('vision_disclosure','unit_source_reference',
                        'located_inspection_context','flagged_unit_preview') and
                    not admitted.get('source_parts')):
                admitted.setdefault('presentation_uses',[]).append(record)
                return existing
        _word_disclosure_image(package,src,display,bounds)
        record_presentation(src,record)
        return src
    placed=inline_print.locate_context(plain(text),evidence_words or [],display.rect)
    replacements=[]
    inspections={};scopes={}
    for match in matches:
        start=len(lex(text[:match.start()]));count=len(lex(match[1]))
        own=[placed['word_boxes'].get(i)for i in range(start,start+count)]
        scopes.setdefault(match[2],[]).append(vision_reading.union(own)if own and all(own)else None)
    initial_flagged=sum(len(lex(m[1]))for m in matches)
    initial_dense=len(matches)>=2 and initial_flagged>=max(8,.25*len(lex(text)))
    for match in matches:
        start=len(lex(text[:match.start()]));count=len(lex(match[1]))
        boxes=[placed['word_boxes'].get(i)for i in range(start,start+count)]
        own=bool(boxes)and all(boxes)
        bounds=vision_reading.union(boxes)if own else None
        asset=images.get(match[3],{})
        # Tapping the evidence must not lead back to the stale crop which
        # prompted this re-placement. Unplaced words retain complete context,
        # explicitly identified as context rather than a word identification.
        target_scopes=scopes[match[2]]
        inspection_placed=all(target_scopes)
        inspection_bounds=vision_reading.union(target_scopes)if inspection_placed else placed['bbox']
        if match[2]not in inspections:
            original_src=asset.get('inspection_image',match[3])
            original=images.get(original_src,{})
            previous=original.get('bbox',[])
            # Existing correct padded inspections remain immutable and avoid
            # duplicate encoded assets. Only a changed occurrence is re-cut.
            same=(len(previous)==4 and inspection_placed and
                all(abs(a-b)<=3 for a,b in zip(previous,inspection_bounds)))
            inspection=original_src if same else f'images/{match[2]}-located-context.jpg'
            if not same:
                inspection=context_image(inspection,inspection_bounds,dict(page=pno,block=bid,bbox=inspection_bounds,
                    kind='located_inspection_context'if inspection_placed else'flagged_unit_preview',
                    presentation_role='located_inspection_context',coverage_eligible=False,
                    placement_method=placed['method'],unplaced_context=not inspection_placed))
            inspections[match[2]]=inspection
            for index,aside in enumerate(extras):
                # A factory fragment can contain several sibling notes. Scope
                # the replacement to this exact aside; changing every image in
                # the fragment would detach another note's source receipt.
                scoped=re.compile(r'(<aside\b[^>]*\bid="'+re.escape(match[2])+r'"[^>]*>)(.*?)(</aside>)',re.S)
                def replace_inspection(found):
                    if match[2]in protected_inspections:
                        # A neighboring typed reference may share the complete
                        # original span. Keep its admitted image and add the
                        # separately located word context; neither proof is
                        # allowed to erase the other's printed occurrence.
                        inner=found[2]
                        if not same:
                            context='<p>Located reading context:</p><img src="'+inspection+'" alt="Located original reading"/>'
                            if inspection_placed:
                                at=inner.find('<img')
                                inner=inner[:at]+context+'<p>Original context for the adjoining reference:</p>'+inner[at:]
                            else:
                                # Unplaced whole-page context is supplemental;
                                # keep the admitted reference receipt first.
                                at=inner.rfind('<a ')
                                if at<0:at=len(inner)
                                inner=inner[:at]+context+inner[at:]
                    else:
                        inner=re.sub(r'(<img\b[^>]*\bsrc=")[^"]+("[^>]*>)',
                            lambda m:m[1]+inspection+m[2],found[2])
                    if not inspection_placed:
                        inner=inner.replace('<p>','<p>Word placement is not proved; compare the complete source context. ',1)
                    return found[1]+inner+found[3]
                extras[index]=scoped.sub(replace_inspection,aside)
        # A clean-looking crop is still unsafe when the occurrence was bound
        # from stale rectangles. Rebuild from the source sequence's own slot.
        if initial_dense:continue
        preview=(inline_print.preview(display,bounds)
                 if own and not heading and count<=6 else None)
        preview=inline_print.readable_preview(preview,plain(match[1]))
        if preview is None:
            if asset.get('kind')=='inline_print_preview':
                images[match[3]]=dict(asset,kind='unplaced_word_preview')
            continue
        data,metrics=preview;src=match[3].rsplit('.',1)[0]+'-placed.png'
        # Optional previews are never source-coverage proofs. The original
        # inspection receipt remains separate and reciprocal.
        if hasattr(package,'image'):
            if src not in images:package.image(src,data)
            images[src]=dict(asset,kind='inline_print_preview',coverage_eligible=False,**metrics,
                placement_method=placed['method'])
            replacement=re.sub(r' style="[^"]*"',' style="'+metrics['style']+'"',match[0].replace(match[3],src))
            replacements.append((match.start(),match.end(),replacement))
    for a,z,value in reversed(replacements):text=text[:a]+value+text[z:]
    matches=list(pattern.finditer(text))
    refused=any(images.get(m[3],{}).get('kind')!='inline_print_preview'for m in matches)
    flagged=sum(len(re.sub(r'<[^>]+>',' ',m[1]).split())for m in matches)
    total=len(re.sub(r'<[^>]+>',' ',text).split())
    dense=len(matches)>=2 and flagged>=max(8,.25*total)
    # Only the compact evidence control is atomic, not the live reading.
    control=re.compile(r'(<a class="source-word-image"(?: title="Inspect original print")? href="#[^"]+"><img [^>]+/></a>)(</span>)(<a id="[^"]+" class="source-disclosure-ref" epub:type="noteref" href="#[^"]+"><sup>†</sup></a>)')
    if not(refused or dense):
        return control.sub(r'<span class="reflow-print-control">\1\3</span>\2',text),print_only_html
    refused_targets={m[2]for m in matches if images.get(m[3],{}).get('kind')!='inline_print_preview'}
    selected=[m for m in matches if dense or m[2]in refused_targets]
    targets={m[2]for m in selected}
    last_for_target={m[2]:m.start()for m in selected}
    def reading_link(match):
        if match[2]not in targets:return match[0]
        identity=' id="r-'+match[2]+'"'if match.start()==last_for_target[match[2]]else''
        return ('<span class="reflow-flagged-reading"><span class="reflow-plausible"><a'+identity
            +' title="Compare this reading with original print" href="#'+match[2]+'">'
            +match[1]+'</a></span></span>')
    text=pattern.sub(reading_link,text)
    action=re.compile(r'<a id="([^"]+)" class="source-disclosure-ref" epub:type="noteref" href="#([^"]+)"><sup>†</sup></a>')
    text=action.sub(lambda m:''if m[2]in targets else m[0],text)
    ident=f'v4-d-unit-print-{pno}-{bid}';origin='r-'+ident
    bounds=placed['bbox']
    src=f'images/{ident}.jpg'
    src=context_image(src,bounds,dict(page=pno,block=bid,bbox=bounds,kind='flagged_unit_preview',coverage_eligible=False,
        reason='dense_flagged_reading'if dense else'refused_word_preview',flagged_runs=len(selected),
        placement_method=placed['method'],unplaced_context=not bool(placed['word_boxes'])))
    details=[]
    for target in sorted(targets):
        # Show the highlighted words, not a second copy of each detailed
        # explanation. Their individual source receipts remain one click away.
        label=' '.join(re.sub(r'<[^>]+>',' ',m[1]).strip()for m in selected if m[2]==target)
        details.append('<li><a href="#'+target+'">'+label+'</a></li>')
    detail_links='<ul>'+''.join(details)+'</ul>'
    extras.append(f'<aside id="{ident}" class="footnote vision-disclosure" data-reflow-owner-page="{pno}" data-reflow-owner-block="{escape(bid,quote=True)}" epub:type="footnote"><p>Underlined readings are plausible, not independently proved. This original print is shared by the unit.</p><img src="{src}" alt="Original printed reading"/>{detail_links}<a href="#{origin}">Return</a></aside>')
    supplement=(f'<div class="reflow-unit-print"><a id="{origin}" class="source-disclosure-ref" epub:type="noteref" href="#{ident}">'
        f'<img src="{src}" alt=""/><sup>†</sup></a></div>')
    text=control.sub(r'<span class="reflow-print-control">\1\3</span>\2',text)
    return text,print_only_html+('' if heading else supplement)


def _coalesce_inline_source_images(pieces,display,pno,bid,package,images):
    """Publish overlapping adjacent ink once, with all inspection links intact.

    Padding is useful for inspection, but two neighboring digit/glyph crops can
    repeat the same printed strokes when inserted side by side. One original
    span preserves that ink and its actual spacing without clipping either word.
    Separate rows, columns and intervening live words never form a group.
    """
    pattern=re.compile(r'<a\b[^>]*class="source-word-image"[^>]*>\s*<img\b[^>]*src="([^"]+)"[^>]*/>\s*</a>')
    groups=[]
    for index,piece in enumerate(pieces):
        # Appended print accompanies live words and may share a broad receipt
        # with another run. It is not a repeated print-only word to coalesce.
        if 'class="reflow-flagged-reading"' in piece:
            groups.append([]);continue
        match=pattern.search(piece)
        image=images.get(match[1])if match else None
        if not image or not image.get('bbox')or image.get('source_parts'):
            groups.append([]);continue
        original=images.get(image.get('inspection_image'),image)
        if original.get('source_parts'):
            groups.append([]);continue
        box=original['bbox'];entry=(index,match,box)
        previous=groups[-1][-1]if groups and groups[-1]else None
        if previous:
            other=previous[2]
            same_row=min(other[3],box[3])-max(other[1],box[1])>=.5*min(other[3]-other[1],box[3]-box[1])
            overlap=min(other[2],box[2])-max(other[0],box[0])
            if same_row and overlap>.1*min(other[2]-other[0],box[2]-box[0]):
                # Intersecting inspection padding is not duplicated print.
                # Require actual strokes in the shared strip; two separate
                # letters with blank space between them keep separate images.
                shared=extract.pymupdf.Rect(max(other[0],box[0]),max(other[1],box[1]),
                    min(other[2],box[2]),min(other[3],box[3]))
                pix=display.pixmap(shared,scale=3,gray=True,max_pixels=100_000)
                samples=pix.samples
                if samples and max(samples)-min(samples)>40:
                    groups[-1].append(entry);continue
        groups.append([entry])
    for group in groups:
        if len(group)<2:continue
        box=vision_reading.union([entry[2]for entry in group])
        src=f'images/v4-word-span-{pno}-{bid}-{group[0][0]}-{group[-1][0]}.jpg'
        _word_disclosure_image(package,src,display,extract.pymupdf.Rect(box))
        images[src]=dict(page=pno,bbox=list(box),kind='vision_disclosure',
            source_word_indices=[index for index,_,_ in group],method='overlapping_inline_ink_once')
        from . import inline_print
        preview=inline_print.preview(display,box)if hasattr(package,'image')else None
        inline_src=src;style='height:1.4em;vertical-align:-0.25em;width:auto'
        if preview is not None:
            data,metrics=preview;inline_src=src[:-4]+'-inline.png'
            package.image(inline_src,data)
            images[inline_src]=dict(page=pno,kind='inline_print_preview',coverage_eligible=False,
                inspection_image=src,**metrics)
            style=metrics['style']
        for offset,(index,match,_)in enumerate(group):
            replacement=match[0].replace(match[1],inline_src)if offset==0 else ''
            if replacement:
                replacement=re.sub(r' style="[^"]*"',' style="'+style+'"',replacement)
            pieces[index]=pieces[index][:match.start()]+replacement+pieces[index][match.end():]


def _word_disclosure_image(package,name,display,clip,parts=None):
    if not parts:
        if hasattr(package,'disclosure_image'):package.disclosure_image(name,display,clip)
        else:package.image(name,display.jpeg(clip,scale=3,quality=90))
        return
    # A decorative initial spans several rows. Preserve only its own glyphs
    # and word suffix, not unrelated ink inside their enclosing rectangle.
    import io,math
    from PIL import Image
    original=Image.open(io.BytesIO(display.jpeg(clip,scale=3,quality=90))).convert('RGB')
    kept=Image.new('RGB',original.size,'white')
    for part in parts:
        box=part['bbox']if isinstance(part,dict)else part
        sx=original.width/clip.width;sy=original.height/clip.height
        region=(max(0,math.floor((box[0]-clip.x0)*sx)),max(0,math.floor((box[1]-clip.y0)*sy)),
                min(original.width,math.ceil((box[2]-clip.x0)*sx)),min(original.height,math.ceil((box[3]-clip.y0)*sy)))
        kept.paste(original.crop(region),region)
    encoded=io.BytesIO();kept.save(encoded,format='JPEG',quality=90)
    if len(encoded.getvalue())>2*1024*1024:raise extract.RasterTooLarge('compound word exceeds encoded-byte bound')
    package.image(name,encoded.getvalue())


def _owned_block_words(block,answer,items,display,regions=None,body_owners=None,ordered=False):
    """Use one source-ownership rule for publication and boundary inference."""
    territories=(regions or {}).get(block['id'],[block_rect(block,display)])
    other_body_regions=[box for other in answer['blocks']
        if other['id']!=block['id'] and other['role']=='body'
        for box in (regions or {}).get(other['id'],[])]
    def strictly_inside(word,box):
        x=(word['bbox'][0]+word['bbox'][2])/2
        y=(word['bbox'][1]+word['bbox'][3])/2
        return box[0]<=x<=box[2] and box[1]<=y<=box[3]
    seen=set();result=[];ordered_groups=[];pending_additions=[]
    def adjacent_groups(local,height):
        return [group for _,group in ordered_groups if any(
            abs((w['bbox'][1]+w['bbox'][3]-v['bbox'][1]-v['bbox'][3])/2)<=.5*height
            and (-.1*height<=w['bbox'][0]-v['bbox'][2]<=2*height
                 or -.1*height<=v['bbox'][0]-w['bbox'][2]<=2*height)
            for w in local for v in group)]
    for territory in territories:
        local=[]
        for word in owned_words(items,territory):
            if _source_instance(word)in(regions or {}).get('_ambiguous_instances',()):continue
            # Rounding admission must not pull a following row into an earlier
            # partial-row territory owned by this same block.
            if not strictly_inside(word,territory) and any(
                    strictly_inside(word,box) for box in territories if box is not territory):continue
            claim=(regions or {}).get('_instances',{}).get(_source_instance(word))
            if claim is not None and claim!=block['id']:continue
            # A paragraph may wrap beside an original illustration. Its broad
            # row envelope does not turn unclaimed diagram lettering into prose.
            # Independently proved BODY occurrences still outrank a coarse hint.
            if block['role']=='body' and claim!=block['id'] and any(
                    strictly_inside(word,box) for other in answer['blocks']
                    if other['id']!=block['id'] and other['role'] in ('figure','table')
                    for box in (regions or {}).get(other['id'],[block_rect(other,display)])):continue
            strict=any(strictly_inside(word,box) for box in territories)
            # A final printed territory outranks its neighbour's rounding halo.
            # Strict/strict overlap retains the existing coherent-owner rule.
            if block['role']=='body' and not strict and any(
                    strictly_inside(word,box) for box in other_body_regions):continue
            if block['role']=='footnote' and not strict and any(
                    strictly_inside(word,box)for other in answer['blocks']
                    if other['id']!=block['id']and other['role']=='footnote'
                    for box in (regions or {}).get(other['id'],[block_rect(other,display)])):continue
            # Reservations locate coherent paragraphs; only their final source
            # territories can exclude ink from another published block.
            if block['role'] in ('body','caption') and any(owned_words([word],box)
                    and (not strict or strictly_inside(word,box))
                    for owner,reservation in (body_owners or {}).items() if owner!=block['id']
                    for box in (regions or {}).get(owner,[reservation])):continue
            if claim!=block['id'] and block['role'] not in ('footnote','running_head','folio') and any(
                    owned_words([word],box) for other in answer['blocks']
                    if other['id']!=block['id'] and other['role'] in ('footnote','running_head','folio')
                    for box in (regions or {}).get(other['id'],[block_rect(other,display)])):continue
            if id(word) not in seen:local.append(word);seen.add(id(word))
        if not ordered:result.extend(local)
        elif local:
            import statistics
            prior=ordered_groups[-1][0] if ordered_groups else None
            height=statistics.median(w['bbox'][3]-w['bbox'][1]for w in local)
            additions=(regions or {}).get('_row_additions',{}).get(block['id'],())
            if additions and any(tuple(territory)==item[1]for item in additions):
                # Exact added row words keep the position of their neighbouring
                # original ink. Appending their crop after later rows must not
                # duplicate them as a second reading at the end of the unit.
                targets=adjacent_groups(local,height)
                if len(targets)==1:
                    targets[0].extend(local);continue
                # The outermost opening word may be too far away until its
                # intervening original words have joined the same row.
                pending_additions.append((territory,local,height));continue
            overlap=(min(prior[2],territory[2])-max(prior[0],territory[0]) if prior else 0)
            same_column=(prior is not None and overlap>=.5*min(prior[2]-prior[0],territory[2]-territory[0])
                         and prior[1]<=territory[1]<=prior[3]+2*height)
            # A raised terminal symbol can form a tiny territory before its
            # baseline text. Join only a single physically adjacent row, so
            # vertical sorting cannot move that glyph to the paragraph head.
            same_row=(bool(ordered_groups) and
                      len(_pixel_lines(ordered_groups[-1][1]+local))==1 and
                      any(-.1*height<=w['bbox'][0]-v['bbox'][2]<=2*height or
                          -.1*height<=v['bbox'][0]-w['bbox'][2]<=2*height
                          for w in local for v in ordered_groups[-1][1]))
            # Rounding can admit the last word of a split row through the first
            # territory. Order the connected column as one occurrence inventory;
            # a following column keeps its supplied reading-flow position.
            if same_column or same_row:
                ordered_groups[-1][0]=territory;ordered_groups[-1][1].extend(local)
            else:ordered_groups.append([territory,list(local)])
    while pending_additions:
        remaining=[];progress=False
        for territory,local,height in pending_additions:
            targets=adjacent_groups(local,height)
            if len(targets)==1:
                targets[0].extend(local);progress=True
            else:remaining.append((territory,local,height))
        if not progress:
            ordered_groups.extend([territory,list(local)]for territory,local,_ in remaining)
            break
        pending_additions=remaining
    if ordered:
        result=[]
        for _,group in ordered_groups:
            columns,proved=(_proved_recovery_columns(group)if block.get('role')in ('body','other')else([group],False))
            for column in columns if proved else [_pixel_line_order(group)]:result.extend(column)
    if block['role']=='heading' and result and body_owners:
        # A run-in heading can share a physical row with the following
        # paragraph. Its broad rectangle must not let a damaged recognition
        # word trigger a fallback that republishes the paragraph. Reserve exact
        # corroborated BODY instances, not a broad multi-row paragraph box;
        # heading anchors remain protected even when wording repeats elsewhere.
        keys=lambda text:[re.sub(r'[^\w]','',vision_reading.normalized(t).casefold())
                          for t in re.sub(r'<n:[^>]+>',' ',text).split()]
        own=_geometry_anchors(result,list(range(len(result))),keys(block['text']))
        protected={id(result[i]) for i,_ in own};reserved=set()
        for other in answer['blocks']:
            if other['role']!='body' or other['id'] not in body_owners:continue
            boxes=(regions or {}).get(other['id'],[body_owners[other['id']]])
            source=_pixel_line_order([w for w in items if any(strictly_inside(w,box) for box in boxes)])
            candidate=keys(other['text'])
            matches=_geometry_anchors(source,list(range(len(source))),candidate,body=True)
            matched=len({j for _,j in matches})
            if matched>=5 and matched/max(1,len(candidate))>=.6:
                reserved.update(id(source[i]) for i,_ in matches)
        result=[word for word in result if id(word) not in reserved or id(word) in protected]
    return result




def _unpartitioned_note_openings(answer,optical,display,regions,body_owners):
    """Find still-interior symbolic openings before accepting note geometry."""
    import statistics
    unproved=[]
    for block in answer['blocks']:
        if block['role']!='footnote' or block['marker'] not in ('*','†','‡'):continue
        owned=_owned_block_words(block,answer,optical,display,regions,body_owners,ordered=True)
        opening=regions.get('_note_openings',{}).get(block['id'])
        owned=_note_lexical_evidence(owned,opening)
        if not owned:continue
        height=max(1,statistics.median(w['bbox'][3]-w['bbox'][1]for w in owned))
        for position,word in enumerate(owned):
            if not position or word['text'] not in {block['marker'],'+'}:continue
            if not any(re.search(r'\w',w['text'])for w in owned[:position]):continue
            box=word['bbox'];middle=sum(box[1::2])/2
            row=[w for w in owned if w is not word and abs(sum(w['bbox'][1::2])/2-middle)<=height*.55]
            following=[w for w in row if 0<=w['bbox'][0]-box[2]<=4*height]
            preceding=[w for w in row if 0<=box[0]-w['bbox'][2]<=4*height]
            if following and not preceding:
                unproved.append(dict(block=block['id'],source_bbox=list(box)))
    return unproved


def _note_lexical_evidence(items, opening):
    """Exclude one bound label occurrence, without asserting its identity."""
    if not opening:return items
    expected=(opening['observed_text'],tuple(opening['source_bbox']))
    if any(_source_instance(word)==expected for word in items):
        return [word for word in items if _source_instance(word)!=expected]
    box=opening['source_bbox']
    def same_glyph(word):
        slot=word['bbox'];width=min(box[2]-box[0],slot[2]-slot[0]);height=min(box[3]-box[1],slot[3]-slot[1])
        return (min(box[2],slot[2])-max(box[0],slot[0])>=width*.7
            and min(box[3],slot[3])-max(box[1],slot[1])>=height*.5
            and max(box[2]-box[0],slot[2]-slot[0])<=3*max(.01,width))
    matches=[word for word in items if same_glyph(word)]
    return [word for word in items if word is not matches[0]]if len(matches)==1 else items


def _printed_wraps(tokens,optical):
    """Keep a literal printed hyphen, without inventing space at a row wrap."""
    from difflib import SequenceMatcher
    lexical=[(i,t) for i,t in enumerate(tokens) if not t.startswith('<n:')]
    source=[vision_reading.normalized(w['text']) for w in optical]
    published=[vision_reading.normalized(t) for _,t in lexical]
    matches=SequenceMatcher(None,source,published,autojunk=False)
    mapped={i:j for a,b,count in matches.get_matching_blocks()
            for i,j in zip(range(a,a+count),range(b,b+count))}
    rows=_pixel_lines(optical);starts={id(row[0]) for row in rows[1:]}
    return {lexical[mapped[i]][0] for i in range(1,len(optical))
            if id(optical[i]) in starts and optical[i-1]['text'].endswith(tuple(assemble.HYPHENS))
            and i in mapped and i-1 in mapped and mapped[i]==mapped[i-1]+1}


def _pixel_row_ids(words):
    return {id(word):index for index,row in enumerate(_pixel_lines(words)) for word in row}


def _narrow_row_edge(left_box,right_box,left_row,right_row):
    """Adjacent source words on one printed row, rather than a column gutter."""
    return (left_box is not None and right_box is not None and left_row is not None
            and left_row==right_row and
            0<=right_box[0]-left_box[2]<=max(left_box[3]-left_box[1],right_box[3]-right_box[1]))


def _source_punctuation_suffixes(words):
    """Exact adjacent punctuation instances, retaining original ink identity.

    A recognizer can split a word from its punctuation. This is geometry proof,
    not transcription correction: never discard punctuation, concatenate
    different words, or cross a physical row/gutter.
    """
    found={}
    for row in _pixel_lines(words):
        for index,word in enumerate(row):
            if not any(c.isalnum() for c in word['text']):continue
            members=[word];text=word['text']
            for suffix in row[index+1:]:
                if not suffix['text'] or not all(unicodedata.category(c).startswith('P')
                                                for c in suffix['text']):break
                if not _narrow_row_edge(members[-1]['bbox'],suffix['bbox'],0,0):break
                members.append(suffix);text+=suffix['text']
                found.setdefault(text,[]).append(tuple(members))
    return found


def _published_pixel_edges(tokens,optical):
    """Bind published endpoints to exact ink matches, not rectangle extremes.

    A tall word's region can also include ink from a neighboring printed row.
    Only exact sequence matches can provide physical continuation endpoints;
    unmatched or interpolated disclosure boxes cannot establish that proof.
    """
    from difflib import SequenceMatcher
    visible=[t for t in tokens if not t.startswith('<n:')]
    if not visible or not optical:return None,None
    lexical=lambda word:vision_reading.normalized(word).casefold()
    match=SequenceMatcher(None,[lexical(w['text']) for w in optical],
                          [lexical(t) for t in visible],autojunk=False)
    edges={}
    for source,published,count in match.get_matching_blocks():
        for endpoint in (0,len(visible)-1):
            if published<=endpoint<published+count:
                edges[endpoint]=optical[source+endpoint-published]
    # Admit only a unique literal cluster and require the same sequence proof
    # as an unsplit source token. Keep its original word for physical row IDs.
    clusters=_source_punctuation_suffixes(optical)
    for endpoint in (0,len(visible)-1):
        if endpoint in edges:continue
        candidates=clusters.get(visible[endpoint],[])
        proven=[]
        for members in candidates:
            identities={id(w) for w in members}
            combined={'text':visible[endpoint],
                      'bbox':vision_reading.union([w['bbox'] for w in members]),
                      '_source_word':members[0]}
            source_words=[combined if w is members[0] else w for w in optical
                          if w is members[0] or id(w) not in identities]
            proof=SequenceMatcher(None,[lexical(w['text']) for w in source_words],
                                  [lexical(t) for t in visible],autojunk=False)
            for source,published,count in proof.get_matching_blocks():
                if published<=endpoint<published+count:
                    bound=source_words[source+endpoint-published]
                    if bound is combined:proven.append(combined)
        # Repeated spelling is ordinary prose. Only ambiguity about which
        # physical occurrence fits this published endpoint blocks the proof.
        if len(proven)==1:edges[endpoint]=proven[0]
    return edges.get(0),edges.get(len(visible)-1)


def _caption_punctuation_anchors(optical,indices,text):
    """Prove a complete caption using printed punctuation boundaries.

    Recognition can place several punctuated words in one optical token.
    Keep their original source instance rather than inventing word rectangles.
    A complete, unique occurrence must start and finish at source-token
    boundaries. Plain letters never supply a missing boundary, and callers
    provide only local, unreserved source territory.
    """
    atoms=lambda value:re.findall(r'\w+',vision_reading.normalized(value).casefold())
    tokens=re.sub(r'<n:[^>]+>',' ',text).split()
    candidate=[(atom,j) for j,token in enumerate(tokens) for atom in atoms(token)]
    if not candidate:return []
    ordered=_pixel_line_order([optical[i] for i in indices])
    lookup={id(optical[i]):i for i in indices}
    source=[]
    for word in ordered:
        parts=atoms(word['text']);index=lookup[id(word)]
        source.extend((atom,index,k,len(parts)) for k,atom in enumerate(parts))
    wanted=[atom for atom,j in candidate];occurrences=[]
    for start in range(len(source)-len(wanted)+1):
        selected=source[start:start+len(wanted)]
        if selected[0][2]!=0 or selected[-1][2]!=selected[-1][3]-1:continue
        if [atom for atom,_,_,_ in selected]!=wanted:continue
        occurrences.append(selected)
        if len(occurrences)>1:return []
    if len(occurrences)!=1:return []
    return list(dict.fromkeys((index,j) for (_,index,_,_),(_,j)
        in zip(occurrences[0],candidate)))



def _caption_punctuation_suffix_anchors(optical,indices,text,hint):
    """Own an exact local caption ending without approving its damaged prefix.

    The whole suffix must be unique, substantial, begin inside the original
    hint and follow adjacent source rows. Callers exclude proved BODY owners
    and limit the pool to the existing nearby caption band. These anchors only
    establish printed ownership; they neither rewrite words nor remove marks.
    """
    import statistics
    tokens=re.sub(r'<n:[^>]+>',' ',text).split()
    for start in range(len(tokens)):
        if len(tokens)-start<5 or (len(tokens)-start)/max(1,len(tokens))<.6:break
        pairs=_caption_punctuation_anchors(optical,indices,' '.join(tokens[start:]))
        if not pairs:continue
        pairs=[(i,j+start) for i,j in pairs]
        first=optical[pairs[0][0]]
        if not owned_words([first],hint):continue
        distinct=list(dict.fromkeys(i for i,j in pairs))
        rows=_pixel_lines([optical[i] for i in distinct])
        height=statistics.median(max(1,optical[i]['bbox'][3]-optical[i]['bbox'][1])
            for i in distinct)
        rectangles=[vision_reading.union([w['bbox'] for w in row]) for row in rows]
        if any(b[1]-a[3]>2.5*height for a,b in zip(rectangles,rectangles[1:])):continue
        return pairs
    return []



def _caption_segmentation_evidence(optical,pairs,text):
    """Split only proved punctuation-fused source instances for the line guard.

    Original uncertainty alignment still sees the untouched source tokens.
    Unmatched prefix words and all unclaimed instances remain in this guard.
    Every reconstructed whitespace token retains its original source rectangle.
    """
    tokens=re.sub(r'<n:[^>]+>',' ',text).split()
    by_token={}
    for i,j in pairs:by_token.setdefault(j,[]).append(i)
    claimed={i for i,j in pairs};at={}
    for j,indices in by_token.items():
        at.setdefault(min(indices),[]).append(dict(text=tokens[j],
            bbox=vision_reading.union([optical[i]['bbox'] for i in indices])))
    result=[]
    for i,word in enumerate(optical):
        if i in at:result.extend(at[i])
        if i not in claimed:result.append(word)
    return result


def _geometry_anchors(optical,indices,candidate,body=False,wrap_pool=None):
    """Locate locally corroborated body ink without changing either reading.

    A source token can fuse a raised reference, or retain only a word's suffix.
    Only a unique substantial letter suffix inside a coherent paragraph may
    supply its geometry. Printed hyphen pieces additionally need the preceding
    exact word on the same physical row and an adjacent following row.
    """
    from difflib import SequenceMatcher
    lexical=lambda text:re.sub(r'[^\w]','',vision_reading.normalized(text).casefold())
    source=[lexical(optical[i]['text']) for i in indices]
    exact=SequenceMatcher(None,source,candidate,autojunk=False).get_matching_blocks()
    count=sum(n for _,_,n in exact)
    if body and count>=5 and count/max(1,len(candidate))>=.6:
        fixed=[(indices[i],j) for a,b,n in exact
               for i,j in zip(range(a,a+n),range(b,b+n))]
        proposals={}
        for offset,index in enumerate(indices):
            letters=''.join(c for c in lexical(optical[index]['text']) if c.isalpha())
            targets=[token for token in candidate if token.isalpha() and len(letters)>=4
                     and len(letters)>=.6*len(token) and token.endswith(letters)]
            if len(targets)==1 and targets[0] not in source:proposals[offset]=targets[0]
        for offset,target in proposals.items():
            if list(proposals.values()).count(target)!=1:continue
            position=candidate.index(target);box=optical[indices[offset]]['bbox']
            for neighbor,j in fixed:
                if abs(j-position)!=1:continue
                near=optical[neighbor]['bbox'];height=max(1,near[3]-near[1],box[3]-box[1])
                gap=max(0,max(box[1],near[1])-min(box[3],near[3]))
                ordered=(box[1]>=near[1]-height*.5 if j<position else box[1]<=near[1]+height*.5)
                row=[optical[i]['bbox'] for i,_ in fixed
                     if abs((optical[i]['bbox'][1]+optical[i]['bbox'][3]-near[1]-near[3])/2)<=height*.6]
                components=[]
                for rect in sorted(row,key=lambda r:r[0]):
                    if not components or rect[0]-components[-1][-1][2]>2*height:components.append([])
                    components[-1].append(rect)
                envelope=vision_reading.union(next(part for part in components if near in part))
                column=box[0]<envelope[2]+height and box[2]>envelope[0]-height
                if column and ordered and gap<=2.5*height:
                    source[offset]=target;break
    matches=SequenceMatcher(None,source,candidate,autojunk=False).get_matching_blocks()
    anchors=[(indices[i],j) for a,b,n in matches
             for i,j in zip(range(a,a+n),range(b,b+n))]
    if body and count>=5 and count/max(1,len(candidate))>=.6:
        # A model can already complete a word divided at a paragraph/art/page
        # boundary. Locate its partial printed atom only beside an exact,
        # corroborated neighbour in this same row. Joining or deduplicating
        # still requires the independently owned opposite physical atom.
        for position,leading in ((0,True),(len(candidate)-1,False)):
            if any(j==position for i,j in anchors):continue
            neighbour=next((i for i,j in anchors if j==position+(1 if leading else -1)),None)
            if neighbour is None:continue
            nearby=[];near=optical[neighbour]['bbox']
            for index in indices:
                raw=optical[index]['text'];letters=lexical(raw);box=optical[index]['bbox']
                partial=(len(letters)>=2 and letters!=candidate[position] and
                    (candidate[position].endswith(letters) if leading else
                     raw.endswith(tuple(assemble.HYPHENS)) and candidate[position].startswith(letters)))
                height=max(1,box[3]-box[1],near[3]-near[1])
                gap=near[0]-box[2] if leading else box[0]-near[2]
                same_row=abs((box[1]+box[3]-near[1]-near[3])/2)<=height*.55
                if partial and same_row and 0<=gap<=2*height:nearby.append(index)
            if len(nearby)==1:anchors.append((nearby[0],position))
        for suffix,position in list(anchors):
            letters=lexical(optical[suffix]['text'])
            if letters==candidate[position] or not candidate[position].endswith(letters):continue
            previous=next((i for i,j in anchors if j==position-1),None)
            if previous is None:continue
            tail=optical[suffix]['bbox'];before=optical[previous]['bbox']
            height=max(1,before[3]-before[1],tail[3]-tail[1])
            for index in (indices if wrap_pool is None else wrap_pool):
                word=optical[index]
                prefix=word['bbox']
                if not word['text'].endswith(tuple(assemble.HYPHENS)):continue
                if lexical(word['text'])+letters!=candidate[position]:continue
                same_row=abs((prefix[1]+prefix[3]-before[1]-before[3])/2)<=height*.55
                adjacent=0<=prefix[0]-before[2]<=2*height
                next_row=prefix[1]<tail[1] and 0<=tail[1]-prefix[3]<=2.5*height
                if same_row and adjacent and next_row and tail[0]<prefix[0]:
                    anchors.append((index,position))
    return sorted(set(anchors),key=lambda pair:(pair[1],optical[pair[0]]['bbox'][1],optical[pair[0]]['bbox'][0]))


def _bind_figures(answer,optical,display,regions):
    """Replace a model's illustration hint with the ink in a printed text gap."""
    from . import vision_pixels
    repairs=[];blocks=answer['blocks'];r=display.rect
    seeds={b['id']:block_rect(b,display) for b in blocks if b['role'] in ('figure','table')}
    # Only words corroborated by the block reading erect a prose barrier.
    # Diagram labels recognized by OCR therefore remain part of the diagram.
    rows=[]
    lex=lambda text:vision_reading.normalized(text).casefold()
    for b in blocks:
        if b['role'] in ('figure','table'):continue
        hint=block_rect(b,display)
        if b['role'] in ('folio','running_head') and any(
                hint[0]<s[2] and hint[2]>s[0] and hint[1]<s[3] and hint[3]>s[1]
                for s in seeds.values()):
            # A furniture label cannot turn lettering inside the original
            # artwork hint into a prose barrier, even when OCR agrees with it.
            continue
        local=_owned_block_words(b,answer,optical,display,regions,ordered=True)
        tokens=[lex(t) for t in re.sub(r'<n:[^>]+>',' ',b['text']).split()]
        keys=[re.sub(r'[^\w]','',t) for t in tokens]
        anchors=_geometry_anchors(local,list(range(len(local))),keys,body=b['role']=='body')
        owned=[local[i] for i,_ in anchors]
        for row in _pixel_lines(owned):rows.append((b['id'],b['role'],vision_reading.union([w['bbox'] for w in row])))
        if b['role']=='running_head' and not owned:
            # A separately labelled marginal title can have damaged optical
            # words. Its original ink still bounds the illustration's gap.
            # A title-role hint inside any authored figure seed cannot erase
            # diagram lettering or establish an interior crop boundary.
            hint=block_rect(b,display)
            marginal=hint[3]<r.height*.13 or hint[1]>r.height*.88
            outside=not any(hint[0]<s[2] and hint[2]>s[0] and hint[1]<s[3] and hint[3]>s[1]
                            for s in seeds.values())
            if marginal and outside and hint[3]-hint[1]<r.height*.05:
                ink=vision_pixels.ink_bounds(display,hint)
                if ink and ink[3]-ink[1]<r.height*.05:rows.append((b['id'],b['role'],ink))
        if b['role']=='folio' and re.fullmatch(r'\d+',b['text'].strip()) and not owned:
            # A text-free chart page can have no OCR at all. Its separately
            # labelled small marginal folio still has original printed ink.
            # Conversely, a shifted hint can bind an exact nearby optical
            # folio without borrowing a number from inside the diagram.
            hint=block_rect(b,display)
            top=hint[3]<r.height*.13
            bottom=hint[1]>r.height*.88
            if (top or bottom) and hint[3]-hint[1]<r.height*.05:
                near=[w for w in optical if lex(w['text'])==lex(b['text'])
                    and ((top and w['bbox'][3]<r.height*.13) or
                         (bottom and w['bbox'][1]>r.height*.88))
                    and abs((w['bbox'][1]+w['bbox'][3]-hint[1]-hint[3])/2)<2*(hint[3]-hint[1])
                    and abs((w['bbox'][0]+w['bbox'][2]-hint[0]-hint[2])/2)<2*(hint[3]-hint[1])
                    and not any(w['bbox'][0]<s[2] and w['bbox'][2]>s[0]
                                and w['bbox'][1]<s[3] and w['bbox'][3]>s[1]
                                for s in seeds.values())]
                box=min(near,key=lambda w:abs(w['bbox'][1]-hint[1]))['bbox'] if near else hint
                ink=vision_pixels.ink_bounds(display,box)
                if ink and ink[2]-ink[0]<r.width*.15 and ink[3]-ink[1]<r.height*.05:
                    rows.append((b['id'],b['role'],ink))
    # The detached separator below an original marginal title is furniture
    # too. A wide table's blank gap cannot claim that page rule merely because
    # its source hint is large. Authored rules inside artwork remain protected.
    for bid,role,box in tuple(rows):
        if role!='running_head' or box[3]>=r.height*.13:continue
        rule=vision_pixels.header_separating_rule(display,box)
        if rule and not any(rule[0]<s[2] and rule[2]>s[0] and
                            rule[1]<s[3] and rule[3]>s[1] for s in seeds.values()):
            rows.append((bid,role,rule))
    for i,b in enumerate(blocks):
        if b['role'] not in ('figure','table'):continue
        seed=seeds[b['id']];cx=(seed[0]+seed[2])/2;cy=(seed[1]+seed[3])/2
        x0,x1=r.width*.015,r.width*.985
        if r.width>=r.height*1.2:
            x0,x1=(x0,r.width/2-1) if cx<r.width/2 else (r.width/2+1,x1)
        fragments=vision_pixels.page_spine_fragments(display,seeds.values())
        fragments=[g for g in fragments if not any(g[0]<box[2] and g[2]>box[0]
            and g[1]<box[3] and g[3]>box[1] for _,_,box in rows)]
        gutters=vision_pixels.page_gutter_bounds(display)+fragments
        for gutter in gutters:
            if gutter[2]<seed[0]:x0=max(x0,gutter[2]+1)
            elif gutter[0]>seed[2]:x1=min(x1,gutter[0]-1)
        # Establish complete original component ownership before any text-gap
        # clipping. Corroborated prose components are excluded from ownership,
        # while connected artwork crossing their rows remains ambiguous.
        panel=[0,0,r.width,r.height]
        if r.width>=r.height*1.2 and not seed[0]<r.width/2<seed[2]:
            panel[0],panel[2]=(0,r.width/2) if cx<r.width/2 else (r.width/2,r.width)
        complete=vision_pixels.figure_ink_bounds(display,panel,seed,
            prose_rows=[box for _,_,box in rows]+gutters)
        def degrade(reason,territory=None,rejected=None):
            repairs.append({'block':b['id'],'seed_bbox':seed,
                'complete_bbox':complete,'rejected_bbox':rejected,
                'method':'whole_source_context','status':'DEGRADED_SOURCE_CONTEXT',
                'degraded':True,'reason':reason,'territory':territory})
        if complete is None:
            if vision_pixels.ink_bounds(display,panel) is None:
                # Entirely blank source pixels prove there is neither clipped
                # artwork nor foreign prose in this faithful empty region.
                repairs.append({'block':b['id'],'seed_bbox':seed,'bbox':seed,
                    'method':'verified_blank_source_region','territory':panel})
                continue
            degrade('Original artwork extent could not be established');continue
        # An illustration can sit beside a paragraph at the same height.
        # Corroborated rows beside its hint establish a horizontal barrier
        # before the vertical gap search; their prose never becomes figure ink.
        beside=[(bid,box) for bid,_,box in rows if box[1]<seed[3] and box[3]>seed[1]]
        left={bid for bid,box in beside if box[2]<seed[0]}
        right={bid for bid,box in beside if box[0]>seed[2]}
        # Neighboring lines can have different lengths. The same corroborated
        # paragraph's panel, rather than one row end, owns that horizontal gap.
        # Evaluate EVERY source-bound row of the identified paragraph, even
        # rows starting inside the coarse hint. Rows above/below the original
        # object have their own vertical positions, rather than imposing one
        # global paragraph edge on a full-width-to-wrapped transition.
        beside_art=lambda box:box[1]<complete[3] and box[3]>complete[1]
        x0=max([x0]+[box[2]+1 for bid,_,box in rows if bid in left and beside_art(box)])
        x1=min([x1]+[box[0]-1 for bid,_,box in rows if bid in right and beside_art(box)])
        local=[row for row in rows if row[2][0]<x1 and row[2][2]>x0]
        following=[]
        for c in blocks[i+1:]:
            if c['role'] in ('figure','table'):break
            following.append(c)
        caption=next((c for c in following if c['role']=='caption'),None)
        caption_rows=[row[2] for row in local if caption and row[0]==caption['id']]
        if caption_rows:
            ceiling=min(box[1] for box in caption_rows)-1
            floor=max((box[3]+1 for bid,role,box in local
                       if bid!=caption['id'] and box[3]<ceiling),default=r.height*.015)
        else:
            floor=max((box[3]+1 for _,_,box in local if box[3]<cy),default=r.height*.015)
            ceiling=min((box[1]-1 for _,_,box in local if box[1]>cy),default=r.height*.985)
        # Distinct objects in the same prose gap retain their own source
        # territories. Use the original hints, never a preceding repaired crop,
        # to place a boundary between their separate physical centers.
        for bid,other in seeds.items():
            if bid==b['id'] or other[0]>=seed[2] or other[2]<=seed[0]:continue
            other_y=(other[1]+other[3])/2
            if other_y<cy:
                edge=(other[3]+seed[1])/2 if other[3]<seed[1] else (other_y+cy)/2
                floor=max(floor,edge)
            elif other_y>cy:
                edge=(seed[3]+other[1])/2 if seed[3]<other[1] else (other_y+cy)/2
                ceiling=min(ceiling,edge)
        territory=[x0,floor,x1,ceiling]
        if ceiling-floor<4 or x1-x0<4:
            degrade('Insufficient source-proven text-free territory',territory);continue
        # A tidy clipped result cannot certify that the complete artwork fits.
        if any(box[0]<complete[2] and box[2]>complete[0] and
               box[1]<complete[3] and box[3]>complete[1] for _,_,box in rows):
            degrade('Complete original artwork overlaps corroborated live prose',territory,complete);continue
        if not (x0<=complete[0] and floor<=complete[1] and
                complete[2]<=x1 and complete[3]<=ceiling):
            degrade('Complete original artwork is outside the clean territory',territory,complete);continue
        bound=complete
        b['bbox']=[bound[0]/r.width*1000,bound[1]/r.height*1000,
                   bound[2]/r.width*1000,bound[3]/r.height*1000]
        regions[b['id']]=[bound]
        repairs.append({'block':b['id'],'seed_bbox':seed,'bbox':bound,
                        'method':'printed_text_gap_original_ink','territory':territory})
    return repairs


def _figure_source_context(display,pno,bid,package,image_records,source=None):
    """Keep an inseparable object whole in optional original-page evidence."""
    ident=f'v4-figure-source-{pno}-{bid}';ref='r-'+ident
    src=source or f'images/{ident}.jpg'
    if source is None:
        writer=getattr(package,'source_image',package.image)
        writer(src,package.evidence_jpeg(display,display.rect) if hasattr(package,'evidence_jpeg')
            else display.roi_jpeg(display.rect,scale=2,quality=90))
    image_records[src]=dict(page=pno,bbox=list(display.rect),kind='figure_source_reference')
    figure=(f'<figure><a id="{escape(ref,quote=True)}" class="source-disclosure-ref" '
        f'epub:type="noteref" href="#{escape(ident,quote=True)}">View figure in original page</a></figure>')
    context=(f'<aside id="{escape(ident,quote=True)}" class="footnote vision-disclosure" epub:type="footnote">'
        f'<p>Original page for this figure.</p><img src="{escape(src,quote=True)}" '
        f'alt="Whole original page containing the figure"/><a href="#{escape(ref,quote=True)}">Return</a></aside>')
    return figure,context,src,ident


def _recover_native_figures(unavailable,html_native,book,doc,package,images,evidence):
    """Recover source-known unavailable assets, preserving unrelated native flow."""
    by_page={}
    for figure in book.figures:by_page.setdefault(figure['pno'],[]).append(figure)
    cases=[];asides={}
    for src in dict.fromkeys(unavailable):
        controller=unit_recovery.current()
        def generated_metadata_failure(reason):
            # Filename/ordinal metadata is generated presentation, not original
            # PDF authority. Actual admitted fragments prove the SOURCE owners.
            # Preserve the entire proved owner set rather than guess one page
            # when a failed reference appears in more than one fragment.
            if controller is None:
                raise unit_recovery.SourceIntegrityError(reason)
            owners=sorted(n for n,fragment in html_native.items()
                          if src in build_epub._IMG_SRC.findall(fragment))
            units=[dict(page=n) for n in owners]
            controller.check_units(units)
            if doc is None or any(n<0 or n>=len(doc) for n in owners):
                raise unit_recovery.SourceIntegrityError(
                    'Original PDF unavailable for generated native figure owners')
            raise unit_recovery.UnitFailure(units,'native_figure_source_metadata',
                                           ValueError(reason))
        match=re.fullmatch(r'images/fig_p(\d+)_(\d+)\.jpg',src)
        if not match:generated_metadata_failure('Unidentifiable native figure source')
        pno,index=map(int,match.groups());objects=by_page.get(pno,[])
        if controller and pno in controller.source_pages():continue
        if pno not in html_native or index>=len(objects):
            generated_metadata_failure('Unknown native figure source identity')
        bid=f'native-figure-source-{index}' if controller else f'native-{index}'
        if controller and bid not in controller.blocked(pno):
            # This failure owns the SOURCE asset, not the figure's existing
            # raster prose carrier. Fresh replay preserves that carrier until
            # the independent original-pixel context is published below.
            raise unit_recovery.UnitFailure([dict(page=pno,block=bid)],'native_figure_unavailable',
                ValueError('Source-bound native figure asset unavailable: '+src))
        with unit_recovery.guard(pno,'native_figure_context',block=bid):
            display=SourceDisplay(doc,pno)
            source=evidence.get(pno,{}).get('full')
            figure,context,image,target=_figure_source_context(display,pno,bid,package,images,source)
            # Original inspection navigation may already wrap this exact image.
            # Keep the authored caption and the outer figure, replacing only that
            # source-known presentation and its generated inspection wrapper.
            pattern=(r'<figure\b[^>]*>\s*(?:<a\b[^>]*>\s*)?'
                r'<img\b[^>]*src="'+re.escape(src)+r'"[^>]*/?>\s*(?:</a>)?')
            replacement=figure[:-len('</figure>')]
            updated,count=re.subn(pattern,lambda m:replacement,html_native[pno])
            if count==0:
                # Some existing native presentations are source atoms containing
                # uncertain prose as well as artwork. Preserve that original BODY
                # region once; optional whole-page context cannot replace it.
                carrier=(r'<p\b[^>]*>\s*<span\b[^>]*class="source-raster"[^>]*>'
                    r'\s*(?:<a\b[^>]*>\s*)?<img\b[^>]*src="'+re.escape(src)+
                    r'"[^>]*/?>\s*(?:</a>\s*)?</span>\s*</p>')
                matches=list(re.finditer(carrier,html_native[pno]))
                if len(matches)==1:
                    box=list(display.reading_rect(objects[index]['bbox']))
                    retained=f'images/v4-native-source-atom-{pno}-{index}.jpg'
                    package.image(retained,package.evidence_jpeg(display,box) if hasattr(package,'evidence_jpeg')
                        else display.roi_jpeg(box,scale=2,quality=90))
                    images[retained]=dict(page=pno,bbox=box,kind='native_source_raster_reference')
                    updated,count=re.subn(carrier,lambda m:m.group(0).replace(src,retained)+figure,
                        html_native[pno])
            if count!=1:raise ValueError('Native figure reference is not uniquely source-bound')
            html_native[pno]=updated;asides.setdefault(pno,[]).append(context)
            case=dict(page=pno,block=bid,seed_bbox=list(objects[index]['bbox']),
                reason='Native figure asset unavailable',status='DEGRADED_SOURCE_CONTEXT',
                source_reference=target,source_image=image,source_reference_origin='r-'+target)
            if controller:
                case.update(controller.blocked(pno)[bid],source_reference=target,source_image=image)
                controller.degraded[(pno,bid)].update(case)
            cases.append(case)
    return cases,asides


def _source_header_ownership(scans):
    """A proved source header family can correct a mistaken HEADING label.

    Matching printed row geometry and separating rules matter, never the title
    wording. Authored large titles, subsections, notes and artwork retain their
    ownership. Unsupported marginal text remains unchanged.
    """
    import statistics
    from . import vision_pixels
    candidates=[]
    for n,s in scans.items():
        with unit_recovery.guard(n,'source_header_ownership'):
            blocks=s['answer']['blocks'];r=s['display'].rect
            protected=[block_rect(b,s['display']) for b in blocks
                       if b['role'] in ('figure','table','caption','footnote')]
            for b in blocks:
                if b['role'] not in ('heading','running_head') or b.get('marker'):continue
                hint=block_rect(b,s['display'])
                if hint[3]>=r.height*.13 or not 0<len(b['text'].split())<=12:continue
                words=_owned_block_words(b,s['answer'],s['optical'],s['display'],s['regions'],ordered=True)
                keys=[re.sub(r'[^\w]','',vision_reading.normalized(t).casefold()) for t in b['text'].split()]
                anchors=_geometry_anchors(words,list(range(len(words))),keys)
                if not anchors or len({j for _,j in anchors})<len(keys)*.6:continue
                owned=[words[i] for i,_ in anchors]
                rows=_pixel_lines(owned)
                if len(rows)!=1:continue
                box=vision_reading.union([w['bbox'] for w in owned])
                if any(box[0]<p[2] and box[2]>p[0] and box[1]<p[3] and box[3]>p[1] for p in protected):continue
                size=statistics.median(w['bbox'][3]-w['bbox'][1] for w in owned)
                rule=vision_pixels.header_separating_rule(s['display'],box)
                if not rule or any(rule[0]<p[2] and rule[2]>p[0] and rule[1]<p[3] and rule[3]>p[1] for p in protected):continue
                left,right=(0.,r.width)
                if r.width>=r.height*1.2:
                    left,right=(0.,r.width/2) if (box[0]+box[2])/2<r.width/2 else (r.width/2,r.width)
                body=[w for other in blocks if other['role']=='body'
                      for w in _owned_block_words(other,s['answer'],s['optical'],s['display'],s['regions'],ordered=True)
                      if left<=(w['bbox'][0]+w['bbox'][2])/2<=right and w['bbox'][1]>box[3]]
                if not body or min(w['bbox'][1] for w in body)<rule[3]+size*1.5:continue
                candidates.append(dict(page=n,block=b,scan=s,box=box,rule=rule,size=size,
                    y=(box[1]+box[3])/2/r.height,h=size/r.height,
                    x=((box[0]+box[2])/2-left)/(right-left),ry=rule[1]/r.height))
    seeds=[c for c in candidates if c['block']['role']=='running_head']
    def matches(a,b):
        return (.8*b['h']<=a['h']<=1.2*b['h'] and abs(a['y']-b['y'])<=b['h']*.6
                and abs(a['ry']-b['ry'])<=b['h']*.6 and abs(a['x']-b['x'])<=.15)
    for candidate in candidates:
        with unit_recovery.guard(candidate['page'],'source_header_family', block=candidate['block']['id']):
            if candidate['block']['role']!='heading':continue
            family=[seed for seed in seeds if matches(candidate,seed)]
            support=sorted({seed['page'] for seed in family})
            if len(support)<3:continue
            candidate['block']['role']='running_head'
            candidate['scan']['geometry_repairs'].append(dict(block=candidate['block']['id'],
                method='source_margin_rule_header_family',source_bbox=candidate['box'],
                source_rule_bbox=candidate['rule'],support_pages=support))


def _corroborate_note_prefix(display,prefixes,label,cache):
    """Read the complete original label before treating an OCR fragment as a veto."""
    if not re.fullmatch(r'\d{1,4}',label):return None
    from . import pixel_adjudication as pa
    bounds=vision_reading.union([w['bbox']for w in prefixes])
    if not (0<bounds[2]-bounds[0]<=80 and 0<bounds[3]-bounds[1]<=40):return None
    occurrence=dict(bbox=bounds)
    normalize=lambda text:re.sub(r'^[\s([.]+|[\s).\]]+$','',text)
    from . import native_text
    native=[dict(text=w[4],bbox=list(w[:4]))for w in display.page.get_text('words')]
    candidates=[]
    for word,proved in zip(native,native_text.drawn_word_authorities(display.page,native)):
        box=list(display.reading_rect(word['bbox']))
        # A smaller native fragment is not proof of the complete printed label.
        overlap=max(0,min(box[2],bounds[2])-max(box[0],bounds[0]))
        if (proved and overlap>=(bounds[2]-bounds[0])*.8
                and pa._complete_word_occurrence(occurrence,dict(bbox=box))):
            candidates.append(dict(text=word['text'],bbox=box))
    if candidates:
        if len(candidates)!=1 or normalize(candidates[0]['text'])!=label:return None
        return dict(label=label,original_readings=[w['text']for w in prefixes],
            source_bbox=list(bounds),method='visible_native_note_prefix')
    if cache is None:return None
    witness=pa.read_crop(display,[bounds],cache)
    def owned(view,confidence):
        words=[w for w in view if pa._complete_word_occurrence(occurrence,w)]
        return (words[0]if len(words)==1 and words[0].get('confidence',0)>=confidence
                and normalize(words[0]['text'])==label else None)
    primary=owned(witness,85)
    alternatives=getattr(witness,'alternates',None)
    if primary is None or alternatives is None:return None
    views=alternatives()[:2]
    if len(views)!=2:return None
    secondary=[owned(view,90)for view in views]
    if not all(secondary):return None
    return dict(label=label,original_readings=[w['text']for w in prefixes],
        source_bbox=list(bounds),method='complete_original_note_prefix_pixel_agreement',
        english_confidence=primary['confidence'],
        latin_confidences=[w['confidence']for w in secondary])


def _note_ownership(scans):
    """Establish note continuations and marginal furniture across selected pages.

    The catalogue is finite to this conversion. A chapter heading is a barrier;
    an ordinary body page with a footer note is never a note section by majority
    alone. Continuation ownership preserves every source block and page.
    """
    import statistics
    _source_header_ownership(scans)
    repeated={};owners={};last=None;last_note=None;previous_pno=None;section=False;note_size=None;previous_body_size=None
    controller=unit_recovery.current()
    def selected_blocks(n,s):
        blocked=controller.blocked(n) if controller else {}
        return [b for b in s['answer']['blocks']
                if not (b['role']=='footnote' and b['id'] in blocked)]
    def ink_size(s,roles):
        heights=[w['bbox'][3]-w['bbox'][1] for b in selected_blocks(n,s) if b['role'] in roles
                 for w in _owned_block_words(b,s['answer'],s['optical'],s['display'],s['regions'],ordered=True)
                 if re.search(r'[A-Za-z]',w['text'])]
        return statistics.median(heights) if heights else None
    for n,s in scans.items():
        with unit_recovery.guard(n,'note_ownership'):
            for b in selected_blocks(n,s):
                box=block_rect(b,s['display']);text=re.sub(r'\W+',' ',b['text']).strip().casefold()
                if text and len(text.split())<=12 and (box[1]<s['display'].rect.height*.13 or box[1]>s['display'].rect.height*.88):
                    repeated.setdefault(text,set()).add(n)
    for n,s in scans.items():
        with unit_recovery.guard(n,'note_ownership'):
            blocks=selected_blocks(n,s);r=s['display'].rect
            if previous_pno is None or n!=previous_pno+1:
                last=None;last_note=None;section=False;note_size=None;previous_body_size=None
            footer=last_note if last_note and last_note[0]==n-1 else None
            note_words=sum(len(b['text'].split()) for b in blocks if b['role']=='footnote')
            other_words=sum(len(b['text'].split()) for b in blocks if b['role'] in ('body','other'))
            title=any(re.fullmatch(r'(?:footnotes|endnotes|notes)[.:]?',b['text'].strip(),re.I)
                      and block_rect(b,s['display'])[1]<r.height*.2 for b in blocks)
            body_size=ink_size(s,('body','other'));current_note_size=ink_size(s,('footnote',))
            if section and not title and body_size and note_size and body_size>note_size*1.2:
                section=False;last=None
            # Footer notes can dominate a short, illustrated body's word count.
            # A new unheaded note section also needs notes outside the footer.
            interior_notes=any(b['role']=='footnote' and block_rect(b,s['display'])[1]<r.height*.65 for b in blocks)
            section=title or (interior_notes and note_words>other_words and sum(b['role']=='footnote' for b in blocks)>=2) or section
            if current_note_size:note_size=current_note_size
            current=last if section else None
            for b in blocks:
                box=block_rect(b,s['display']);text=re.sub(r'\W+',' ',b['text']).strip().casefold()
                marginal=box[1]<r.height*.13 or box[1]>r.height*.88
                printer=(re.search(r'\bprinted\s+(?:by|at|in)\b|\bprinters\b',b['text'],re.I)
                         and len(b['text'].split())<=20 and b['role'] in ('body','other'))
                if printer and section and note_size:
                    ink=_owned_block_words(b,s['answer'],s['optical'],s['display'],s['regions'],ordered=True)
                    size=statistics.median(w['bbox'][3]-w['bbox'][1] for w in ink) if ink else None
                    centered=abs((box[0]+box[2])/2-r.width/2)<r.width*.2
                    if size and size<note_size*.8 and centered:
                        b['role']='running_head';continue
                # Repetition cannot override a source-bound structural heading or
                # labelled note. Chapter titles often recur as running heads; the
                # explicit heading still establishes its paragraph/note barrier.
                repeat_furniture=(b['role'] not in ('footnote','heading') and len(repeated.get(text,()))>=2 and
                                  (b['role']!='body' or box[3]<r.height*.08 or box[1]>r.height*.94))
                if marginal and (repeat_furniture or
                        (b['role']!='heading' and re.fullmatch(r'\d+',b['text'].strip())) or
                        re.match(r'^printed\s+(?:by|in|at)\b',b['text'],re.I) or
                        (title and text in ('notes','footnotes','endnotes'))):
                    b['role']='folio' if re.fullmatch(r'\d+',b['text'].strip()) else 'running_head'
                    continue
                if b['role']=='heading':current=None;last=None;footer=None;last_note=None;section=False;continue
                # Footer carry survives the ordinary main paragraph above it. Both
                # source fonts and unfinished printed wording establish ownership;
                # a new label, completed sentence or full-size body is a barrier.
                if footer and not b['marker'] and b['role'] in ('body','other','footnote') and box[1]>r.height*.65:
                    prior_n,prior_id,prior_owner=footer
                    prior=scans[prior_n]
                    prior_block=next(x for x in prior['answer']['blocks'] if x['id']==prior_id)
                    before=_owned_block_words(prior_block,prior['answer'],prior['optical'],prior['display'],prior['regions'],ordered=True)
                    after=_owned_block_words(b,s['answer'],s['optical'],s['display'],s['regions'],ordered=True)
                    prior_size=statistics.median(w['bbox'][3]-w['bbox'][1] for w in before) if before else None
                    next_size=statistics.median(w['bbox'][3]-w['bbox'][1] for w in after) if after else None
                    ordinary=max(body_size or 0,previous_body_size or 0)
                    small=(prior_size and next_size and .7*prior_size<=next_size<=1.2*prior_size
                           and ordinary and next_size<ordinary*.85)
                    lexical=assemble.continues(' '.join(w['text'] for w in before),' '.join(w['text'] for w in after))
                    if small and lexical and block_rect(prior_block,prior['display'])[1]>prior['display'].rect.height*.65:
                        b['role']='footnote';b['marker']='';current=prior_owner
                        b['continues_previous']=b['continues_next']=False
                if b['role'] in ('figure','other') and not b['marker'] and box[1]>r.height*.88 and body_size:
                    ink=owned_words(s['optical'],box)
                    # A source-sized isolated footer glyph is a gathering signature
                    # or folio, even when its reading calls it a tiny illustration.
                    # Connected diagrams, internal labels and captions remain art.
                    if len(ink)==1 and re.fullmatch(r'[A-Za-z0-9°○]{1,3}',ink[0]['text']):
                        glyph=ink[0]['bbox'];size=glyph[3]-glyph[1]
                        prose=[w for x in blocks if x['role']=='body'
                               for w in _owned_block_words(x,s['answer'],s['optical'],s['display'],s['regions'],ordered=True)
                               if w is not ink[0] and w['bbox'][0]<glyph[2]+body_size and w['bbox'][2]>glyph[0]-body_size]
                        body_bottom=max((w['bbox'][3] for w in prose),default=0)
                        caption=any(x['role']=='caption' and block_rect(x,s['display'])[1]>=box[1]
                                    for x in blocks)
                        if prose and size<body_size*.85 and glyph[2]-glyph[0]<3*body_size and not caption:
                            from . import vision_pixels
                            bounds=vision_pixels.ink_bounds(s['display'],box)
                            # Native/font boxes can extend below the actual last
                            # prose row. Establish the footer's white separation
                            # from that row's original ink, not its descent space.
                            last_row=vision_reading.union([w['bbox'] for w in prose
                                if w['bbox'][3]>=body_bottom-body_size*.5])
                            prose_bounds=vision_pixels.ink_bounds(s['display'],last_row)
                            if bounds and prose_bounds and glyph[1]>prose_bounds[3]+body_size*.25 and (bounds[0]>=glyph[0]-1.5 and bounds[1]>=glyph[1]-1.5
                                           and bounds[2]<=glyph[2]+1.5 and bounds[3]<=glyph[3]+1.5):
                                b['role']='running_head';continue
                if b['role']=='footnote':
                    if b['marker'] or current is None:
                        current=(n,b['id']);owners[current]=current
                    else:owners[(n,b['id'])]=current
                    last=current;last_note=(n,b['id'],current)
                elif b['role'] in ('body','other') and current:
                    previous=next((x for x in reversed(blocks[:blocks.index(b)])
                                   if x['role'] not in ('running_head','folio')),None)
                    contiguous=(previous and previous['role']=='footnote' and
                                box[1]-block_rect(previous,s['display'])[3]<max(18,r.height*.06)
                                and box[1]>r.height*.65)
                    if section or contiguous:
                        b['role']='footnote';b['marker']='';b['continues_previous']=b['continues_next']=False
                        owners[(n,b['id'])]=current;last=current;last_note=(n,b['id'],current)
                    else:current=None
            previous_pno=n;previous_body_size=body_size
    catalogue={};labels={}
    for n,s in scans.items():
        with unit_recovery.guard(n,'note_ownership'):
            for b in selected_blocks(n,s):
                if b['role']!='footnote':continue
                key=(n,b['id']);owner=owners.get(key,key)
                label=re.sub(r'^[\s(.]+|[\s).]+$','',b['marker'])
                if owner==key:
                    # Content anchors can exclude a printed label when the model's
                    # marker field is wrong. Check the adjacent opening ink before
                    # allowing that field to define the finite note catalogue.
                    if label:
                        local=_owned_block_words(b,s['answer'],s['optical'],s['display'],s['regions'],ordered=True)
                        if local:
                            first=local[0];box=first['bbox'];height=box[3]-box[1]
                            prefixes=[w for w in s['optical']
                                if w.get('confidence',0)>=80
                                and re.fullmatch(r'[([]?(?:\d{1,4}|[*†‡])[.)\]]?',w['text'])
                                and abs((w['bbox'][1]+w['bbox'][3]-box[1]-box[3])/2)<height*.55
                                and (w is first or 0<=box[0]-w['bbox'][2]<3*height)]
                            if any(re.sub(r'^[\s([.]+|[\s).\]]+$','',w['text'])!=label for w in prefixes):
                                proof=_corroborate_note_prefix(s['display'],prefixes,label,s.get('pixel_cache'))
                                if proof is None:
                                    raise ValueError('Selected note marker disagrees with its printed source prefix')
                                s.setdefault('source_note_marker_verifications',{})[b['id']]=dict(proof,block=b['id'])
                    catalogue[owner]={'label':label or '*','target':f'v4-note-{n}-{b["id"]}'}
                    if label:labels.setdefault(label,[]).append(owner)
    return owners,catalogue,labels


class _LiteralNoteRefs(dict):
    """Reference spans with independently bounded original glyph instances."""
    def __init__(self):
        super().__init__()
        self.source_instances={}
        self.sole_source_instances=set()


def _sole_source_note_instance(display,optical,box,glyph):
    """Require original terminal-row ink, not OCR cardinality, for collapse.

    Unexplained ink outside the preceding lexical word keeps the second action.
    Inside that word's possibly overbroad optical box, a second matching original
    glyph also prevents collapse. This only measures original pixels.
    """
    from . import vision_pixels
    from PIL import Image
    height=max(1,glyph[3]-glyph[1])
    row=[w for w in optical if re.search(r'[A-Za-z]',w['text']) and
         min(w['bbox'][3],glyph[3])-max(w['bbox'][1],glyph[1])>0]
    preceding=[w for w in row if w['bbox'][0]<glyph[0]]
    if not preceding:return False
    word=max(preceding,key=lambda w:w['bbox'][0]);b=word['bbox']
    following=[w['bbox'][0] for w in row if w['bbox'][0]>glyph[2]]
    # A lexical binding may itself have shrunk around an omitted glyph. Inspect
    # the original row through its next lexical word or the physical leaf edge.
    right=min(following,default=display.rect.x1)
    territory=[b[0],min(b[1],glyph[1])-.5,right-.1,
               max(b[3],glyph[3])+.5]
    if territory[2]<=glyph[2]:return False
    pix=display.pixmap(territory,scale=6,max_pixels=200_000)
    picture,components=vision_pixels._word_ink_components(pix)
    sx=(territory[2]-territory[0])/picture.width
    sy=(territory[3]-territory[1])/picture.height
    def source(c):
        return [territory[0]+c[0]*sx,territory[1]+c[1]*sy,
                territory[0]+c[2]*sx,territory[1]+c[3]*sy]
    selected=[c for c in components if
              min(source(c)[2],glyph[2])-max(source(c)[0],glyph[0])>0 and
              min(source(c)[3],glyph[3])-max(source(c)[1],glyph[1])>0]
    if len(selected)!=1:return False
    target=selected[0];mask=picture.point(lambda v:255 if v<180 else 0)
    template=mask.crop(target)
    for component in components:
        if component==target:continue
        s=source(component)
        # No optical observation is required to see another original component
        # after the lexical word or the independently bounded reference.
        if s[0]>=min(b[2],glyph[2])-.1:return False
        if glyph[0]-s[2]>height*2:continue
        width=component[2]-component[0];h=component[3]-component[1]
        tw,th=template.size
        if not (.7<=width/tw<=1.4 and .7<=h/th<=1.4):continue
        other=mask.crop(component).resize(template.size,Image.Resampling.NEAREST)
        a=list(template.getdata());z=list(other.getdata())
        smaller=min(sum(bool(x) for x in a),sum(bool(x) for x in z))
        overlap=sum(bool(x and y) for x,y in zip(a,z))
        # Subpixel raster phases can change a stroke's thickness. Containment
        # of the smaller ink silhouette is the conservative ambiguity veto.
        if smaller and overlap/smaller>=.85:return False
    return True


def _adjacent_source_note_glyphs(owned,source):
    # Lexical paragraph binding can exclude a separate printed reference glyph.
    # Admit only adjacent original symbol instances for marker correspondence;
    # these never change the paragraph's words, geometry or reading confidence.
    result=list(owned);seen={id(w) for w in owned}
    lexical=[w for w in owned if re.search(r'[A-Za-z]',w['text'])]
    for word in source:
        if id(word) in seen or not re.fullmatch(r'[*†‡]',word['text']):continue
        b=word['bbox']
        for neighbour in lexical:
            a=neighbour['bbox'];height=max(1,a[3]-a[1])
            if (0<=b[0]-a[2]<height*1.2 and
                abs((a[1]+a[3]-b[1]-b[3])/2)<height*.55):
                result.append(word);seen.add(id(word));break
    return result


def _numeric_original_marker_role(display, lexical_box, glyph):
    """Prove a numeric reference is smaller AND raised in original ink.

    A matching printed character or note owner proves identity, never role.
    Independently measure lexical ink left of the glyph; unavailable or ordinary
    baseline ink stays unproved and retains its literal BODY representation.
    """
    from . import vision_pixels
    right=min(lexical_box[2],glyph[0]-max(.5,(lexical_box[3]-lexical_box[1])*.08))
    territory=[lexical_box[0],min(lexical_box[1],glyph[1])-.5,
               right,max(lexical_box[3],glyph[3])+.5]
    if territory[2]<=territory[0]:return False
    ink=vision_pixels.ink_bounds(display,territory)
    if not ink:return False
    height=ink[3]-ink[1];glyph_height=glyph[3]-glyph[1]
    return bool(height>0 and 0<glyph_height<=height*.85
        and (glyph[1]+glyph[3])/2<=(ink[1]+ink[3])/2-height*.2
        and glyph[3]<=ink[3]-height*.2)


def _literal_note_refs(tokens,optical,display,box,cache,labels,pno,context,allow_omitted=False):
    """Map source-proven raised sub-tokens to unique finite book note owners."""
    from . import vision_pixels
    bare_pattern=(r'(?<=[A-Za-z])\d{1,4}(?=[.,;:]?$)|'
                  r'''(?<=[A-Za-z.,;:!?'’"”)\]])[*†‡](?=[.,;:)\]”’"']*$)|^[*†‡]$''')
    omitted_labels={label for label,owners in labels.items()
                    if label in ('*','†','‡') and any(owner[0]==pno for owner in owners)} if allow_omitted else set()
    if not labels or not (omitted_labels or any(re.search(r'\(\d{1,4}\)|'+bare_pattern,t) for t in tokens)):return {}
    candidates=vision_pixels.raised_markers(display,box,cache)
    import statistics
    # Standalone optical marker glyphs sometimes disappear into the preceding
    # letter in character OCR. Their smaller, raised ORIGINAL ink can still
    # establish the reference without trusting a whole-word height alone.
    for w in optical:
        if omitted_labels and not any(symbol in w['text'] for symbol in ('*','†','‡')):
            closing=re.search(r'''[A-Za-z]\.([)\]”’"']+)$''',w['text'])
            if closing:
                ink=vision_pixels.unrecognized_terminal_symbol_ink(display,w['bbox'],closing[1])
                if ink:candidates.append(dict(label=None,bbox=ink,kind='bare',method='unrecognized_terminal_original_ink'))
        terminal=re.search(r'''([*†‡])([)\]”’"']*)$''',w['text'])
        if terminal and len(re.sub(r'[^A-Za-z]','',w['text'][:terminal.start()]))>=2:
            label=terminal[1]
            if len([owner for owner in labels.get(label,[]) if owner[0]==pno])==1:
                ink=vision_pixels.terminal_symbol_ink(display,w['bbox'],closing_punctuation=terminal[2])
                if ink:candidates.append(dict(label=label,bbox=ink,kind='bare',method='corroborated_terminal_symbol_original_ink'))
        if re.fullmatch(r'\d{1,4}|[*†‡]',w['text']):
            b=w['bbox'];neighbours=[v for v in optical if re.search(r'[A-Za-z]',v['text'])
                and abs(v['bbox'][1]-b[1])<4 and 0<=b[0]-v['bbox'][2]<30]
            if not neighbours:continue
            baseline=statistics.median(v['bbox'][3] for v in neighbours)
            height=statistics.median(v['bbox'][3]-v['bbox'][1] for v in neighbours)
            selected_symbol=(w['text'] in ('*','†','‡') and
                len([owner for owner in labels.get(w['text'],[]) if owner[0]==pno])==1)
            if selected_symbol or b[3]<baseline-height*.18 and b[3]-b[1]<height*.8:
                ink=vision_pixels.ink_bounds(display,b)
                if ink:candidates.append({'label':w['text'],'bbox':ink,'kind':'bare','method':'raised_original_glyph_ink'})
        elif '(' in w['text'] and ')' in w['text']:
            b=w['bbox'];height=b[3]-b[1]
            neighbours=[v['bbox'] for v in optical if v is not w
                and re.fullmatch(r"[A-Za-z]+(?:[-‐‑–—'’][A-Za-z]+)*[.,;:]?",v['text'])
                and v['bbox'][1]<b[3] and v['bbox'][3]>b[1]
                and max(b[0]-v['bbox'][2],v['bbox'][0]-b[2],0)<4*height]
            evidence=vision_pixels.parenthetical_ink(display,b,neighbours)
            if evidence:
                existing=[v for v in candidates if abs((v['bbox'][0]+v['bbox'][2])/2-
                                                       (evidence['bbox'][0]+evidence['bbox'][2])/2)<4]
                if existing:
                    # Character OCR can recognize only the first digit of a
                    # multi-digit superscript. A verification request needs
                    # the whole independently bounded label, not that fragment.
                    for v in existing:v['bbox']=evidence['bbox']
                else:candidates.append(dict(evidence,label=None))
    refs=_LiteralNoteRefs()
    # Bind marker positions to optical word geometry; choose the corresponding
    # parenthetical sub-token even when the model or optical digit is wrong.
    parens=[(i,m) for i,t in enumerate(tokens) for m in re.finditer(r'\(\d{1,4}\)',t)]
    bare=[(i,m) for i,t in enumerate(tokens) for m in re.finditer(bare_pattern,t)
          if len(re.sub(r'[^A-Za-z]','',t[:m.start()]))>=2 or
          (m[0] in ('*','†','‡') and m.start()==0 and len(t)==1 and i>0
           and len(re.sub(r'[^A-Za-z]','',tokens[i-1]))>=2)]
    from difflib import SequenceMatcher
    skeleton=lambda t:re.sub(r'\([^()]{1,4}\)','()',vision_reading.normalized(t).casefold())
    mapping={}
    for a,b,count in SequenceMatcher(None,[skeleton(w['text']) for w in optical],
                                    [skeleton(t) for t in tokens],autojunk=False).get_matching_blocks():
        mapping.update((b+i,optical[a+i]) for i in range(count))
    # An unknown full-height symbol can look like an ordinary baseline letter
    # to both optical recognizers. A placeholder does not prove its identity:
    # two exact lexical anchors only establish a provisional ORIGINAL glyph
    # slot, which still needs the independent bounded label reader below.
    source=context.get('source_words') or optical
    lexical_key=lambda s:re.sub(r'[^a-z0-9]','',vision_reading.normalized(s).casefold())
    for i,m in bare:
        expected=m[0]
        if expected not in ('*','†','‡') or i+2>=len(tokens):continue
        marker=re.fullmatch(r'<n:([^>]+)>',tokens[i+1])
        if not marker:continue
        owner=context.get('owners',{}).get((pno,marker[1]),(pno,marker[1]))
        options=labels.get(expected,[]);local=[key for key in options if key[0]==pno]
        if (local or options)!=[owner]:continue
        before_key=lexical_key(tokens[i][:m.start()]);after_key=lexical_key(tokens[i+2])
        if len(before_key)<2 or len(after_key)<2:continue
        pairs=[]
        for before in source:
            if lexical_key(before['text'])!=before_key:continue
            a=before['bbox']
            for after in source:
                if lexical_key(after['text'])!=after_key:continue
                b=after['bbox'];height=max(a[3]-a[1],b[3]-b[1],1)
                if (0<b[0]-a[2]<=height*3 and
                    min(a[3],b[3])-max(a[1],b[1])>0):pairs.append((a,b))
        if len(pairs)!=1:continue
        a,b=pairs[0]
        slot=[w for w in source if len(w['text'].strip())==1 and
              a[2]<=w['bbox'][0] and w['bbox'][2]<=b[0] and
              min(w['bbox'][3],a[3],b[3])-max(w['bbox'][1],a[1],b[1])>0]
        if len(slot)!=1 or slot[0]['text'] in ('*','†','‡'):continue
        ink=vision_pixels.ink_bounds(display,slot[0]['bbox'])
        if ink:candidates.append(dict(label=None,bbox=ink,kind='bare',
            method='two_exact_source_anchors_unknown_glyph',provisional=True))
    def same_instance(a,b):
        overlap=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
        area=min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1]))
        return area>0 and overlap>=area*.5
    for candidate in candidates:
        cx=(candidate['bbox'][0]+candidate['bbox'][2])/2;cy=(candidate['bbox'][1]+candidate['bbox'][3])/2
        choices=[]
        for i,m in (bare if candidate.get('kind')=='bare' else parens):
            word=mapping.get(i)
            if word is None:
                prefix=tokens[i][:m.start()];suffix=tokens[i][m.end():]
                key=re.sub(r'\W','',vision_reading.normalized(prefix).casefold())
                if key:
                    matches=[w for w in optical if re.sub(r'\W','',vision_reading.normalized(w['text']).casefold())==key]
                    word=min(matches,key=lambda w:abs(w['bbox'][2]-cx)+abs((w['bbox'][1]+w['bbox'][3])/2-cy)) if matches else None
                    if word:
                        # OCR may split the raised digit into a second word.
                        near=[w for w in optical if 0<=w['bbox'][0]-word['bbox'][2]<8
                              and abs(w['bbox'][1]-word['bbox'][1])<8]
                        if near:word={'bbox':vision_reading.union([word['bbox']]+[w['bbox'] for w in near])}
            if word:
                b=word['bbox'];distance=max(b[0]-cx,0,cx-b[2])+abs((b[1]+b[3])/2-cy)
                if distance<max(8,b[3]-b[1])*2:choices.append((distance,i,m,list(b)))
        if not choices:continue
        _,i,m,lexical_box=min(choices,key=lambda x:x[0]);label=candidate['label']
        expected=tokens[i][m.start():m.end()].strip('()')
        instance_box=candidate['bbox']
        if (candidate.get('kind')=='bare' and expected.isdigit()
                and not _numeric_original_marker_role(display,
                    lexical_box,instance_box)):
            continue
        if expected in ('*','†','‡'):
            # Agreement on the surrounding word cannot let its quotation mark
            # or punctuation borrow a terminal symbol's identity. Locate that
            # independently corroborated glyph in the original ink first.
            symbols=[]
            for word in optical:
                if word['text']==expected:
                    ink=vision_pixels.ink_bounds(display,word['bbox'])
                elif (suffix:=re.search(re.escape(expected)+r'''([)\]”’"']*)$''',word['text'])):
                    ink=vision_pixels.terminal_symbol_ink(display,word['bbox'],closing_punctuation=suffix[1])
                else:continue
                if ink:symbols.append(ink)
            if symbols and not any(b[0]-.5<=cx<=b[2]+.5 and b[1]-.5<=cy<=b[3]+.5
                                   for b in symbols):continue
            if symbols:
                instance_box=min(symbols,key=lambda b:
                    abs((b[0]+b[2])/2-cx)+abs((b[1]+b[3])/2-cy))
        # Repeated recognizer observations of an already verified original
        # glyph do not require another label request. A distinct neighbouring
        # glyph still passes through the full verification below.
        if any(a==m.start() and b==m.end() and label==expected and
               any(same_instance(instance_box,old) for old in
                   refs.source_instances.get((i,a,b,owner,label),[]))
               for a,b,owner,label in refs.get(i,[])):continue
        if expected in ('*','†','‡') and label!=expected:
            # A full-height dagger can resemble '+' in character recognition.
            # Exact nearby optical symbol agreement is independent corroboration;
            # otherwise the existing bounded original-glyph reader verifies it.
            if any(expected in w['text'] and
                   w['bbox'][0]-3<=cx<=w['bbox'][2]+3 and w['bbox'][1]-3<=cy<=w['bbox'][3]+3
                   for w in optical):label=expected
        corroborated_local_symbol=bool(expected in ('*','†','‡') and symbols
            and label==expected and len([owner for owner in labels.get(expected,[]) if owner[0]==pno])==1)
        selected_source_label=label in labels
        tiny=candidate['bbox'][3]-candidate['bbox'][1]<=3.2
        if label is None or label!=expected or tiny:
            marker_reader=context.get('marker_reader')
            if marker_reader:
                crop=list(candidate['bbox']);crop[0]-=1;crop[1]-=1;crop[2]+=1;crop[3]+=1
                crop=list(extract.pymupdf.Rect(crop)&display.rect)
                try:
                    receipt=marker_reader(display.roi_jpeg(crop,scale=12,quality=95))
                except vision_reading.model.ModelError as error:
                    if not (candidate.get('provisional') and
                            'Printed marker absent from offline cache' in str(error)):raise
                    context['marker_receipts'].setdefault(pno,[]).append(dict(
                        label='',uncertain=True,deferred=True,request_dispatched=False,
                        new_cost_usd=0.,cached=False,
                        reason='provisional_glyph_not_verified_in_offline_cache',
                        source_bbox=crop,block_token=i))
                    continue
                context['marker_receipts'].setdefault(pno,[]).append(dict(receipt,source_bbox=crop,block_token=i,
                    original_frame=dict(display.provenance.get('original_frame') or {})))
                if receipt['uncertain'] or not receipt['label']:
                    if candidate.get('provisional'):continue
                    raise ValueError('Source-proven printed note reference has an uncertain label')
                label=receipt['label']
                if candidate.get('provisional') and label!=expected:continue
                if (corroborated_local_symbol and label!=expected and labels.get(label) and
                        not any(owner[0]==pno for owner in labels.get(label,[]))):
                    # A tiny glyph answer cannot move a literal, original-ink-
                    # corroborated local reference to another page's different
                    # symbol. Retain the actual answer and report its rejection;
                    # correctly verified local alternatives still take effect.
                    context['marker_receipts'][pno][-1].update(
                        binding_label=expected,verification_usable_for_owner=False,
                        binding_reason='compact_label_conflicts_with_local_source_correspondence')
                    label=expected
        options=labels.get(label,[])
        if not options:
            if selected_source_label:
                raise ValueError('Source-proven selected note reference has an unknown label')
            continue
        local=[key for key in options if key[0]==pno];options=local or options
        if len(options)!=1:raise ValueError('Ambiguous source-proven note reference: '+label)
        value=(m.start(),m.end(),options[0],label)
        instances=refs.source_instances.setdefault((i,*value),[])
        if not any(same_instance(instance_box,b) for b in instances):instances.append(tuple(instance_box))
        if value not in refs.get(i,[]):refs.setdefault(i,[]).append(value)
    for key,instances in refs.source_instances.items():
        if len(instances)==1 and _sole_source_note_instance(display,
                context.get('source_words') or optical,box,instances[0]):
            refs.sole_source_instances.add(key)
    # A correctly read caption can omit a tiny printed symbol. Original
    # character ink, a terminal source word and a unique local note can restore
    # the action without adding or changing any lexical caption word.
    if omitted_labels:
        for candidate in candidates:
            label=candidate.get('label')
            unrecognized=candidate.get('method')=='unrecognized_terminal_original_ink'
            if candidate.get('kind')!='bare' or not (unrecognized or label in omitted_labels|{'+'}):continue
            cx=(candidate['bbox'][0]+candidate['bbox'][2])/2
            cy=(candidate['bbox'][1]+candidate['bbox'][3])/2
            choices=[]
            for i,token in enumerate(tokens):
                if any(symbol in token for symbol in ('*','†','‡')) or i in refs:continue
                key=re.sub(r'\W','',vision_reading.normalized(token).casefold())
                if len(key)<2:continue
                for word in optical:
                    if re.sub(r'\W','',vision_reading.normalized(word['text']).casefold())!=key:continue
                    b=word['bbox'];height=max(1,b[3]-b[1])
                    if (b[0]+b[2])/2<=cx<=b[2]+height and abs((b[1]+b[3])/2-cy)<=height:
                        choices.append((abs(b[2]-cx)+abs((b[1]+b[3])/2-cy),i))
            if not choices:continue
            _,i=min(choices)
            if label=='+' or unrecognized:
                label=_verify_note_glyph(display,candidate['bbox'],context.get('marker_reader'),
                    context['marker_receipts'],pno,'omitted_caption_note_symbol')
            if label not in omitted_labels:continue
            owners=[owner for owner in labels[label] if owner[0]==pno]
            if len(owners)!=1:continue
            closing=re.search(r'''[)\]”’"']+$''',tokens[i])
            at=closing.start() if closing else len(tokens[i])
            refs.setdefault(i,[]).append((at,at,owners[0],label))
    return refs


def _finish_notes(pages,owners,catalogue,refs):
    """Merge note continuation spans without losing provenance or return links."""
    locations={};continuations=[]
    for page in pages:
        with unit_recovery.guard(page['pno'],'note_finalization'):
            for i,html in enumerate(page['asides']):
                match=re.match(r'<aside id="v4-note-(\d+)-([^" ]+)"[^>]*><p>(.*?)</p>(.*?)</aside>$',html,re.S)
                if not match:continue
                key=(int(match[1]),match[2]);owner=owners.get(key,key)
                if key==owner:locations[key]=(page,i,html)
                else:continuations.append((page,i,key,owner,match[3]))
    for page,i,key,owner,content in continuations:
        with unit_recovery.guard(page['pno'],'note_continuation', block=key[1]):
            if owner not in locations:raise ValueError('Note continuation has no selected owner')
            target,j,html=locations[owner]
            html=html.replace('</aside>',f'<p>{content}</p></aside>')
            locations[owner]=(target,j,html);page['asides'][i]=''
    for key,(page,i,html) in locations.items():
        with unit_recovery.guard(page['pno'],'note_backlinks', block=key[1]):
            backlinks=' '.join(f'<a href="#{ref}">Return</a>' for ref in dict.fromkeys(refs.get(key,[])))
            page['asides'][i]=html.replace('</aside>',backlinks+'</aside>')
    for page in pages:
        with unit_recovery.guard(page['pno'],'note_finalization'):
            page['asides']=[a for a in page['asides'] if a]


def _source_grid_live_text(text,optical,display,box,pno,bid):
    """Publish only complete source-matched text in independently ruled cells.

    Neither img.alt nor a table role proves cell text or structure. Every token
    and its order must match an owned original word; every word must fit one
    complete original ruled cell. Unknown/descriptive/mismatching data retains
    the original graphic and receives no invented live cell structure.
    """
    import io,itertools,statistics
    from PIL import Image
    source=_pixel_line_order(optical)
    tokens=text.split()
    normalize=lambda value:vision_reading.normalized(value)
    if (not source or len(tokens)!=len(source) or
            [normalize(t) for t in tokens]!=[normalize(w['text']) for w in source]):return None
    pix=display.pixmap(box,scale=3,max_pixels=500_000)
    picture=Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
    mask=picture.point(lambda v:255 if v<180 else 0);width,height=mask.size;data=mask.tobytes()
    def long(values,n):
        return any(value and sum(1 for _ in group)>=n*.7 for value,group in itertools.groupby(values))
    def centers(indices):
        groups=[]
        for v in indices:
            if groups and v<=groups[-1][-1]+1:groups[-1].append(v)
            else:groups.append([v])
        return [statistics.mean(g) for g in groups]
    horizontal=centers([y for y in range(height) if long(data[y*width:(y+1)*width],width)])
    vertical=centers([x for x in range(width) if long((data[y*width+x] for y in range(height)),height)])
    if len(horizontal)<4 or len(vertical)<3:return None
    xs=[box[0]+x/width*(box[2]-box[0]) for x in vertical]
    ys=[box[1]+y/height*(box[3]-box[1]) for y in horizontal]
    cells={};ownership=[]
    for token,word in zip(tokens,source):
        w=word['bbox'];owners=[(r,c) for r in range(len(ys)-1) for c in range(len(xs)-1)
            if xs[c]-.5<=w[0]<w[2]<=xs[c+1]+.5 and ys[r]-.5<=w[1]<w[3]<=ys[r+1]+.5]
        if len(owners)!=1:return None
        owner=owners[0];cells.setdefault(owner,[]).append(token);ownership.append(owner)
    if ownership!=sorted(ownership):return None
    rows=[]
    for r in range(len(ys)-1):
        row=[]
        for c in range(len(xs)-1):
            value=' '.join(cells.get((r,c),[]))
            row.append(f'<td><span data-source-page="{pno}" data-source-block="{escape(bid,quote=True)}">{escape(value)}</span></td>')
        rows.append('<tr>'+''.join(row)+'</tr>')
    html='<table class="source-table-transcription"><tbody>'+''.join(rows)+'</tbody></table>'
    record=dict(page=pno,block=bid,method='complete_source_word_order_and_original_ruled_cell_ownership',
        source_bbox=list(box),horizontal_tracks=ys,vertical_tracks=xs,source_words=len(source),
        live_cells=len(cells),rows=len(ys)-1,columns=len(xs)-1,source_text_proved=True)
    return html,record


def _sole_original_marker_between_words(display,before,after,glyph,label):
    """Inspect every original component in an independently bounded word gap.

    A raised marker may slightly overlap a word's OCR rectangle. Its original
    component centre still lies after that word's ink; any second component in
    the slot vetoes collapse. A terminal slot without a next word uses the
    older complete-row singularity proof instead.
    """
    if after is None:return False
    height=max(1,before[3]-before[1])
    if (min(before[3],after[3])-max(before[1],after[1])<=height*.3
            or glyph[3]>=before[3]-height*.1 or glyph[3]-glyph[1]>=height*.8):return False
    if glyph[2]-glyph[0]>(glyph[3]-glyph[1])*1.25*len(label):return False
    territory=[before[0]-.5,min(before[1],glyph[1])-.5,after[0]-.1,max(before[3],glyph[3])+.5]
    if territory[2]<=territory[0]:return False
    from . import vision_pixels
    pix=display.pixmap(territory,scale=6,max_pixels=200_000)
    picture,components=vision_pixels._word_ink_components(pix)
    sx=(territory[2]-territory[0])/picture.width;sy=(territory[3]-territory[1])/picture.height
    boxes=[[territory[0]+c[0]*sx,territory[1]+c[1]*sy,territory[0]+c[2]*sx,territory[1]+c[3]*sy]for c in components]
    # Locate the lexical ink boundary from its lower baseline strokes, not an
    # OCR box that may already include a reference. Small raised glyphs are
    # absent from that band. Inspect ALL ink after this independent boundary.
    baseline=[b for b in boxes if b[3]>glyph[3]+.5 and b[0]<glyph[0]]
    if not baseline:return False
    start=max(b[2]for b in baseline)+.1
    slot=[b for b in boxes if start<=(b[0]+b[2])/2<after[0]-.1]
    return bool(slot and all(glyph[0]-.4<=b[0]and b[2]<=glyph[2]+.4
        and glyph[1]-.4<=b[1]and b[3]<=glyph[3]+.4 for b in slot))


def _original_marker_slot_components(display, lexical_box, following_box, candidate_box, right):
    """Measure the complete raised group after independently located body ink.

    OCR boxes can include a missed superscript. Lower baseline components locate
    the lexical edge; every component after it belongs to the inspected slot.
    This proves coverage and horizontal cardinality, never a character value.
    """
    from . import vision_pixels
    a=lexical_box;c=candidate_box;height=max(1,a[3]-a[1])
    territory=[a[0]-.5,min(a[1],c[1])-.5,right-.1,max(a[3],c[3])+.5]
    if territory[2]<=territory[0]:return None
    pix=display.pixmap(territory,scale=8,max_pixels=250_000)
    picture,components=vision_pixels._word_ink_components(pix)
    sx=(territory[2]-territory[0])/picture.width;sy=(territory[3]-territory[1])/picture.height
    boxes=[[territory[0]+v[0]*sx,territory[1]+v[1]*sy,
            territory[0]+v[2]*sx,territory[1]+v[3]*sy]for v in components]
    baseline=[v for v in boxes if v[3]>c[3]+.5 and v[0]<c[0]]
    if not baseline:return None
    edge=max(v[2]for v in baseline)+.1
    slot=[v for v in boxes if edge<=(v[0]+v[2])/2<right-.1]
    if not slot:return None
    lexical=vision_reading.union(baseline);ink=vision_reading.union(slot)
    body_height=lexical[3]-lexical[1]
    if body_height<=0 or any(v[3]>lexical[3]-body_height*.18
            or v[3]-v[1]>body_height*.85 for v in slot):return None
    # Connected strokes of a single character may be disconnected vertically.
    # Horizontal overlap binds those strokes without merging adjacent digits.
    groups=[]
    for v in sorted(slot,key=lambda v:v[0]):
        if groups and v[0]<=groups[-1][1]+.15:groups[-1][1]=max(groups[-1][1],v[2])
        else:groups.append([v[0],v[2]])
    return dict(bbox=ink,horizontal_components=len(groups))


def _lexical_marker_view(tokens,source,proofs):
    """Reserve proved reference occurrences without editing source inventories.

    A placeholder's bounded raised slot is not an ordinary word. Identity may
    remain uncertain: only marker-only recognizer tokens in an independently
    anchored original slot can be reserved opaquely. Final reference rendering
    still uses the complete original inventory and its normal verification.
    """
    lexical=list(tokens)
    slots=[]
    opaque_reasons={_MARKER_GROUP_AMBIGUOUS,
        'original_character_optical_marker_conflict',
        'original_character_without_independent_optical_corroboration',
        _MARKER_GROUP_INCOMPLETE,'original_marker_verification_unusable'}
    for index,proof in proofs.items():
        if not (0<=index<len(tokens) and re.fullmatch(r'<n:[^>]+>',tokens[index])):continue
        box=proof.get('source_bbox')
        if not box or len(box)!=4 or not (box[0]<box[2] and box[1]<box[3]):continue
        proved=(proof.get('status')=='PROVED' and
            re.fullmatch(r'\d{1,4}|[*†‡§¶]',proof.get('label') or ''))
        opaque=(proof.get('status')=='UNPROVED' and proof.get('reason') in opaque_reasons
            and proof.get('anchor_before_bbox') and (proof.get('anchor_after_bbox') or
                box[2]-box[0]<=2*max(1,proof['anchor_before_bbox'][3]-proof['anchor_before_bbox'][1])))
        # An attached symbolic alternative and two exact lexical endpoints
        # reserve a bounded unknown glyph for the dedicated marker verifier.
        # English OCR must not turn that provisional reference into a word.
        before=proof.get('anchor_before_bbox');after=proof.get('anchor_after_bbox')
        symbol=re.search(r'([*†‡])$',tokens[index-1]) if index else None
        symbol_value=symbol[1] if symbol else proof.get('lexical_literal_suffix')
        if not proved and symbol_value:
            # An unplaced literal is protected from lexical replacement too.
            # This metadata makes no claim about its original glyph or owner.
            proof['unplaced_literal_suffix']=symbol_value
            proof['unplaced_literal_placeholder']=tokens[index]
        # Keep an unplaced literal in all lexical views. Removing it only from
        # the model makes independent OCR reintroduce the same ink as a word
        # correction, after which restoration would print the symbol twice.
        # Only a bounded original slot can reserve and later restore it.
        provisional=bool(not proved and symbol_value and before and after and
            before[2]-.5<=box[0] and box[2]<=after[0]+.5 and
            0<box[2]-box[0]<=max(1,before[3]-before[1])*3 and
            min(box[3],before[3],after[3])>max(box[1],before[1],after[1]))
        if provisional:
            proof['lexical_literal_suffix']=symbol_value
            proof['lexical_placeholder']=tokens[index]
            if tokens[index-1].endswith(symbol_value):lexical[index-1]=tokens[index-1][:-1]
            opaque=True
        if not (proved or opaque):continue
        if index:proof['lexical_preceding_token']=lexical[index-1]
        slots.append((proof,bool(proved)))
        # This field is supplied only by the existing sole-instance proof.
        # Never infer model suffix removal from a shared label alone.
        prior=proof.get('deduplicated_literal_token')
        if proved and prior is not None and prior==index-1 and prior>=0:
            lexical[prior]=proof['literal_display_text']
    # A missing occurrence binding cannot turn independently raised, marker-only
    # ink into prose. Reserve the physical atom without assigning its identity
    # to any placeholder. Ordinary full-height baseline numbers stay lexical.
    raised=[]
    if proofs:
        for word in source:
            if not re.fullmatch(r'[0-9*†‡§¶]+',word['text']):continue
            if word.get('native_reference_ink'):
                raised.append(word);continue
            box=word['bbox'];height=max(.1,box[3]-box[1])
            key=lambda text:re.sub(r'[^a-z]','',vision_reading.normalized(text).casefold())
            before={key(tokens[i-1])for i in proofs if i>0}
            symbolic=(re.fullmatch(r'[*†‡§¶]',word['text']) and any(
                key(w['text']) in before and key(w['text']) and
                0<=box[0]-w['bbox'][2]<=3*max(height,w['bbox'][3]-w['bbox'][1]) and
                min(box[3],w['bbox'][3])>max(box[1],w['bbox'][1])for w in source))
            if symbolic:
                raised.append(word);continue
            neighbours=[w for w in source if re.search(r'[A-Za-z]',w['text'])
                and 0<=min(abs(w['bbox'][0]-box[2]),abs(box[0]-w['bbox'][2]))<=3*max(height,w['bbox'][3]-w['bbox'][1])
                and w['bbox'][1]-height<=box[1]<=w['bbox'][3]]
            if any(height<.8*(w['bbox'][3]-w['bbox'][1]) and
                   box[3]<w['bbox'][3]-.15*(w['bbox'][3]-w['bbox'][1]) for w in neighbours):
                raised.append(word)
    result=[]
    for word in source:
        if any(word is w for w in raised):continue
        text=word['text'];bounds=word['bbox'];replacement=text
        for proof,proved in slots:
            box=proof['source_bbox']
            overlap=max(0,min(bounds[2],box[2])-max(bounds[0],box[0]))*max(0,min(bounds[3],box[3])-max(bounds[1],box[1]))
            small=min((bounds[2]-bounds[0])*(bounds[3]-bounds[1]),(box[2]-box[0])*(box[3]-box[1]))
            if small<=0 or overlap<small*.5:continue
            if proved and text==proof['label']:
                replacement='';break
            preceding=proof.get('lexical_preceding_token')
            if (preceding and text.startswith(preceding) and text!=preceding and
                    re.fullmatch(r'''[0-9*†‡§¶'’`]+''',text[len(preceding):]) and
                    box[0]>=bounds[2]-max(1,bounds[3]-bounds[1])*1.5):
                # An independently bounded reference may be fused to the word
                # as an apostrophe by OCR. Remove only that extra nonlexical
                # suffix; preserve all punctuation already in the reading.
                replacement=preceding;break
            if not proved:
                # An unknown identity cannot remove lexical letters or a
                # broad cardinal token. Keep it opaque only inside its actual
                # bounded raised original slot; numeric-role failures stay live.
                height=max(1,proof['anchor_before_bbox'][3]-proof['anchor_before_bbox'][1])
                marker_atom=(re.fullmatch(r'[0-9*†‡§¶]+',text) or
                    proof.get('lexical_literal_suffix') and re.fullmatch(r'[A-Za-z+]',text))
                if (marker_atom and
                        box[0]-height*.5<=bounds[0] and bounds[2]<=box[2]+height*.5 and
                        box[1]-height*.5<=bounds[1] and bounds[3]<=box[3]+height*.5):
                    replacement='';break
                # A symbolic suffix at this bounded slot is nonlexical even
                # when its identity is not proved. Preserve adjacent punctuation
                # and letters; the final marker path still discloses the glyph.
                suffix=re.search(r'''([*†‡§¶+])([.,;:)\]”’"']*)$''',text)
                if (suffix and re.search(r'[A-Za-z]',text[:suffix.start()]) and
                        box[0]>=bounds[2]-max(1,bounds[3]-bounds[1])*1.5):
                    replacement=text[:suffix.start()]+suffix[2];break
                continue
            suffix=re.search(re.escape(proof['label'])+r'''([.,;:)\]”’"']*)$''',text)
            if (suffix and re.search(r'[A-Za-z]',text[:suffix.start()]) and
                    box[0]>=bounds[2]-max(1,bounds[3]-bounds[1])*1.5):
                replacement=text[:suffix.start()]+suffix[1];break
        if replacement:result.append(word if replacement==text else dict(word,text=replacement))
    from .pixel_adjudication import CropWitness
    if isinstance(source,CropWitness):
        result=CropWitness(result)
        # Reserving reference ink changes the lexical view, not the independent
        # recognizer or its failure receipt. Project lazy secondary views through
        # the same reservations so they cannot reintroduce the reference as prose.
        def project(views):
            return [_lexical_marker_view(tokens,view,proofs)[1]for view in views]
        for name in ('alternates','resampled'):
            reader=getattr(source,name,None)
            if reader is not None:
                setattr(result,name,lambda reader=reader:project(reader()))
        for name in ('word_readings','word_context_readings'):
            reader=getattr(source,name,None)
            if reader is not None:
                setattr(result,name,lambda word,reader=reader:project(reader(word)))
        # Reserving a reference changes the lexical inventory, not the
        # original ink used to prove complete words and printed spaces.
        for name in ('mark_absence','failure','word_scope','word_separation','model_reading',
                     'word_gap','dash_shape','fragment_boundary','word_end_dash'):
            if hasattr(source,name):setattr(result,name,getattr(source,name))
    return lexical,result


def _placeholder_display_evidence(tokens,optical,display,box,context,pno,literal=None):
    """Bind display glyphs to original slots, independently of note owners.

    Exact lexical neighbours locate a slot, never identify its ink. A unique
    raised character reading or one bounded marker verification identifies it.
    Missing or conflicting evidence is returned for visible SOURCE disclosure.
    """
    positions=[i for i,t in enumerate(tokens) if re.fullmatch(r'<n:[^>]+>',t)]
    if not positions:return {}
    from . import vision_pixels
    candidates=vision_pixels.raised_markers(display,box,context['cache'])
    key=lambda t:re.sub(r'[^a-z]','',vision_reading.normalized(t).casefold())
    source=[w for w in optical if len(key(w['text']))>=2]
    answers={}
    for index in positions:
        # A verified literal and this placeholder can name one physical glyph.
        # Reuse its actual proof; never re-read it or alter the model tokens.
        marker=re.fullmatch(r'<n:([^>]+)>',tokens[index]);owner=context['owners'].get((pno,marker[1]),(pno,marker[1]))
        reused=[]
        for a,b,bound_owner,label in (literal or {}).get(index-1,[]):
            identity=(index-1,a,b,bound_owner,label)
            if bound_owner==owner and identity in getattr(literal,'sole_source_instances',set()):
                instances=literal.source_instances.get(identity,[])
                if len(instances)==1:reused.append((label,instances[0]))
        if len(reused)==1:
            label,glyph=reused[0];answers[index]=dict(status='PROVED',label=label,source_bbox=list(glyph),
                method='existing_verified_literal_same_original_instance',reason=None);continue
        if any(bound_owner!=owner for a,b,bound_owner,label in (literal or {}).get(index-1,[])):
            # A non-singular or differently owned literal proves a separate
            # physical action, not this placeholder's display glyph.
            answers[index]=dict(status='UNPROVED',source_bbox=list(box),
                reason='adjacent_literal_not_proved_same_original_instance');continue
        before=next((j for j in range(index-1,-1,-1) if len(key(tokens[j]))>=2),None)
        after=next((j for j in range(index+1,len(tokens)) if len(key(tokens[j]))>=2),None)
        pairs=[]
        for first in source:
            if before is None or key(first['text'])!=key(tokens[before]):continue
            a=first['bbox'];height=max(1,a[3]-a[1])
            if after is None:
                pairs.append((first,None));continue
            for second in source:
                if key(second['text'])!=key(tokens[after]):continue
                b=second['bbox']
                same_row=min(a[3],b[3])-max(a[1],b[1])>height*.3
                if ((same_row and b[0]>=a[0] and 0<=b[0]-a[2]<=height*4)
                        or (0<=b[1]-a[3]<=height*2 and b[0]<a[0])):
                    pairs.append((first,second))
        proof={'status':'UNPROVED','source_bbox':list(box),'reason':_MARKER_SLOT_UNBOUND}
        # An absent lexical slot provides no evidence for the printed label.
        # Keep the known note owner, but disclose its unproved body display in
        # exactly the same way as multiple candidate original slots below.
        if len(pairs)==1:
            first,second=pairs[0];a=first['bbox'];height=max(1,a[3]-a[1])
            b=second['bbox'] if second else None
            same_row=b is not None and min(a[3],b[3])-max(a[1],b[1])>height*.3
            right=b[0] if same_row else min(box[2],a[2]+height*1.5)
            # An OCR token can include its superscript. Candidate geometry may
            # lie inside that token's right edge; lexical anchors stay exact.
            eligible=[c for c in candidates if c.get('kind')=='bare'
                and c['bbox'][0]>=a[2]-height*.9 and c['bbox'][2]<=right+1
                and a[1]-height*.6<=c['bbox'][1]<c['bbox'][3]<=a[3]+height*.1]
            unique=[]
            for c in eligible:
                if not any(c.get('label')==v.get('label') and
                    all(abs(x-y)<.8 for x,y in zip(c['bbox'],v['bbox'])) for v in unique):unique.append(c)
            proof.update(anchor_before_bbox=list(a),anchor_after_bbox=list(b) if b else None)
            optical_labels=set()
            if len(unique)==1:
                candidate=unique[0];c=candidate['bbox']
                for word in optical:
                    w=word['bbox']
                    if min(w[2],c[2])-max(w[0],c[0])<=0 or min(w[3],c[3])-max(w[1],c[1])<=0:continue
                    label=re.fullmatch(r'\d{1,4}|[*†‡§¶]',word['text'])
                    if label:optical_labels.add(label[0])
                    else:
                        suffix=re.search(r'''(?<=[A-Za-z])[.,;:!?…)\]”’"']*(\d{1,4}|[*†‡§¶])[.,;:]*$''',word['text'])
                        if suffix and c[0]>=w[2]-(w[3]-w[1]):optical_labels.add(suffix[1])
            conflict=bool(optical_labels and optical_labels!={unique[0].get('label')}) if len(unique)==1 else False
            complete_group=None;complete_identity=False
            if len(unique)==1:
                candidate=unique[0];c=candidate['bbox']
                complete_group=_original_marker_slot_components(display,a,b,c,right)
                label=candidate.get('label') or ''
                expected=len(label) if label.isdigit() else 1
                complete_identity=bool(complete_group and complete_group['horizontal_components']==expected
                    and all(c[j]-.4<=complete_group['bbox'][j] for j in (0,1))
                    and all(complete_group['bbox'][j]<=c[j]+.4 for j in (2,3)))
            incomplete_group=bool(len(unique)==1 and not complete_identity)
            numeric_role_unproved=bool(len(unique)==1 and optical_labels=={unique[0].get('label')}
                and str(unique[0].get('label') or '').isdigit()
                and not _numeric_original_marker_role(display,a,unique[0]['bbox']))
            if numeric_role_unproved:
                proof.update(source_bbox=list(unique[0]['bbox']),reason=_MARKER_ROLE_UNPROVED)
            elif len(unique)==1 and complete_identity and optical_labels=={unique[0].get('label')} and re.fullmatch(r'\d{1,4}|[*†‡§¶]',unique[0].get('label') or ''):
                c=unique[0];proof.update(status='PROVED',label=c['label'],source_bbox=list(c['bbox']),
                    method='unique_raised_original_glyph_at_exact_lexical_slot',reason=None)
            elif len(unique)>1:
                proof.update(source_bbox=vision_reading.union([c['bbox'] for c in unique]),
                    reason=_MARKER_GROUP_AMBIGUOUS)
            else:
                # Independent OCR can miss the glyph entirely. Original ink
                # between two exact words supplies only a bounded verification
                # request; empty ink or an unusable answer never earns a label.
                slot=[max(box[0],a[2]-.4),max(box[1],a[1]-height*.4),
                      min(box[2],right+.4),min(box[3],a[3]+.4)]
                uncorroborated=len(unique)==1 and not optical_labels
                if slot[0]<slot[2] and slot[1]<slot[3] or conflict or uncorroborated or incomplete_group:
                    # A unique OCR candidate locates original ink, but cannot
                    # certify its character without an independent observation.
                    # Verify exactly that slot or expose its original reading.
                    # Verify all independently bounded original strokes, not
                    # merely the mutually agreeing OCR candidate rectangle.
                    if complete_group:ink=list(complete_group['bbox'])
                    elif incomplete_group:
                        slot[0]=max(box[0],a[0]-.4)
                        ink=vision_pixels.ink_bounds(display,slot)
                    else:ink=list(unique[0]['bbox']) if conflict or uncorroborated else vision_pixels.ink_bounds(display,slot)
                    if incomplete_group:proof['reason']=_MARKER_GROUP_INCOMPLETE
                    if conflict:proof['reason']='original_character_optical_marker_conflict';proof['optical_labels']=sorted(optical_labels)
                    elif uncorroborated:proof['reason']='original_character_without_independent_optical_corroboration'
                    proof['source_bbox']=list(ink or slot)
                    reader=context.get('marker_reader')
                    if ink and reader:
                        clip=list(extract.pymupdf.Rect([ink[0]-1,ink[1]-1,ink[2]+1,ink[3]+1])&display.rect)
                        previous=[r for r in context['marker_receipts'].get(pno,[]) if r.get('block_token')==before
                            and r.get('source_bbox') and min(r['source_bbox'][2],clip[2])>max(r['source_bbox'][0],clip[0])
                            and min(r['source_bbox'][3],clip[3])>max(r['source_bbox'][1],clip[1])]
                        if len(previous)==1:receipt=previous[0]
                        else:
                            try:receipt=reader(display.roi_jpeg(clip,scale=12,quality=95))
                            except Exception as error:
                                from . import unit_recovery
                                if unit_recovery.fatal(error):raise
                                receipt=dict(label='',uncertain=True,request_dispatched=False,
                                    reason=type(error).__name__+': '+str(error),new_cost_usd=0.)
                            context['marker_receipts'].setdefault(pno,[]).append(dict(receipt,
                                source_bbox=clip,block_token=index,original_frame=dict(display.provenance.get('original_frame') or {})))
                        if (not receipt.get('uncertain',True) and re.fullmatch(r'\d{1,4}|[*†‡§¶]',receipt.get('label') or '')
                                and (not complete_group or complete_group['horizontal_components']==
                                     (len(receipt['label']) if receipt['label'].isdigit() else 1))
                                and (not receipt['label'].isdigit() or _numeric_original_marker_role(display,a,ink))):
                            proof.update(status='PROVED',label=receipt['label'],source_bbox=clip,
                                method='bounded_original_slot_verification',reason=None)
                        else:proof['reason']='original_marker_verification_unusable'
                    elif not ink:proof['reason']='no_original_ink_at_bound_marker_slot'
        if proof['status']=='PROVED' and before==index-1:
            # A literal followed by a placeholder can describe this one glyph.
            # Strip only the exact verified suffix at its original raised slot;
            # ordinary lexical numbers and a second physical slot remain intact.
            suffix=re.search(re.escape(proof['label'])+r'''([.,;:)\]”’"']*)$''',tokens[before])
            if (suffix and len(key(tokens[before][:suffix.start()]))>=2
                    and (_sole_source_note_instance(display,context.get('source_words') or optical,box,proof['source_bbox'])
                         or _sole_original_marker_between_words(display,proof['anchor_before_bbox'],proof.get('anchor_after_bbox'),proof['source_bbox'],proof['label']))):
                proof['deduplicated_literal_token']=before
                proof['literal_display_text']=tokens[before][:suffix.start()]+suffix[1]
            elif suffix and len(key(tokens[before][:suffix.start()]))>=2:
                proof.update(status='UNPROVED',reason='literal_and_placeholder_not_same_original_instance')
        answers[index]=proof
    return answers


class _DisclosureQualityRejection(ValueError):
    """A known unsafe disclosure reading; other compilation errors stay strict."""


class _CompilationQualityFailure(unit_recovery.UnitFailure):
    """Independent rejected units from a disposable compilation attempt."""
    def __init__(self, units):
        units=[rejection for unit in copy.deepcopy(units)
               for rejection in unit.get('source_failure_units',[unit])]
        whole_pages={unit['page'] for unit in units if unit.get('block') is None}
        owned=[];combined=set()
        for unit in units:
            page=unit['page']
            if page not in whole_pages:
                owned.append(unit);continue
            if page in combined:continue
            combined.add(page)
            rejected=[item for item in units if item['page']==page]
            reason='; '.join(dict.fromkeys(item['source_failure_reason'] for item in rejected))
            record=dict(page=page,block=None,source_failure_reason=reason)
            if len(rejected)>1:
                # A required whole-page fallback also owns its failed blocks.
                # Preserve every rejection without creating unrendered units.
                record['source_failure_units']=rejected
            owned.append(record)
        reason='; '.join(dict.fromkeys(unit['source_failure_reason'] for unit in owned))
        super().__init__(owned,'page_compilation',ValueError(reason))


def _unproved_optical_letter_clusters(text,optical,display):
    """Decline an optical substitution supported by repeated solid dot-like ink.

    A dictionary can contain OCR noise as legitimate abbreviations. Require two
    nearby same-baseline lettering anchors and multiple thin, wide clusters of
    compact solid components. Real miniature letters with open interiors remain
    eligible. This conservative pattern check does not identify characters; the
    caller retains the affected original unit as an image instead.
    """
    from . import vision_pixels
    import statistics
    vocabulary={word.casefold() for word in re.findall(r'[^\W\d_]+',text)}
    lexical=[word for word in optical if re.fullmatch(r'[^\W\d_]{3,}',word['text'])]
    anchors=[word for word in lexical if word['text'].casefold() in vocabulary]
    if len(anchors)<2:return False
    suspect=0;measured=0
    for word in lexical:
        if word['text'].casefold() in vocabulary:continue
        box=word['bbox'];height=box[3]-box[1]
        nearby=[anchor for anchor in anchors
            if abs(anchor['bbox'][3]-box[3])<=.45*(anchor['bbox'][3]-anchor['bbox'][1])]
        if len(nearby)<2:continue
        normal=statistics.median(anchor['bbox'][3]-anchor['bbox'][1] for anchor in nearby)
        if height<=0 or height>=normal*.35 or box[2]-box[0]<=height*6:continue
        measured+=1
        if measured>32:return True
        pix=display.pixmap(box,scale=6,max_pixels=200_000)
        picture,components=vision_pixels._word_ink_components(pix)
        if len(components)<3:continue
        sx=(box[2]-box[0])/picture.width;sy=height/picture.height
        pixels=picture.load()
        def solid_component(component):
            left,top,right,bottom=component
            area=(right-left)*(bottom-top)
            background={(x,y) for x in range(left,right) for y in range(top,bottom)
                        if pixels[x,y]>=180}
            if len(background)>area*.2:return False
            # A real small rounded letter can have the same outside dimensions
            # as a dot. Its enclosed white interior is independent pixel evidence
            # against this pattern, even when most of the component is dark.
            edge={point for point in background if point[0] in (left,right-1)
                  or point[1] in (top,bottom-1)}
            reached=set(edge);pending=list(edge)
            while pending:
                x,y=pending.pop()
                for neighbour in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                    if neighbour in background and neighbour not in reached:
                        reached.add(neighbour);pending.append(neighbour)
            return len(reached)==len(background)
        if all((component[3]-component[1])*sy<normal*.35 and
               .4<=(component[2]-component[0])*sx/max(.01,(component[3]-component[1])*sy)<=2.5
               and solid_component(component) for component in components):
            suspect+=1
            if suspect>=2:return True
    return False


def compile_page(answer,words,display,pno,package,image_records,pixel_words=None,forced_uncertain=(),regions=None,body_owners=None,note_context=None,figure_cases=None,local_ocr=False,local_ocr_units=None):
    vision_reading.validate(answer)
    if local_ocr:words=[]
    if words:
        regions=dict(regions or {})
        regions['_instances']=dict(regions.get('_instances',{}))
        regions['_ambiguous_instances']=set(regions.get('_ambiguous_instances',()))
        _reserve_layer_instances(answer,words,display,regions)
        if pixel_words:_reserve_layer_instances(answer,pixel_words,display,regions)
    body=[];asides=[];alignment=[];marked=0;tokens_count=0;source_quote_suppressions={}
    footnotes={b['id']:b for b in answer['blocks'] if b['role']=='footnote'}
    controller=unit_recovery.current()
    unresolved_notes=set(footnotes)&set(controller.blocked(pno) if controller else {})
    refs={};extras=[];live_table_cells=[];marker_disclosures=[];marker_repairs=[];pixel_rows=_pixel_row_ids(pixel_words or [])
    quality_failures=[];pixel_claims={};optical_fallbacks=set()
    final_local_units=[]
    if local_ocr:
        source=f'images/v4-local-ocr-source-{pno}.jpg'
        if hasattr(package,'disclosure_image'):
            package.disclosure_image(source,display,display.rect)
        else:
            package.image(source,display.jpeg(display.rect,scale=2,quality=88))
        image_records[source]=dict(page=pno,bbox=list(display.rect),kind='vision_disclosure')
        extras.append(f'<aside id="v4-local-ocr-source-{pno}" class="footnote vision-disclosure" epub:type="footnote">'
            '<p>Complete original page for the unverified local OCR transcription.</p>'
            f'<img src="{source}" alt="Complete original source page"/></aside>')
    for block in answer['blocks']:
        try:
            original_model_tokens=block['text'].split()
            # Reader emphasis is metadata, never a printed angle-bracket word.
            # Accept only the closed formatting vocabulary, retaining all inner
            # words and the original response in the audit record.
            cleaned_tokens=[re.sub(r'</?(?:i|b|em|strong)>','',token) for token in original_model_tokens]
            positions={old:new for new,old in enumerate(i for i,token in enumerate(cleaned_tokens) if token)}
            block=dict(block,text=re.sub(r'</?(?:i|b|em|strong)>','',block['text']),
                low_confidence=[positions[i] for i in block['low_confidence'] if i in positions])
            bid=block['id'];role=block['role'];box=block_rect(block,display)
            # A rejected note's validation stub owns no live words or note link.
            # Its admitted original unit is inserted as source context below.
            if role=='footnote' and bid in unresolved_notes:continue
            territories=(regions or {}).get(bid,[box])
            def owned(items):
                return _owned_block_words(block,answer,items,display,regions,body_owners)
            if role in ('running_head','folio'):
                alignment.append(dict(id=bid,role=role,text=block['text'],bbox=box));continue
            if role in ('figure','table'):
                from . import vision_pixels
                case=(figure_cases or {}).get(bid)
                if case:
                    figure,context,src,target=_figure_source_context(display,pno,bid,package,image_records)
                    body.append(figure);extras.append(context)
                    case.update(page=pno,source_reference=target,source_image=src)
                    frame=display.provenance.get('original_frame')
                    if frame:case['original_page_index0']=frame['original_page_index0']
                    alignment.append(dict(id=bid,role=role,bbox=box,
                        source_bound_art=role=='figure' or vision_pixels.table_rules(display,box),
                        degraded_source_context=True,continues_previous=False,continues_next=False))
                    continue
                if text_quality.inspect_disclosure(block['text'])['needs_read']:
                    raise _DisclosureQualityRejection('Unreliable image description requires original source disclosure')
                src=f'images/vision_p{pno:04d}_{bid}.jpg'
                package.image(src,display.jpeg(box,scale=2,quality=88))
                image_records[src]=dict(page=pno,bbox=box,kind='figure' if role=='figure' else 'table')
                # A generated description is not a transcription merely
                # because it consists of dictionary words. Publish literal
                # reading text only when this original crop proves it; otherwise
                # the actual figure carries a neutral source label.
                description_verified=False
                if note_context and note_context.get('cache'):
                    from . import pixel_adjudication
                    description_pixels=pixel_adjudication.read_crop(display,territories,
                        Path(note_context['cache'])/'adjudication')
                    description_verified=bool(description_pixels) and (
                        [pixel_adjudication._key(w['text']) for w in description_pixels]
                        ==[pixel_adjudication._key(t) for t in block['text'].split()]
                        and all(w.get('confidence',0)>=90 for w in description_pixels))
                description=(block['text'] if description_verified else
                             f'Original {role} from PDF page {pno+1}')
                body.append(f'<figure><img src="{src}" alt="{escape(description,quote=True)}"/></figure>')
                if role=='table':
                    proved=_source_grid_live_text(block['text'],owned(pixel_words or words),display,box,pno,bid)
                    if proved:
                        html,case=proved;body[-1]+=html;live_table_cells.append(case)
                        tokens_count+=case['source_words']
                alignment.append(dict(id=bid,role=role,bbox=box,
                    description_pixel_verified=description_verified,
                    source_bound_art=bool((regions or {}).get(bid)) and
                        (role=='figure' or vision_pixels.table_rules(display,box)),
                    continues_previous=False,continues_next=False));continue
            # PDF emission order can place short glyph runs or italic words
            # after later lines. Conservation compares physical occurrences in
            # each owned territory, never that arbitrary serialization order.
            local=_owned_block_words(block,answer,words,display,regions,body_owners,ordered=True)
            local=_native_lexical_view(display,local,
                re.findall(r'(?:^|\s)([^\s<>]+)\s*<n:[^>]+>',block['text']))
            opening_proof=copy.deepcopy((regions or {}).get('_note_openings',{}).get(bid)) if role=='footnote' else None
            local=_note_lexical_evidence(local,opening_proof)
            # A footnote label is preserved as a label, outside its lexical reading.
            if role=='footnote' and local and vision_reading.normalized(local[0]['text']).strip('.,:;()[]{}')==vision_reading.normalized(block['marker']).strip('.,:;()[]{}'):
                local=local[1:]
            optical_tail=_complete_optical_row_territory(block,answer,pixel_words,display,regions,body_owners,words)
            if optical_tail:
                regions=dict(regions or {})
                territories=list(territories)+[list(w['bbox'])for w in optical_tail]
                regions[bid]=territories
                regions['_instances']=dict(regions.get('_instances',{}))
                regions['_instances'].update({_source_instance(w):bid for w in optical_tail})
                regions['_row_additions']=dict(regions.get('_row_additions',{}))
                regions['_row_additions'][bid]=tuple(_source_instance(w)for w in optical_tail)
                local=_owned_block_words(block,answer,words,display,regions,body_owners,ordered=True)
                local=_native_lexical_view(display,local,
                    re.findall(r'(?:^|\s)([^\s<>]+)\s*<n:[^>]+>',block['text']))
                local=_note_lexical_evidence(local,opening_proof)
                if role=='footnote' and local and vision_reading.normalized(local[0]['text']).strip('.,:;()[]{}')==vision_reading.normalized(block['marker']).strip('.,:;()[]{}'):
                    local=local[1:]
            original=' '.join(w['text'] for w in local)
            original_quality=text_quality.inspect_disclosure('\n'.join(
                ' '.join(w['text'] for w in row) for row in _pixel_lines(local)))
            optical_evidence=owned(pixel_words) if pixel_words is not None else None
            punctuation_proof=(_caption_punctuation_anchors(optical_evidence,
                list(range(len(optical_evidence))),block['text'])
                if role=='caption' and optical_evidence and (regions or {}).get(bid) else [])
            # A complete exact optical caption proves that a long whitespace-token
            # disagreement is punctuation segmentation, not an unrelated line.
            # Ordinary layer disagreement marks and confidence positions remain.
            run_limit=(max(len(local),len(block['text'].split()))+1
                       if punctuation_proof else vision_reading.UNALIGNED_RUN)
            result=vision_reading.align(block['text'],local,block['low_confidence'],
                has_layer=bool(words),pixel_words=optical_evidence,run_limit=run_limit)
            if optical_tail:result['guard'].append(dict(kind='complete_optical_row_territory',restored_words=len(optical_tail)))
            evidence_boxes=result['boxes'];wraps=set();optical_local=[];independent_optical_fallback=False
            if pixel_words is not None:
                optical_local=_note_lexical_evidence(_owned_block_words(block,answer,pixel_words,display,regions,body_owners,ordered=True),opening_proof)
                if role=='footnote' and optical_local and vision_reading.normalized(optical_local[0]['text']).strip('.,:;()[]{}')==vision_reading.normalized(block['marker']).strip('.,:;()[]{}'):
                    optical_local=optical_local[1:]
                # Check optical-only dropped lines even when a sparse original layer
                # agrees perfectly with the model's incomplete paragraph.
                suffix_proof=(_caption_punctuation_suffix_anchors(optical_local,
                    list(range(len(optical_local))),block['text'],box)
                    if role=='caption' and not punctuation_proof and (regions or {}).get(bid) else [])
                guard_evidence=(_caption_segmentation_evidence(optical_local,suffix_proof,block['text'])
                    if suffix_proof else optical_local)
                guarded=vision_reading.align(block['text'],guard_evidence,(),has_layer=True,
                    run_limit=(max(len(optical_local),len(block['text'].split()))+1
                               if punctuation_proof else vision_reading.UNALIGNED_RUN))
                if guarded['fallback']:
                    if _unproved_optical_letter_clusters(block['text'],optical_local,display):
                        raise _DisclosureQualityRejection('Optical letter claims have unproved punctuation-sized source ink')
                    if role=='heading':
                        # An unproved heading hint cannot turn an adjacent
                        # optical line into replacement heading words.
                        raise _DisclosureQualityRejection('Unproved optical heading requires original source disclosure')
                    independent_optical_fallback=True
                    result=guarded
                    result['guard'].append({'kind':'independent_pixel_line_fallback'})
                elif result['fallback'] and optical_local:
                    # The full pixel region independently corroborates this reading.
                    # An unrelated damaged layer cannot replace it with garbage.
                    result=vision_reading.align(block['text'],local,block['low_confidence'],
                        has_layer=bool(words),run_limit=max(len(local),len(block['text'].split()))+1)
                    result['guard']=[{'kind':'damaged_layer_pixel_corroboration'}]
                if suffix_proof and not guarded['fallback']:
                    # The proved suffix cannot certify its unproved prefix. Keep
                    # every prefix disagreement visible even on image-only pages.
                    result['marked'].update(guarded['marked'])
                # Optical coordinates bind the disclosure to the printed reading;
                # they do not change certainty or the chosen published words. A
                # damaged layer can have unrelated word rectangles even when its
                # few anchors permit a correct full-page model transcription.
                evidence_boxes=(guarded['boxes'] if result['tokens']==guarded['tokens']
                                else result['boxes'])
                wraps=_printed_wraps(result['tokens'],optical_local)
            # A block with no word overlap in a layered page needs explicit uncertainty,
            # but cannot fall back to an empty string and delete printed content.
            if words and not local and block['text'].strip():
                result['marked']=set(range(len(result['tokens'])))
                result['unmapped_block']=True
            if local_ocr:
                # OCR and a PDF OCR layer are not independent witnesses. Their
                # agreement never certifies a local-only transcription.
                result['marked']=set(range(len(result['tokens'])))
            if local_ocr and local_ocr_units is not None:
                owned_units=[unit for unit in local_ocr_units if unit['id']==bid]
                if len(owned_units)>1:
                    raise _DisclosureQualityRejection('Local OCR has ambiguous source owners')
                raw=owned_units[0] if owned_units else None
                if raw and [word['text'] for word in raw['words']]==result['tokens']:
                    source_unit=copy.deepcopy(raw)
                    source_unit['source_method']='local_recognition_occurrences'
                elif ((independent_optical_fallback or bid in forced_uncertain)
                        and optical_local
                        and [word['text'] for word in optical_local]==result['tokens']):
                    # A recovery reading owns the actual independent occurrences
                    # selected above, rather than the superseded OCR transcript.
                    indices={id(word):index for index,word in enumerate(pixel_words or [])}
                    selected=[indices.get(id(word)) for word in optical_local]
                    if (len(indices)!=len(pixel_words or []) or None in selected
                            or len(set(selected))!=len(selected)):
                        raise _DisclosureQualityRejection('Local OCR recovery has nonunique source occurrences')
                    source_unit=dict(id=bid,words=copy.deepcopy(optical_local),
                        source_method='independent_pixel_occurrences',
                        source_word_indices=selected,mean_confidence=None,
                        low_confidence_tokens=list(range(len(optical_local))))
                else:
                    raise _DisclosureQualityRejection('Local OCR reading has no exact source occurrence inventory')
                # Crop the words actually published. Independent recognition may
                # miss a final row; interpolated alignment boxes cannot replace
                # the original local occurrences that still supply that text.
                evidence_boxes={index:word['bbox'] for index,word in enumerate(source_unit['words'])}
                final_local_units.append(source_unit)
            # One discreet reference covers each contiguous disagreement span.
            # Every covered word remains recorded in the machine-readable alignment.
            if bid in forced_uncertain:
                result['marked']=set(range(len(result['tokens'])))
                result['fallback']=True
                result['guard'].append({'kind':'omitted_pixel_line_recovery'})
            proposed=re.findall(r'<n:[^>]+>|\S+',re.sub(r'(<n:[^>]+>)',r' \1 ',block['text']))
            lexical=lambda tokens:[t for t in tokens if not t.startswith('<n:')]
            admitted=[lexical(proposed),[w['text'] for w in local],[w['text'] for w in optical_local]]
            if lexical(result['tokens']) not in admitted:
                raise _DisclosureQualityRejection('Aligned reading has no admitted source or reader inventory')
            # Decide lexical disagreements against an independently recognized
            # upright crop before rendering any live text or disclosure note.
            adjudication=None;witness=None;legacy_native_words=[];native_geometry=None
            if note_context and note_context.get('cache') and not local_ocr:
                from . import pixel_adjudication
                with unit_recovery.guard(pno,'pixel_adjudication',block=bid):
                    from .native_text import drawn_word_authority,drawn_word_authorities,unproved_legacy_word
                    note_head=_complete_note_head_territory(block,answer,words,display,regions,local)
                    if note_head is not None:
                        result['guard'].append(dict(kind='complete_note_head_territory',
                            restored_words=len(note_head)-len(local)))
                        local=note_head;territories=_native_ink_regions(territories,local)
                        refreshed=vision_reading.align(block['text'],local,block['low_confidence'],
                            has_layer=True,pixel_words=optical_local)
                        if not refreshed['fallback']:
                            independent_optical_fallback=False
                            for key in ('tokens','marked','boxes','fallback','dropped_layer_words'):
                                result[key]=refreshed[key]
                    note_tail=_complete_note_tail_territory(block,answer,words,display,regions,local)
                    if note_tail is not None:
                        result['guard'].append(dict(kind='complete_note_tail_territory',
                            restored_words=len(note_tail)-len(local)))
                        local=note_tail;territories=_native_ink_regions(territories,local)
                        # Re-evaluate the conservation guard with the complete
                        # owned inventory; its earlier missing-row fallback is
                        # not word-level reader doubt. Pixel adjudication below
                        # still checks the widened original crop normally.
                        refreshed=vision_reading.align(block['text'],local,block['low_confidence'],
                            has_layer=True,pixel_words=optical_local)
                        if not refreshed['fallback']:
                            independent_optical_fallback=False
                            for key in ('tokens','marked','boxes','fallback','dropped_layer_words'):
                                result[key]=refreshed[key]

                    closing=_complete_closing_mark_territory(block,answer,words,pixel_words,display,regions,local)
                    if closing is not None:
                        result['guard'].append(dict(kind='complete_closing_mark_territory',restored_marks=1))
                        local=closing;territories=_native_ink_regions(territories,local)
                        refreshed=vision_reading.align(block['text'],local,block['low_confidence'],
                            has_layer=True,pixel_words=optical_local)
                        if not refreshed['fallback']:
                            independent_optical_fallback=False
                            for key in ('tokens','marked','boxes','fallback','dropped_layer_words'):
                                result[key]=refreshed[key]

                    complete=_complete_native_reading_territory(block,answer,words,display,
                        regions,body_owners,local)
                    if complete is not None:
                        result['guard'].append(dict(kind='complete_native_reading_territory',
                            restored_words=len(complete)-len(local)))
                        local=complete;territories=_native_ink_regions(territories,local)
                    native_occurrences=[dict(w,bbox=list(display.source_rect(w['bbox'])*display.page.derotation_matrix),
                        **({'native_parts':[dict(p,bbox=list(display.source_rect(p['bbox'])*display.page.derotation_matrix))for p in w['native_parts']]}if 'native_parts'in w else{}))for w in local]
                    native_proof=drawn_word_authorities(display.page,native_occurrences)
                    legacy_native_words=[w for w,physical_word in zip(local,native_occurrences)if unproved_legacy_word(display.page,physical_word)]
                    native_print_words=[w for w,proved in zip(local,native_proof)if proved]
                    source_print=bool(local) and all(native_proof)
                    if source_print:territories=_native_ink_regions(territories,local)
                    witness=(pixel_adjudication.CropWitness([dict(w,confidence=100)for w in local]) if source_print else
                        pixel_adjudication.read_crop(display,territories,Path(note_context['cache'])/'adjudication'))
                    from difflib import SequenceMatcher
                    baseline=(optical_local if independent_optical_fallback else local or optical_local)
                    if native_print_words:baseline=local
                    if not native_print_words and local and optical_local and not independent_optical_fallback:
                        overlap=sum(n for _,_,n in SequenceMatcher(None,
                            [vision_reading.normalized(w['text']) for w in local],
                            [vision_reading.normalized(w['text']) for w in optical_local],autojunk=False).get_matching_blocks())
                        if overlap/max(1,len(optical_local))<.6:baseline=optical_local
                    if baseline and (witness is not None or native_print_words or legacy_native_words):
                        # Only the admitted alignment inventories reach this
                        # stage. Retain the first reader as an alternative, but
                        # require positive pixels to revive rejected words.
                        first_tokens=list(proposed)
                        # Existing local physical proof reserves reference ink
                        # before lexical conservation can turn it into body words.
                        # This early pass cannot dispatch a marker request. Final
                        # rendering keeps the complete original inventories and
                        # performs its usual verified/disclosed reference binding.
                        marker_corroboration=optical_local;physical={}
                        if role in ('body','other','caption') and any(
                                re.fullmatch(r'<n:[^>]+>',t) for t in first_tokens):
                            local_context=dict(note_context,marker_reader=None)
                            with unit_recovery.guard(pno,'page_compilation',block=bid):
                                physical=_placeholder_display_evidence(first_tokens,
                                    optical_local,display,box,local_context,pno)
                            first_tokens,baseline=_lexical_marker_view(first_tokens,baseline,physical)
                            _,witness=_lexical_marker_view(first_tokens,witness,physical)
                            _,marker_corroboration=_lexical_marker_view(first_tokens,optical_local,physical)
                        if source_print:
                            native_geometry=list(baseline)
                            normalized_baseline=_native_wrap_view(baseline,first_tokens)
                            if normalized_baseline!=baseline:
                                baseline=normalized_baseline;native_print_words=list(baseline)
                                witness=pixel_adjudication.CropWitness([dict(w,confidence=100)for w in baseline])
                                result['guard'].append(dict(kind='native_line_wrap_normalization'))
                        # Conservation failure and interpolated alignment are
                        # block-level routing evidence, not reader uncertainty in
                        # every neighboring word. Actual lexical alternatives
                        # are adjudicated below at their own occurrences.
                        pending=vision_reading.align(block['text'],[],block['low_confidence'],
                            has_layer=False)['marked']
                        if not hasattr(witness,'__dict__'):
                            witness=pixel_adjudication.CropWitness(witness)
                        witness.neighbor_occurrences=pixel_words or ()
                        witness.model_reading=not bool(getattr(witness,'failure',None))
                        if not source_print:
                            baseline=pixel_adjudication.canonical_source_occurrences(
                                first_tokens,baseline,witness,native_print_words)
                        adjudication=pixel_adjudication.adjudicate(first_tokens,
                            baseline,witness,uncertain=pending,corroboration=marker_corroboration,
                            # Inconclusive OCR cannot revive a model passage already
                            # rejected by the source-conservation alignment guard.
                            unresolved_source=bool(result['fallback']),source_print=source_print,native_print_words=native_print_words,
                            absence_proof=lambda left,right:(pixel_adjudication.blank_between(display,left,right,territories)
                                or pixel_adjudication.blank_wrap_margins(display,left,right)))
                        def printed_space(left,right):
                            a,b=left['bbox'],right['bbox'];h=min(a[3]-a[1],b[3]-b[1])
                            return (b[0]-a[2]>=h*.15 and
                                pixel_adjudication.blank_between(display,left,right,territories))
                        conflicts=pixel_adjudication.audit_agreement(adjudication['tokens'],baseline,witness,
                            boundary_proof=printed_space,word_boxes=adjudication['boxes'],
                            conserved_boundaries=adjudication.get('conserved_boundaries'))
                        for i,decision in conflicts.items():
                            if pixel_adjudication.proved_native_occurrence(adjudication['tokens'][i],adjudication['boxes'].get(i),native_print_words):continue
                            adjudication['marked'].add(i)
                            adjudication['word_notes'].setdefault(i,decision)
                        if (result['fallback'] or bid in forced_uncertain) and not native_print_words:
                            refined=pixel_adjudication.refine_fallback(adjudication['tokens'],witness,
                                text_quality.english_lexicon(),source=baseline)
                            if refined is not None:
                                from difflib import SequenceMatcher
                                for kind,a,z,b,y in SequenceMatcher(None,adjudication['tokens'],refined['tokens'],autojunk=False).get_opcodes():
                                    if kind=='equal':
                                        for old_index,new_index in zip(range(a,z),range(b,y)):
                                            if old_index in adjudication['boxes']:
                                                refined['boxes'].setdefault(new_index,adjudication['boxes'][old_index])
                                    affected=([j for i,j in zip(range(a,z),range(b,y)) if i in adjudication['marked']]
                                        if kind=='equal' else list(range(b,y)) if any(i in adjudication['marked'] for i in range(a,z)) else [])
                                    for i in affected:
                                        refined['marked'].add(i)
                                        prior=adjudication['word_notes'].get(a+i-b) if kind=='equal' else None
                                        if prior and prior.get('source_image_only'):
                                            # This pass has not changed the word. Its own
                                            # inconclusive note cannot certify a spelling
                                            # already withheld from live publication.
                                            refined['word_notes'][i]=prior
                                        else:
                                            refined['word_notes'].setdefault(i,prior or dict(source=[refined['tokens'][i]],
                                                chosen=[refined['tokens'][i]],status='UNRESOLVED',method='reader_uncertainty'))
                                refined['decisions']=adjudication['decisions']+refined['decisions']
                                refined['verified_unchanged']=adjudication.get('verified_unchanged',[])
                                adjudication=refined
                        # Restore the original symbolic alternative at its
                        # stable typed reference boundary. The normal original-
                        # glyph verifier still decides whether it is one action.
                        for proof in physical.values():
                            suffix=proof.get('lexical_literal_suffix') or proof.get('unplaced_literal_suffix')
                            marker=proof.get('lexical_placeholder') or proof.get('unplaced_literal_placeholder')
                            positions=[i for i,t in enumerate(adjudication['tokens']) if t==marker]
                            if suffix and len(positions)==1 and positions[0]>0:
                                index=positions[0]-1
                                if not adjudication['tokens'][index].endswith(suffix):
                                    adjudication['tokens'][index]+=suffix
                                decision=adjudication['word_notes'].get(index)
                                if decision and decision['method'] in ('independent_upright_crop',
                                        'two_own_pixel_readings','agreeing_readings_pixel_conflict'):
                                    values=[decision[k] for k in ('source','model','chosen','alternative') if k in decision]
                                    lexical=lambda value:value[:-len(suffix)] if value.endswith(suffix) else value
                                    if (all(len(value)==1 for value in values)
                                            and len({lexical(value[0])for value in values})==1):
                                        # Glyph identity has its own disclosure.
                                        # It is not a correction of this unchanged
                                        # lexical word, nor authority to delete it.
                                        adjudication['marked'].discard(index)
                                        adjudication['word_notes'].pop(index)
                                        adjudication['decisions']=[d for d in adjudication['decisions'] if d is not decision]
                        result.update(tokens=adjudication['tokens'],
                            marked=adjudication['marked'],boxes=adjudication['boxes'])
                        evidence_boxes=adjudication['boxes']
                        result['adjudication']=adjudication['decisions']
                        result['word_notes']=adjudication['word_notes']
                        wraps=_printed_wraps(result['tokens'],optical_local)
            if local_ocr and note_context and note_context.get('cache'):
                from . import pixel_adjudication
                with unit_recovery.guard(pno,'local_pixel_adjudication',block=bid):
                    witness=pixel_adjudication.read_crop(display,territories,
                        Path(note_context['cache'])/'adjudication')
                    if hasattr(witness,'__dict__'):witness.neighbor_occurrences=pixel_words or ()
                    source_occurrences=(source_unit['words'] if local_ocr_units is not None else optical_local)
                    refined=pixel_adjudication.refine_fallback(result['tokens'],witness,
                        text_quality.english_lexicon(),source=source_occurrences,corroboration=optical_local)
                    # Original occurrence inventories are immutable. Only a
                    # positively corroborated one-for-one repair can replace a
                    # local token without changing those source owners.
                    repairs=(refined if refined and len(refined['tokens'])==len(result['tokens'])
                        and all(len(d['source'])==len(d['chosen']) for d in refined['decisions']
                                if d['status']=='RESOLVED') else None)
                    if repairs:
                        result['tokens']=repairs['tokens']
                        result['word_notes']=repairs['word_notes']
                        result['adjudication']=repairs['decisions']
                        for i,decision in repairs['word_notes'].items():
                            if decision['status']=='RESOLVED' and i in repairs['boxes']:
                                evidence_boxes[i]=repairs['boxes'][i]
                        if local_ocr_units is not None:
                            source_unit['published_words']=list(result['tokens'])
                    conflicts=pixel_adjudication.audit_agreement(result['tokens'],source_occurrences,witness)
                    result.setdefault('adjudication',[]).extend(conflicts.values())
                    # Two settings of one local recognizer do not establish
                    # independent certainty. Preserve all local OCR uncertainty
                    # and its exact recognized source occurrence inventory.
                    for i,decision in conflicts.items():result.setdefault('word_notes',{}).setdefault(i,decision)
            if witness is not None and hasattr(witness,'alternates'):
                witness.source_occurrences=source_occurrences if local_ocr else baseline
                pixel_adjudication.disclose_alternate_conflicts(result,evidence_boxes,witness,allow_clear=not local_ocr)
            if getattr(witness,'failure',None):
                # Keep the already admitted reading and its own source owners.
                # Unavailable secondary recognition must not widen a primary
                # optical/marker failure into healthy sibling units.
                result['word_notes']={i:dict(source=[t],model=[t],chosen=[t],status='UNRESOLVED',
                    method='independent_crop_unavailable') for i,t in enumerate(result['tokens'])
                    if i in result['marked'] and not t.startswith('<n:')}
                result['adjudication']=list(result['word_notes'].values())
                result['guard'].append(dict(kind='independent_crop_unavailable',reason=witness.failure))
            if witness is not None and (local_ocr or (bid in forced_uncertain and not native_print_words)):
                # Recovered optical clauses have no independent model reading.
                # Their numeral identity needs the same print retention as a
                # local-only first pass, without downgrading drawn native text.
                pixel_adjudication.audit_local_numeric_suffixes(result,evidence_boxes,witness)
            if local_ocr and witness is not None:
                pixel_adjudication.audit_reverse_local_ink(result,evidence_boxes,witness,display,
                    territories,text_quality.english_lexicon())
                if local_ocr_units is not None:source_unit['published_words']=list(result['tokens'])
            if legacy_native_words:
                unplaced=any(len([i for i,slot in result['boxes'].items()
                    if pixel_adjudication._complete_word_occurrence(w,dict(bbox=slot))])!=1
                    for w in legacy_native_words)
                legacy_ids={id(w)for w in legacy_native_words}
                script_loss=text_quality.reading_script_mismatches(block['text'],' '.join(result['tokens']))
                if ((unplaced or script_loss) and native_print_words and role in ('body','other','caption')
                        and not any(t.startswith('<n:')for t in proposed+result['tokens'])
                        and all(proved or id(w)in legacy_ids for w,proved in zip(local,native_proof))):
                    from .native_text import visible_word_ink
                    if all(visible_word_ink(display.page,native_occurrences)):
                        # A reader can expand opaque codes into several tokens,
                        # or replace each with a script the font cannot map. The
                        # complete visible native inventory proves the prose;
                        # only opaque occurrences require original ink. This
                        # containment applies whether their token binding was
                        # already unique or must be restored from native print.
                        result.update(tokens=[w['text']for w in local],fallback=False,native_ink_stencil=True,
                            boxes={i:w['bbox']for i,w in enumerate(local)},marked=set(),
                            word_notes={},adjudication=[],
                            verified_unchanged=[i for i,proved in enumerate(native_proof)if proved])
                        result.setdefault('guard',[]).append(dict(kind='visible_native_ink_stencil'))
                        evidence_boxes=result['boxes']
                        adjudication=dict(verified_unchanged=result['verified_unchanged'])
                        wraps=_printed_wraps(result['tokens'],optical_local)
            for legacy_word in legacy_native_words:
                occurrences=[i for i,box in result['boxes'].items()
                    if pixel_adjudication._complete_word_occurrence(legacy_word,dict(bbox=box))]
                if len(occurrences)!=1:
                    raise _DisclosureQualityRejection('Unmapped glyph has no unique published occurrence')
                index=occurrences[0]
                decision=dict(source=[legacy_word['text']],model=[result['tokens'][index]],
                    chosen=[result['tokens'][index]],status='UNRESOLVED',
                    method='unproved_native_character_map',source_image_only=True,
                    source_bbox=legacy_word['bbox'])
                if 'native_parts'in legacy_word:decision['source_parts']=legacy_word['native_parts']
                result['marked'].add(index)
                result.setdefault('word_notes',{})[index]=decision
                result.setdefault('adjudication',[]).append(decision)
            if adjudication:
                result['verified_unchanged']=sorted(set(result.get('verified_unchanged',[]))|
                    set(adjudication.get('verified_unchanged',[])))
            from . import reading_tiers
            # Keep the independent optical bindings when reindexing words.
            # The original layer's rectangles may describe unrelated blank paper.
            result['boxes']=evidence_boxes
            reading_tiers.conserve_punctuation_boundaries(result,baseline if adjudication else local,first_tokens if adjudication else proposed)
            _remove_foreign_word_runs(result,bid,answer,pixel_words or [],regions)
            reading_tiers.join_letter_spaced_readings(result,witness or [])
            reading_tiers.join_fragmented_readings(result,witness if witness is not None else [])
            reading_tiers.discard_dot_leaders(result,display,pixel_words or local)
            if any(g.get('kind')in('foreign_owned_run_published_once','letter_spaced_printed_words')
                   for g in result['guard']):
                wraps=_printed_wraps(result['tokens'],optical_local)
            evidence_boxes=result['boxes']
            compiled_reading=' '.join(result['tokens'])
            lost_scripts=(text_quality.reading_script_mismatches(block['text'],compiled_reading)
                          if result['fallback'] else text_quality.script_mismatches(block['text'],compiled_reading))
            if lost_scripts and not result.get('native_ink_stencil'):
                # An English optical fallback cannot replace a reader's non-Latin
                # passage with Latin lookalikes. Retain original pixels when this
                # compiler cannot safely bind the reading in its printed script.
                raise ValueError('Source-script reading lost during alignment: '+', '.join(lost_scripts))
            # A whole chosen reading reduced to a few optical fragments is not a
            # usable transcript, even when those fragments are dictionary words or
            # punctuation. Measure the complete block: a local replacement may
            # correctly remove a long borrowed model tail from healthy source prose.
            # Short chosen fragments still receive the separate lexical check below.
            model_count=len(re.sub(r'<n:[^>]+>', ' ', block['text']).split())
            chosen_count=sum(not token.startswith('<n:') for token in result['tokens'])
            contracted=(model_count>=vision_reading.UNALIGNED_RUN
                        and chosen_count<.25*model_count
                        and ''.join(block['text'].split())!=''.join(result['tokens']))
            if result['fallback'] and contracted:
                raise _DisclosureQualityRejection('Unreliable contracted fallback requires original source disclosure')
            # A punctuation-only model variant does not turn an unchanged
            # source citation into a new short purported reading. Keep its
            # ordinary page-quality check and each actual chosen span's short
            # check; lexical substitutions and fallbacks retain the stronger
            # complete-reading check.
            source_reading=' '.join(w['text'] for w in (local or optical_local))
            lexical_change=(re.findall(r'\w+',compiled_reading)!=re.findall(r'\w+',source_reading))
            complete_check=(text_quality.inspect_disclosure if result['fallback'] or lexical_change
                            else text_quality.inspect)
            if ((result['fallback'] or result['marked'])
                    and complete_check(compiled_reading)['needs_read']):
                raise _DisclosureQualityRejection('Unreliable chosen reading requires original source disclosure')
            reading_tiers.settle_typographic_equivalents(result)
            reading_tiers.settle_repeated_word_shapes(result,display)
            flagged_readings=reading_tiers.prepare(result)
            literal={};placeholder_display={}
            if note_context and role in ('body','other','caption'):
                note_optical=_adjacent_source_note_glyphs(optical_local,pixel_words or [])
                source_context=dict(note_context,source_words=pixel_words)
                args=(result['tokens'],note_optical,display,box,note_context['cache'],note_context['labels'],pno,source_context)
                literal=(_literal_note_refs(*args,allow_omitted=True) if role=='caption'
                         else _literal_note_refs(*args))
                placeholder_display=_placeholder_display_evidence(result['tokens'],optical_local,display,box,note_context,pno,literal)
            individual_receipts={i for i,d in result.get('word_notes',{}).items()
                if d.get('source') and (d.get('source')!=d.get('chosen') or
                    d.get('model') and d.get('model')!=d.get('chosen'))}
            groups=[]
            for index in sorted(i for i in result['marked'] if not result['tokens'][i].startswith('<n:')):
                resolved_local={i for i,d in (result.get('word_notes') or {}).items() if d['status']=='RESOLVED'} if local_ocr else set()
                image_words={i for i,d in (result.get('word_notes') or {}).items() if d.get('source_image_only')}
                if ((local_ocr or not result.get('adjudication')) and groups and index==groups[-1][-1]+1
                        and (index in flagged_readings)==(groups[-1][-1] in flagged_readings)
                        and index not in literal and groups[-1][-1] not in literal
                        and index not in individual_receipts and groups[-1][-1] not in individual_receipts
                        and index not in resolved_local and groups[-1][-1] not in resolved_local
                        and index not in image_words and groups[-1][-1] not in image_words):groups[-1].append(index)
                else:groups.append([index])
            # Adjacent plausible words on one printed row share one reading
            # underline and one adjacent print preview. A typed reference,
            # resolved correction or another physical row ends the run.
            merged=[]
            for group in groups:
                previous=merged[-1]if merged else []
                if (previous and group[0]==previous[-1]+1
                        and len(previous)+len(group)<=8
                        and not any(i in literal or i in individual_receipts for i in previous+group)
                        and all(i in flagged_readings and i in evidence_boxes for i in previous+group)):
                    a=evidence_boxes[previous[-1]];b=evidence_boxes[group[0]]
                    same_row=min(a[3],b[3])-max(a[1],b[1])>=.5*min(a[3]-a[1],b[3]-b[1])
                    if same_row and -.5<=b[0]-a[2]<=1.5*max(a[3]-a[1],b[3]-b[1]):
                        previous.extend(group);continue
                merged.append(list(group))
            groups=merged
            endings={g[-1]:g for g in groups}
            print_groups=reading_tiers.bound_print_runs(groups,flagged_readings,evidence_boxes)
            print_endings={g[-1]:g for g in print_groups if all(i in flagged_readings for i in g)}
            print_owners={i:g[-1] for g in groups for i in g}
            emission_groups={**endings,**print_endings}

            def flagged_piece(indices, ident, inspection_src, reading_html=None):
                # Presentation runs may be shorter than the original warning.
                # Every run links to that same complete, source-owned receipt;
                # only the final run needs its dagger and return origin.
                # Placement and ink validation happen once after the complete
                # published sequence exists. Rendering a guessed preview here
                # would leave an unused raster when that placement is refused.
                inline_src=inspection_src
                style='height:1.1em;vertical-align:-0.15em;width:auto'
                reading=''.join(('' if j==0 or i in wraps else ' ')+result['tokens'][i]
                    for j,i in enumerate(indices))
                return reading_tiers.flagged_html(reading,inline_src,ident,style,reading_html=reading_html)
            # Reject each chosen span before emitting markers or source images.
            # Keep the rendering-time check as a defensive publication boundary.
            for group in groups:
                chosen_reading=' '.join(result['tokens'][i] for i in group)
                if text_quality.inspect_disclosure(chosen_reading)['needs_read']:
                    raise _DisclosureQualityRejection('Unreliable chosen reading requires original source disclosure')
            display_tokens=list(result['tokens'])
            for proof in placeholder_display.values():
                if 'deduplicated_literal_token' in proof and not literal.get(proof['deduplicated_literal_token']):
                    display_tokens[proof['deduplicated_literal_token']]=proof['literal_display_text']
            pieces=[];tokens_count+=len(result['tokens'])
            for index,token in enumerate(display_tokens):
                marker=re.fullmatch(r'<n:([^>]+)>',token)
                if marker:
                    nid=marker[1];ref=f'v4-ref-{pno}-{bid}-{index}'
                    owner=note_context['owners'].get((pno,nid),(pno,nid)) if note_context else (pno,nid)
                    if nid in unresolved_notes:
                        from .unit_source import source_unit
                        proof=placeholder_display.get(index,dict(status='UNPROVED',
                            source_bbox=list(box),reason=_MARKER_SLOT_UNBOUND))
                        reason='Printed note association requires original source inspection'
                        _,contexts,unit=source_unit(display,pno,f'marker-{bid}-{index}','other',
                            package,image_records,reason,bbox=proof['source_bbox'])
                        extras.extend(contexts)
                        label=proof['label'] if proof['status']=='PROVED' else '†'
                        pieces.append(f'<a id="{unit["source_reference_origin"]}" class="source-disclosure-ref" epub:type="noteref" href="#{unit["source_reference"]}"><sup>{escape(label)}</sup></a>')
                        marker_record=dict(proof,**unit)
                        marker_record.update(page=pno,block=bid,token=index,note_owner=list(owner),
                            display_label=label,association_status='UNPROVED',
                            unit_kind='note_marker',status='DEGRADED_SOURCE_CONTEXT')
                        marker_disclosures.append(marker_record)
                        marked+=1
                        continue
                    # A literal and an adjacent placeholder can describe the same
                    # original reference. Reconcile only one proven source glyph;
                    # two distinct original glyphs keep their separate actions.
                    evidence=getattr(literal,'source_instances',{})
                    sole=getattr(literal,'sole_source_instances',set())
                    repeated=any(bound_owner==owner and
                        re.fullmatch(r'''[.,;:)\]”’"']*''',result['tokens'][index-1][b:]) and
                        len(evidence.get((index-1,a,b,bound_owner,label),[]))==1 and
                        (index-1,a,b,bound_owner,label) in sole
                        for a,b,bound_owner,label in literal.get(index-1,[]))
                    display_proof=placeholder_display.get(index,{})
                    repeated=repeated or bool(display_proof.get('deduplicated_literal_token')==index-1
                        and any(bound_owner==owner for a,b,bound_owner,label in literal.get(index-1,[])))
                    if repeated:
                        pieces.append('');continue
                    refs.setdefault(nid,[]).append(ref)
                    target=note_context['catalogue'][owner] if note_context else {'target':f'v4-note-{pno}-{nid}','label':footnotes[nid]['marker'] or '*'}
                    if note_context:note_context['refs'].setdefault(owner,[]).append(ref)
                    proof=placeholder_display.get(index)
                    label=proof['label'] if proof and proof['status']=='PROVED' else target['label']
                    piece=f'<a id="{ref}" class="noteref" epub:type="noteref" href="#{target["target"]}"><sup>{escape(label)}</sup></a>'
                    if proof:
                        marker_record=dict(page=pno,block=bid,token=index,note_owner=list(owner),
                            catalogue_label=target['label'],display_label=label,**proof)
                        if proof['status']=='PROVED':marker_repairs.append(marker_record)
                        else:
                            from .unit_source import source_unit
                            ident=f'marker-{bid}-{index}'
                            reason='Printed body note marker is uncertain: '+proof['reason']
                            shared=None
                            # Only an already emitted, immediately adjacent action
                            # can share its SOURCE target. Actual image territory,
                            # not a requested crop or an enclosing block hint, must
                            # contain the complete unresolved marker evidence.
                            if index-1 in endings:
                                dest=f'v4-d-{pno}-{bid}-{index-1}';origin='r-'+dest
                                src='images/'+dest+'.jpg';image=image_records.get(src,{})
                                crop=image.get('bbox');needed=proof['source_bbox']
                                matches=[j for j,aside in enumerate(extras) if f'id="{dest}"' in aside]
                                if (image.get('page')==pno and image.get('kind')=='vision_disclosure'
                                        and crop and len(matches)==1
                                        and crop[0]<=needed[0] and crop[1]<=needed[1]
                                        and crop[2]>=needed[2] and crop[3]>=needed[3]):
                                    j=matches[0]
                                    extras[j]=extras[j].replace('<p>','<p>'+escape(reason)+'. ',1)
                                    shared=dict(page=pno,block=ident,source_bbox=list(crop),unit_bbox=list(needed),
                                        source_image=src,image=src,source_reference=dest,source_reference_origin=origin,
                                        reason=reason,shared_existing_source_disclosure=True)
                            if shared is not None:
                                unit=shared
                            else:
                                _,contexts,unit=source_unit(display,pno,ident,'other',package,image_records,
                                    reason,bbox=proof['source_bbox'])
                                extras.extend(contexts);origin=unit['source_reference_origin'];dest=unit['source_reference']
                                piece+=f'<a id="{origin}" class="source-disclosure-ref" epub:type="noteref" href="#{dest}"><sup>†</sup></a>'
                                marked+=1
                            marker_record.update(unit,unit_kind='note_marker',status='DEGRADED_SOURCE_CONTEXT')
                            marker_disclosures.append(marker_record)
                    pieces.append(piece);continue
                if index in flagged_readings and index not in print_endings:
                    pieces.append('');continue
                piece=escape(token)
                if index in literal:
                    start=0;parts=[]
                    for a,b,owner,label in sorted(literal[index]):
                        ref=f'v4-ref-{pno}-{bid}-{index}-{a}'
                        parts.append(escape(token[start:a])+f'<a id="{ref}" class="noteref" epub:type="noteref" href="#{note_context["catalogue"][owner]["target"]}"><sup>{escape(label)}</sup></a>')
                        note_context['refs'].setdefault(owner,[]).append(ref);start=b
                    piece=''.join(parts)+escape(token[start:])
                if index in print_endings and index not in endings:
                    ident=f'v4-d-{pno}-{bid}-{print_owners[index]}'
                    piece=flagged_piece(print_endings[index],ident,f'images/{ident}.jpg',
                        reading_html=piece if index in literal else None)
                if index in endings:
                    chosen_reading=''.join((''if j==0 or i in wraps else' ')+result['tokens'][i]
                        for j,i in enumerate(endings[index]))
                    if text_quality.inspect_disclosure(chosen_reading)['needs_read']:
                        raise _DisclosureQualityRejection('Unreliable chosen reading requires original source disclosure')
                    marked+=1;ident=f'v4-d-{pno}-{bid}-{index}';ref='r-'+ident
                    src=f'images/{ident}.jpg'
                    word_decision=(result.get('word_notes')or{}).get(index,{})
                    boxes=([word_decision['source_bbox']] if len(endings[index])==1 and word_decision.get('source_bbox') and (word_decision.get('source_image_only') or word_decision.get('source_image_only_before_publication')) else
                           [evidence_boxes[i] for i in endings[index] if i in evidence_boxes])
                    crop=(word_decision['source_bbox'] if len(endings[index])==1 and word_decision.get('source_bbox') and (word_decision.get('source_image_only') or word_decision.get('source_image_only_before_publication')) else
                          vision_reading.union(boxes) if len(boxes)==len(endings[index]) else box)
                    if word_decision.get('source_parts'):
                        # A divided word owns both physical pieces, which can
                        # span rows. Its tail box alone cannot enclose its print.
                        crop=vision_reading.union([part['bbox']if isinstance(part,dict)else part
                            for part in word_decision['source_parts']])
                    # Tight evidence remains in final notes, never an inline word crop.
                    clip=extract.pymupdf.Rect(crop);clip.x0-=2;clip.y0-=2;clip.x1+=2;clip.y1+=2;clip &= display.rect
                    _word_disclosure_image(package,src,display,clip,word_decision.get('source_parts'))
                    image_records[src]=dict(page=pno,bbox=list(clip),kind='vision_disclosure',
                        **({'source_parts':copy.deepcopy(word_decision['source_parts'])}
                           if word_decision.get('source_parts')else {}))
                    label=('Unverified local OCR reading' if local_ocr else
                        'Uncertain source reading fallback' if result['fallback'] else
                        'Model reading differs from source layer' if words else 'Low-confidence model reading')
                    if local_ocr:
                        source_quote=('This local OCR transcription has not been independently verified. '
                            'Consult the original printed reading in the image. '
                            f'<a href="#v4-local-ocr-source-{pno}">View complete original page</a>.')
                    elif original_quality['needs_read']:
                        source_quote='The text layer is unreliable; consult the original printed reading in the image.'
                        suppression=source_quote_suppressions.setdefault(bid,dict(block=bid,quality=original_quality,disclosure_count=0))
                        suppression['disclosure_count']+=1
                    else:
                        source_quote='Source reading: '+escape(' '.join(owned_word['text'] for owned_word in local if any(owned_word['bbox']==evidence_boxes.get(i) for i in endings[index])))+'.'
                    decision=(result.get('word_notes') or {}).get(index)
                    if len(endings[index])>1 and all(i in flagged_readings for i in endings[index]):
                        members=[result.get('word_notes',{}).get(i,{})for i in endings[index]]
                        decision=dict(source=[s for d in members for s in d.get('source',[])],
                            model=[s for d in members for s in d.get('model',[])],
                            chosen=[result['tokens'][i]for i in endings[index]],status='UNRESOLVED',
                            method='contiguous_unproved_reading')
                    if decision:
                        label=(('Unverified local OCR reading; pixel-supported correction' if decision['status']=='RESOLVED'
                                else 'Unverified local OCR reading') if local_ocr else
                               'Pixel-verified correction' if decision['status']=='RESOLVED' else 'Unresolved source reading')
                        before=(decision.get('model',[]) if decision['source']==decision['chosen']
                                and decision.get('model',[])!=decision['chosen'] else decision['source'])
                        source_quote=(escape(' '.join(before)) or '[no word]')+' → '+(escape(' '.join(decision['chosen'])) or '[no word]')+'.'
                    if local_ocr:
                        explanation=((source_quote+' ') if decision and (decision['status']=='RESOLVED' or decision.get('source')!=decision.get('chosen')) else '')+'This local OCR transcription has not been independently verified. Consult the original printed reading in the image. '+f'<a href="#v4-local-ocr-source-{pno}">View complete original page</a>.'
                    elif decision:
                        if decision.get('method')=='complete_vision_reading_without_optical_counterevidence':
                            explanation=source_quote+' The complete vision reading is retained. The optical alternative has no complete competing word. Compare the original printed word in the image.'
                        elif decision.get('mapping')=='unplaced_within_span':
                            explanation=escape(' '.join(decision['chosen']))+'. Its source-word correspondence is unresolved. Compare the original printed span in the image.'
                        elif decision['source']==decision['chosen'] and decision.get('model',decision['source'])==decision['chosen']:
                            explanation=escape(' '.join(decision['chosen']))+'. Consult the original printed word in the image.'
                        else:explanation=source_quote+(' An independent upright crop supports this correction.' if decision['status']=='RESOLVED' else ' An independent upright crop could not settle this difference.')+' Compare the original printed word in the image.'
                    else:explanation=escape(' '.join(result['tokens'][i] for i in endings[index]))+'. '+source_quote
                    if decision and decision.get('source_image_only'):
                        explanation=((source_quote+' ')if decision['source']!=decision['chosen']else '')+'The independent pixel readings do not settle this printed word. Read the original image.'
                    if original_quality['needs_read'] and not local_ocr:
                        explanation='The text layer is unreliable; consult the original printed reading in the image. '+explanation
                    if index in flagged_readings:
                        label='Unverified local OCR reading'if local_ocr else'Unresolved source reading'
                        explanation='Plausible reading; it has not been independently proved. Compare the adjacent original print. '+explanation
                    extras.append(f'<aside id="{ident}" class="footnote vision-disclosure" data-reflow-owner-page="{pno}" data-reflow-owner-block="{escape(bid,quote=True)}" epub:type="footnote"><p>{label}: {explanation}</p><img src="{src}" alt="Original printed reading"/><a href="#{ref}">Return</a></aside>')
                    if index in flagged_readings:
                        piece=flagged_piece(print_endings[index],ident,src,reading_html=piece if index in literal else None)
                    elif decision and decision.get('source_image_only'):
                        from . import inline_print
                        inline_src=src;inline_style='height:1.4em;vertical-align:-0.25em;width:auto'
                        preview=(inline_print.preview(display,crop)
                                 if not word_decision.get('source_parts') and len(boxes)==len(endings[index]) else None)
                        if preview is not None and hasattr(package,'image'):
                            data,metrics=preview
                            inline_src=f'images/{ident}-inline.png'
                            package.image(inline_src,data)
                            image_records[inline_src]=dict(page=pno,kind='inline_print_preview',coverage_eligible=False,
                                inspection_image=src,**metrics)
                            inline_style=metrics['style']
                        piece=(f'<a class="source-word-image" href="#{ident}"><img src="{inline_src}" '
                            f'alt="Unresolved printed word; inspect original" style="{inline_style}"/></a>')
                    piece+=f'<a id="{ref}" class="source-disclosure-ref" epub:type="noteref" href="#{ident}"><sup>†</sup></a>'
                if local_ocr and index in result['marked']:
                    piece='<span class="uncertain" title="Unverified local OCR reading">'+piece+'</span>'
                pieces.append(piece)
            _coalesce_inline_source_images(pieces,display,pno,bid,package,image_records)
            from . import occurrence_coverage
            inventory=(raw['words']if local_ocr and local_ocr_units is not None and raw else optical_local or local)
            coverage=occurrence_coverage.classify(inventory,result['tokens'],evidence_boxes,
                result.get('word_notes',{}),image_records,pno)
            for row in coverage:
                if any(occurrence_coverage._contains(region,row['source_bbox'])for region in result.get('decorative_source_regions',[])):
                    row.update(disposition='excluded',rule='measured_contents_dot_leader',published_indices=[])
            typed_occurrences=[dict(source_bbox=proof['source_bbox'],label=proof['label'],
                target=f'v4-ref-{pno}-{bid}-{index}')for index,proof in placeholder_display.items()
                if proof.get('status')=='PROVED'and proof.get('source_bbox')]
            if opening_proof and opening_proof['identity_status']=='PROVED':
                typed_occurrences.append(dict(source_bbox=opening_proof['source_bbox'],
                    label=block['marker'],target=f'v4-note-{pno}-{bid}'))
            for row in coverage:
                matches=[proof for proof in typed_occurrences
                    if occurrence_coverage._key(proof['label'])==occurrence_coverage._key(row['source_text'])
                    and occurrence_coverage._contains(proof['source_bbox'],row['source_bbox'])]
                if len(matches)==1:row.update(disposition='live',typed_reference=matches[0]['target'])
            # A proved printed note label sits outside the lexical note span.
            # Account for its own source occurrence and actual target explicitly.
            for proof in typed_occurrences:
                if not any(occurrence_coverage._key(row['source_text'])==occurrence_coverage._key(proof['label'])
                        and occurrence_coverage._contains(proof['source_bbox'],row['source_bbox'])for row in coverage):
                    coverage.append(dict(source_text=proof['label'],source_bbox=proof['source_bbox'],
                        disposition='live',typed_reference=proof['target'],published_indices=[]))
            missing=[row for row in coverage if row['disposition']=='missing']
            if missing:
                # A replacement optical inventory cannot silently erase source
                # occurrences. Keep healthy live words; retain only this gap's
                # unresolved ownership in an explicit original-print context.
                from .unit_source import source_unit
                _,contexts,unit=source_unit(display,pno,'coverage-'+bid,'other',package,image_records,
                    'Source occurrences without a unique live-word binding are retained as original print',
                    bbox=vision_reading.union([row['source_bbox']for row in missing]))
                extras.extend(contexts)
                origin=unit['source_reference_origin'];target=unit['source_reference']
                pieces.append(f'<a id="{origin}" class="source-disclosure-ref" epub:type="noteref" href="#{target}"><sup>†</sup></a>')
                marked+=1
                for row in missing:row.update(disposition='print',image=unit['image'],
                    source_reference=target,method='unplaced_occurrence_original_print')
            text=''.join(('' if (emission_groups.get(i,[i])[0]==0 or emission_groups.get(i,[i])[0] in wraps) else ' ')+piece
                         for i,piece in enumerate(pieces) if piece)
            text,unit_print_html=_present_flagged_prints(text,display,pno,bid,territories,
                package,image_records,extras,evidence_words=pixel_words or local,heading=role=='heading',
                protected_inspections={item['source_reference']for item in marker_disclosures})
            # The marker is typographic punctuation, not a standalone sentence block.
            text=re.sub(r'\s+(<a[^>]*class="noteref".*?</a>)',r'\1',text)
            text=(f'<span data-source-page="{pno}" data-source-block="{escape(bid,quote=True)}" '
                  f'data-source-regions="{escape(json.dumps(territories),quote=True)}">{text}</span>')
            tag='h2' if role=='heading' else 'p'
            html=f'<{tag}>{text}</{tag}>'+unit_print_html
            if role=='footnote':
                label_html=escape(block['marker'])
                if opening_proof and opening_proof['identity_status']=='UNPROVED':
                    bounds=opening_proof['source_bbox'];receipts=(note_context or {}).get('marker_receipts',{})
                    # Reuse a verified partition/opening receipt at this exact
                    # glyph. A geometry reservation alone never proves identity.
                    def same_slot(receipt):
                        box=receipt.get('source_bbox') or []
                        return len(box)==4 and all(box[i]-1<=bounds[i] for i in (0,1)) and all(bounds[i]<=box[i]+1 for i in (2,3))
                    verified=any(same_slot(r) and r.get('label')==block['marker'] and not r.get('uncertain',True)
                        for r in receipts.get(pno,[]))
                    reader=(note_context or {}).get('marker_reader')
                    if not verified and reader:
                        try:
                            verified=_verify_note_glyph(display,bounds,reader,receipts,pno,'footnote_opening')==block['marker']
                        except (vision_reading.model.ModelError,vision_reading.model.CapExceeded) as error:
                            # This optional label read may be unavailable before
                            # dispatch. Its existing local source control owns
                            # that uncertainty; do not discard readable note text.
                            # Cancellation, ambiguous billing and unexpected
                            # failures retain their ordinary recovery behavior.
                            if not (isinstance(error,vision_reading.model.CapExceeded) or
                                    type(error) is vision_reading.model.ModelError and
                                    str(error)=='Printed marker absent from offline cache'):raise
                            receipts.setdefault(pno,[]).append(dict(label='',uncertain=True,
                                deferred=True,request_dispatched=False,new_cost_usd=0.,cached=False,
                                source_bbox=list(bounds),kind='footnote_opening',
                                reason='opening_glyph_not_verified_without_dispatch'))
                    if not verified:
                        from .unit_source import source_unit
                        _,contexts,unit=source_unit(display,pno,'opening-'+bid,'other',package,image_records,
                            'Printed note opening label requires original source inspection',bbox=bounds)
                        extras.extend(contexts)
                        label_html=(f'<a id="{unit["source_reference_origin"]}" class="source-disclosure-ref" epub:type="noteref" '
                            f'href="#{unit["source_reference"]}">{label_html}</a>')
                        marker_disclosures.append(dict(unit,page=pno,block=bid,unit_kind='note_marker',
                            status='DEGRADED_SOURCE_CONTEXT',opening_identity='UNPROVED'))
                        marked+=1
                asides.append((bid,f'<aside id="v4-note-{pno}-{bid}" class="footnote" epub:type="footnote"><p>{label_html} {text}</p>'+unit_print_html))
            elif role=='caption':body.append(f'<p class="caption">{text}</p>'+unit_print_html)
            else:body.append(html)
            geometry_words=native_geometry if native_geometry is not None else optical_local
            geometry_rows=_pixel_row_ids(geometry_words) if native_geometry is not None else pixel_rows
            printed=owned_words(geometry_words,territories[0])
            rows=_pixel_lines(printed)
            # Column skew far below the opening is not a paragraph indent. Compare
            # the opening with nearby subsequent line starts in its own territory.
            import statistics
            opening_left=(statistics.median(min(w['bbox'][0] for w in row) for row in rows[1:4])
                          if len(rows)>1 else None)
            # Original-ink word images carry no live spelling. Rejected OCR
            # labels must not become structural anchors for that publication.
            live_tokens=[t for i,t in enumerate(result['tokens'])
                if not (result.get('word_notes',{}).get(i,{}).get('source_image_only') or
                        result.get('word_notes',{}).get(i,{}).get('source_image_only_before_publication'))]
            printed_head,printed_tail=_published_pixel_edges(live_tokens,geometry_words)
            head_variant=None
            visible=[t for t in result['tokens'] if not t.startswith('<n:')]
            if printed_head is None and visible and optical_local:
                published=re.fullmatch(r'[-­‐‑]+([a-z]{2,})[.,;:]?',visible[0])
                source=re.fullmatch(r"['’‘\"\-­‐‑]*([a-z]{2,})[.,;:]?",optical_local[0]['text'])
                if published and source and published[1]==source[1]:
                    head_variant={'published':visible[0],'source':optical_local[0]['text'],
                                  'letters':source[1],'bbox':optical_local[0]['bbox']}
            art_gap=None
            if role=='caption' and printed_head is None and printed_tail is None:
                at=answer['blocks'].index(block)
                adjacent=[next((x for x in sequence if x['role'] not in ('caption','running_head','folio')),None)
                          for sequence in (reversed(answer['blocks'][:at]),answer['blocks'][at+1:])]
                candidates=[]
                for art in adjacent:
                    if art is None or art['role'] not in ('figure','table'):continue
                    source=(regions or {}).get(art['id'])
                    degraded=(figure_cases or {}).get(art['id'])
                    if not source and not degraded:continue
                    if art['role']=='table':
                        from . import vision_pixels
                        if not vision_pixels.table_rules(display,block_rect(art,display)):continue
                    bound=vision_reading.union(source) if source else block_rect(art,display)
                    gap=max(0,bound[1]-box[3],box[1]-bound[3])
                    horizontal=max(0,bound[0]-box[2],box[0]-bound[2])
                    candidates.append((gap+horizontal,art,bound))
                if candidates:
                    _,art,bound=min(candidates,key=lambda value:value[0])
                    # Structural attachment moves the caption with its independently
                    # source-bound object. Exact caption territories still identify
                    # its own words; an unmatched hint receives no invented anchors.
                    art_gap={'object':art['id'],'bbox':bound}
            alignment.append(dict(id=bid,role=role,bbox=box,source_regions=territories,
                source_rows=[vision_reading.union([w['bbox'] for w in row])
                             for territory in territories for row in _pixel_lines(owned_words(geometry_words,territory))],
                first_line_x=min((w['bbox'][0] for w in rows[0]),default=None) if rows else None,
                opening_column_left=opening_left,
                printed_tail=printed_tail['text'] if printed_tail is not None else None,
                printed_head=printed_head['text'] if printed_head is not None else None,
                printed_tail_box=printed_tail['bbox'] if printed_tail is not None else None,
                printed_head_box=printed_head['bbox'] if printed_head is not None else None,
                printed_tail_row=geometry_rows.get(id(printed_tail.get('_source_word',printed_tail))) if printed_tail is not None else None,
                printed_head_row=geometry_rows.get(id(printed_head.get('_source_word',printed_head))) if printed_head is not None else None,
                physical_head=(dict(text=geometry_words[0]['text'],bbox=geometry_words[0]['bbox']) if geometry_words else None),
                physical_tail=(dict(text=geometry_words[-1]['text'],bbox=geometry_words[-1]['bbox']) if geometry_words else None),
                disputed_head_prefix=head_variant,source_art_gap=art_gap,
                punctuation_source_anchors=[dict(source_word_index=i,
                    lexical_whitespace_token_index=j,source_bbox=optical_evidence[i]['bbox'])
                    for i,j in punctuation_proof],
                occurrence_coverage=coverage,
                decorative_source_regions=result.get('decorative_source_regions',[]),
                published_word_boxes={str(i):list(box)for i,box in evidence_boxes.items()},
                published_tokens=result['tokens'],
                marked=sorted(result['marked']),fallback=result['fallback'],guard=result['guard'],
                flagged_readings={str(i):choice for i,choice in flagged_readings.items()},
                adjudication=result.get('adjudication',[]),source_image_words=sorted(i for i,d in (result.get('word_notes')or{}).items()if d.get('source_image_only')),verified_unchanged=sorted(set(result.get('verified_unchanged',[]))),original_model_tokens=original_model_tokens,
                layer_words=[w['text'] for w in local],dropped_layer_words=result['dropped_layer_words'],
                continues_previous=block['continues_previous'],continues_next=block['continues_next']))
            if role=='caption' and punctuation_proof and printed_head is not None and printed_tail is not None:
                # Uniqueness must cover the original page, not just locally owned
                # words: a displaced hint could otherwise hide a second caption.
                complete=_caption_punctuation_anchors(pixel_words,
                    list(range(len(pixel_words))),block['text']) if pixel_words else []
                local_box=vision_reading.union([optical_evidence[i]['bbox'] for i,j in punctuation_proof])
                complete_box=vision_reading.union([pixel_words[i]['bbox'] for i,j in complete]) if complete else None
                if complete_box==local_box:
                    # Structural location only; original context stays whole.
                    alignment[-1]['source_complete_caption_bbox']=local_box
            if optical_local:
                # Whole original optical tokens can belong to only one live
                # reading unit. Equal spelling at another rectangle is distinct;
                # fused/partial tokens and interpolated alignment boxes prove no
                # ownership. Only successfully compiled blocks make claims.
                from difflib import SequenceMatcher
                lexical=lambda text:re.sub(r'[^\w]', '', vision_reading.normalized(text).casefold())
                source=[(word,lexical(word['text'])) for word in optical_local
                        if lexical(word['text'])]
                chosen=[lexical(token) for token in result['tokens']
                        if not token.startswith('<n:') and lexical(token)]
                matched=SequenceMatcher(None,[key for word,key in source],chosen,autojunk=False)
                for first,_,count in matched.get_matching_blocks():
                    for word,key in source[first:first+count]:
                        pixel_claims.setdefault(_source_instance(word),set()).add(bid)
                if independent_optical_fallback:optical_fallbacks.add(bid)
        except _DisclosureQualityRejection as error:
            controller=unit_recovery.current()
            if controller is None:raise
            # Refine only against source-bound admitted unit identities,
            # including omitted-line recovery registered before compilation.
            admitted={b['id'] for b in controller.source_answer(pno)['blocks']}
            owned=block['id'] if block['id'] in admitted else None
            units=controller.refine_units([dict(page=pno,block=owned)],error)
            quality_failures.extend(dict(unit,source_failure_reason=str(error)) for unit in units)
    borrowed={bid for claimants in pixel_claims.values() if len(claimants)>1
              for bid in claimants if bid in optical_fallbacks}
    if borrowed:
        error=_DisclosureQualityRejection('Optical fallback has nonunique source ownership; original source disclosure required')
        controller=unit_recovery.current()
        if controller is None:raise error
        units=controller.refine_units([dict(page=pno,block=bid) for bid in sorted(borrowed)],error)
        quality_failures.extend(dict(unit,source_failure_reason=str(error)) for unit in units)
    if quality_failures:
        raise _CompilationQualityFailure(quality_failures)
    note_html=[]
    for nid,html in asides:
        backlinks='' if note_context else ' '.join(f'<a href="#{ref}">Return</a>' for ref in refs.get(nid,[]))
        note_html.append(html+backlinks+'</aside>')
    from .occurrence_coverage import summary as coverage_summary
    return {'pno':pno,'anchor':f'<span id="pg_{pno:04d}"></span>',
            'body':body,'asides':note_html+extras},dict(blocks=alignment,marks=marked,words=tokens_count,
            occurrence_coverage=coverage_summary([row for block in alignment for row in block.get('occurrence_coverage',[])]),
            unlinked_notes=sorted(set(footnotes)-set(refs)),source_figure_contexts=list((figure_cases or {}).values()),
            live_table_cells=live_table_cells,note_marker_disclosures=marker_disclosures,note_marker_repairs=marker_repairs,
            source_quote_suppressions=list(source_quote_suppressions.values()),
            **({'local_ocr_units':final_local_units} if local_ocr and local_ocr_units is not None else {}))


def bind_geometry(answer,optical,display,regions=None,body_owners=None):
    """Bind live source ownership to pixel anchors, including valid wide boxes.

    Word anchors bind text to pixels; they never rewrite the transcription. A
    block that cannot be located remains an explicit conversion failure.
    """
    from difflib import SequenceMatcher
    import statistics
    answer=copy.deepcopy(answer);repairs=[]
    def reject(message):
        # The current response unit owns this error. A previous loop's note
        # object must not turn an unplaced block into a page-wide rejection.
        if unit_recovery.current() is None:
            raise ValueError(message)
        with unit_recovery.guard(display.pno,'geometry',block=block['id']):
            raise ValueError(message)
    def lex(word):return re.sub(r'[^\w]', '', vision_reading.normalized(word).casefold())
    source=[lex(w['text']) for w in optical]
    referenced={n for b in answer['blocks'] for n in re.findall(r'<n:([^>]+)>',b['text'])}
    for block in answer['blocks']:
        if block['id'] in referenced and block['role']=='body' and block['marker']:
            local=owned_words(optical,block_rect(block,display))
            marker=lex(block['marker'])
            if local and lex(local[0]['text'])==marker and block['bbox'][1]>=650:
                block['role']='footnote';block['continues_previous']=False;block['continues_next']=False
    # A later, independently located paragraph owns its physical ink before a
    # broad/global match can borrow it. Repeated wording in different rectangles
    # remains distinct source instances. Require corroboration in both directions
    # so an arbitrary wide model box cannot reserve intervening paragraphs.
    reservations=[]
    for block in answer['blocks']:
        x=block['bbox']
        if block['role']!='body' or not (all(0<=v<=1000 for v in x) and x[0]<x[2] and x[1]<x[3]):continue
        indices=[i for i,w in enumerate(optical) if owned_words([w],block_rect(block,display))]
        candidate=[lex(w) for w in re.sub(r'<n:[^>]+>',' ',block['text']).split()]
        matched=_geometry_anchors(optical,indices,candidate,body=True,wrap_pool=range(len(optical)))
        count=len({j for _,j in matched})
        if count>=5 and count/max(1,len(candidate))>=.6 and count/max(1,len(indices))>=.6:
            # Reserve corroborated source instances, not neighbouring ink that
            # merely falls within this candidate rectangle's rounding halo.
            anchors=[i for i,_ in matched]
            box=vision_reading.union([optical[i]['bbox'] for i in anchors])
            reservations.append(((box[2]-box[0])*(box[3]-box[1]),block['id'],anchors,box))
    owners={};reserved={}
    for area,bid,indices,box in sorted(reservations):
        if any(i in reserved for i in indices):continue
        owners[bid]=box
        reserved.update((i,bid) for i in indices)
    if body_owners is not None:body_owners.update(owners)
    # A coarse BODY hint may reach a footer. Repeated words there are not
    # paragraph ownership: bind locally corroborated note instances before
    # matching that paragraph. Tight, independently coherent BODY instances
    # remain protected from displaced note hints.
    note_openings={}
    # A model can duplicate a separately owned BODY prefix in a note. Its
    # complete source-backed body owner and the remaining note's own printed
    # opening prove the partition; no text is transferred to another note.
    for note in answer['blocks']:
        if note['role']!='footnote':continue
        proposed=note['text'].split()
        for body in answer['blocks']:
            prefix=body['text'].split()
            if (body['role']!='body' or body['id'] not in owners or len(prefix)<5 or
                    len(proposed)-len(prefix)<5 or
                    [lex(t)for t in proposed[:len(prefix)]]!=[lex(t)for t in prefix]):continue
            from .source_geometry import labelled_note_anchors,local_note_openings
            available=[i for i in range(len(optical))if i not in reserved]
            candidate=[lex(t)for t in proposed[len(prefix):]]
            hint=block_rect(note,display)
            opening=local_note_openings(optical,available,note['marker'],hint)
            proof=labelled_note_anchors(optical,available,candidate,note['marker'],openings=opening)
            if not proof or {j for i,j in proof['anchors']}!=set(range(len(candidate))):continue
            note['text']=' '.join(proposed[len(prefix):])
            note['low_confidence']=[i-len(prefix)for i in note['low_confidence']if i>=len(prefix)]
            repairs.append(dict(block=note['id'],method='separate_owned_body_prefix_excluded',
                body_owner=body['id'],excluded_token_count=len(prefix)))
            break
    for note in answer['blocks']:
        x=note['bbox']
        if note['role']!='footnote' or not (all(0<=v<=1000 for v in x) and x[0]<x[2] and x[1]<x[3]):continue
        hint=block_rect(note,display)
        candidate=[lex(w) for w in re.sub(r'<n:[^>]+>',' ',note['text']).split()]
        available=[i for i in range(len(optical)) if i not in reserved]
        from .source_geometry import labelled_note_anchors,local_note_openings
        openings=local_note_openings(optical,available,note['marker'],hint)
        labelled=labelled_note_anchors(optical,available,candidate,note['marker'],openings=openings or None)
        complete=_note_pixel_anchors(optical,available,candidate,hint,exact_complete=True)
        if labelled and complete and any(dict((j,i)for i,j in complete).get(j)!=i for i,j in labelled['anchors']):complete=[]
        anchors=complete or (labelled['anchors'] if labelled else _note_pixel_anchors(optical,available,candidate,hint))
        if labelled:note_openings[note['id']]=labelled['marker_bbox']
        local=[(i,j) for i,j in anchors if owned_words([optical[i]],hint)]
        if (len({j for i,j in anchors})+bool(labelled)<min(5,len(candidate)) or
                len({j for i,j in anchors})/max(1,len(candidate))<.6 or
                not labelled and len({j for i,j in local})<min(5,len(candidate))):continue
        indices={i for i,j in anchors}
        opening=min(anchors,key=lambda a:a[1])
        if labelled:
            indices.update(i for i in available if optical[i]['bbox']==labelled['marker_bbox'])
        if not labelled and note['marker'] and opening[1]<=1:
            first=optical[opening[0]]['bbox']
            height=max(1,first[3]-first[1]);marker=lex(note['marker'])
            labels=[i for i in available if i not in indices and
                owned_words([optical[i]],hint) and
                abs((optical[i]['bbox'][1]+optical[i]['bbox'][3]-first[1]-first[3])/2)<=height and
                0<=first[0]-optical[i]['bbox'][2]<=3*height and
                (lex(optical[i]['text'])==marker or
                 lex(optical[i]['text']).startswith(marker) and
                 len(lex(optical[i]['text']))==len(marker)+1 or
                 # A uniquely adjacent raised glyph can own this opening
                 # without its OCR spelling proving the numeric identity.
                 # Reserve its ink for the note's existing UNPROVED control;
                 # omission recovery must not append it to preceding prose.
                 marker.isdigit() and len(optical[i]['text'])==1
                 and not optical[i]['text'].isalnum()
                 and optical[i]['bbox'][3]-optical[i]['bbox'][1]<height*.85
                 and optical[i]['bbox'][3]<first[3]-height*.25)]
            if len(labels)==1:
                indices.add(labels[0]);note_openings[note['id']]=optical[labels[0]]['bbox']
        for i in indices:
            reserved[i]=note['id']
            if regions is not None:regions.setdefault('_instances',{})[_source_instance(optical[i])]=note['id']
    # Bind BODY first so restored edge instances are reserved before a caption
    # or note can globally borrow its inline reference or continuation.
    controller=unit_recovery.current()
    blocked=controller.blocked(display.pno) if controller else {}
    for block in sorted(answer['blocks'],key=lambda b:b['role']!='body'):
        if (block['role']=='footnote' and block['id'] in blocked
                and block['text']==block['marker']==''):
            # A rejected note's identity carrier cannot establish pixel
            # ownership, including when its original rectangle was invalid.
            if regions is not None:regions[block['id']]=[]
            continue
        x=block['bbox'];valid=all(0<=v<=1000 for v in x) and x[0]<x[2] and x[1]<x[3]
        if block['role'] in ('figure','table') and valid:continue
        candidate=[lex(w) for w in re.sub(r'<n:[^>]+>',' ',block['text']).split()]
        available=[i for i in range(len(optical)) if block['role'] not in ('body','caption','footnote','heading') or reserved.get(i,block['id'])==block['id']]
        if valid and block['role']=='other' and re.fullmatch(r'[A-Za-z0-9°○]{1,3}',block['text'].strip()):
            hint=block_rect(block,display);r=display.rect
            # A shifted short margin hint can find its real glyph in the same
            # margin. It cannot borrow an authored body occurrence globally.
            if hint[1]>r.height*.88:
                available=[i for i in available if optical[i]['bbox'][1]>r.height*.88]
            elif hint[3]<r.height*.13:
                available=[i for i in available if optical[i]['bbox'][3]<r.height*.13]
        local_indices=[i for i in available if owned_words([optical[i]],block_rect(block,display))] if valid else []
        matched=_geometry_anchors(optical,local_indices,candidate,body=valid and block['role']=='body',wrap_pool=available)
        if valid and block['role']=='heading' and 2<=len(candidate)<=4 and matched:
            # A short run-in title may have one damaged OCR word. Its exact
            # neighbours locate one continuous physical row after BODY claims
            # have been excluded. Reserve that ink without certifying spelling.
            anchor_boxes=[optical[i]['bbox'] for i,j in matched]
            middle=statistics.median((box[1]+box[3])/2 for box in anchor_boxes)
            height=statistics.median(max(1,box[3]-box[1]) for box in anchor_boxes)
            row=sorted((i for i in local_indices if
                abs(sum(optical[i]['bbox'][1::2])/2-middle)<=height*.55),
                key=lambda i:optical[i]['bbox'][0])
            if (len(row)==len(candidate) and sum(source[i]==key for i,key in zip(row,candidate))>=len(candidate)-1
                    and all(optical[b]['bbox'][0]-optical[a]['bbox'][2]<=2*height for a,b in zip(row,row[1:]))):
                matched=list(zip(row,range(len(candidate))))
        seed_caption_complete=False
        if block['role']=='caption' and valid and len({j for i,j in matched})<len(candidate):
            # Caption hints can be displaced by a few printed rows. Recover
            # exact nearby instances, with BODY reservations already removed;
            # never relocate a caption using a pagewide transcription match.
            hint=block_rect(block,display)
            height=statistics.median(max(1,optical[i]['bbox'][3]-optical[i]['bbox'][1])
                for i in local_indices or available) if available else 1
            nearby=[i for i in available if owned_words([optical[i]],
                [hint[0],hint[1]-4*height,hint[2],hint[3]+4*height])]
            extended=_geometry_anchors(optical,nearby,candidate)
            if len({j for i,j in extended})>len({j for i,j in matched}):matched=extended
            punctuated=_caption_punctuation_anchors(optical,nearby,block['text'])
            if len({j for i,j in punctuated})>len({j for i,j in matched}):matched=punctuated
            # A complete nearby physical occurrence must survive a shorter
            # local match. Uniqueness and complete coverage are required; this
            # cannot promote a partial caption or borrow reserved BODY words.
            from .source_geometry import unique_anchors
            nearby_proof=unique_anchors(optical,nearby,candidate)
            if {j for i,j in nearby_proof}=={j for j,key in enumerate(candidate) if key}:
                matched=nearby_proof
                seed_caption_complete=True
            # A displaced caption hint can still be proved inside the seed of
            # its adjacent source object. Admit only a complete unique original
            # text instance, with all independently owned BODY ink excluded.
            # Partial matches and pagewide equality never move a caption.
            at=answer['blocks'].index(block)
            adjacent=[next((b for b in sequence if b['role'] not in
                ('caption','running_head','folio')),None)
                for sequence in (reversed(answer['blocks'][:at]),answer['blocks'][at+1:])]
            seeds=[block_rect(b,display) for b in adjacent if b is not None
                and b['role'] in ('figure','table') and
                all(0<=v<=1000 for v in b['bbox']) and
                b['bbox'][0]<b['bbox'][2] and b['bbox'][1]<b['bbox'][3]]
            seed_indices=[i for i in available if any(owned_words([optical[i]],seed) for seed in seeds)]
            seed_proof=_caption_punctuation_anchors(optical,seed_indices,block['text'])
            if seed_proof and {j for i,j in seed_proof}=={j for j,key in enumerate(candidate) if key}:
                matched=seed_proof
                seed_caption_complete=True
                repairs.append(dict(block=block['id'],method='unique_complete_caption_in_adjacent_source_seed',
                    source_word_indices=list(dict.fromkeys(i for i,j in seed_proof))))
            if not punctuated and not seed_proof:
                suffix=_caption_punctuation_suffix_anchors(optical,nearby,block['text'],hint)
                if suffix:
                    # Keep only corroborated prefix anchors, then the proved
                    # ending. Interposed artwork OCR is not caption territory.
                    first=min(j for i,j in suffix)
                    matched=sorted(set([(i,j) for i,j in matched if j<first]+suffix),
                        key=lambda pair:(pair[1],optical[pair[0]]['bbox'][0]))
        labelled=None;local_openings=[];complete_note=[]
        if block['role']=='footnote' and valid:
            from .source_geometry import labelled_note_anchors,local_note_openings
            hint=block_rect(block,display)
            local_openings=local_note_openings(optical,available,block['marker'],hint)
            labelled=labelled_note_anchors(optical,available,candidate,block['marker'],openings=local_openings or None)
            complete_note=_note_pixel_anchors(optical,available,candidate,hint,exact_complete=True)
            if labelled and complete_note and any(dict((j,i)for i,j in complete_note).get(j)!=i for i,j in labelled['anchors']):complete_note=[]
            matched=complete_note or (labelled['anchors'] if labelled else _note_pixel_anchors(optical,local_indices if local_openings else available,candidate,hint))
            if labelled:note_openings[block['id']]=labelled['marker_bbox']
        from .source_geometry import unique_anchors
        proof_pool=(local_indices if valid and block['role'] in ('running_head','folio','heading','caption') else available)
        # A printed note opening owns its local footer even when its reading is
        # damaged. A pagewide prose match cannot move that note onto BODY ink.
        if local_openings:proof_pool=local_indices
        corroborated=unique_anchors(optical,proof_pool,candidate) if not labelled and not seed_caption_complete and not complete_note else []
        if (corroborated and valid and block['role']=='body' and candidate and
                {j for i,j in matched if source[i]==candidate[j]}==set(range(len(candidate)))):
            # Exact common words elsewhere in a wide hint do not establish
            # paragraph continuity. Retain disjoint rows only when an actual
            # source object separates them within the proved text column.
            compact=vision_reading.union([optical[i]['bbox']for i,j in corroborated])
            extent=vision_reading.union([optical[i]['bbox']for i,j in matched])
            rows=[vision_reading.union([word['bbox']for word in row])
                  for row in _pixel_lines([optical[i]for i,j in matched])]
            # A broad row can extend beside a narrower wrapped row. Column
            # continuity is a row-span property, not an every-word overlap.
            same_column=all(min(box[2],compact[2])>max(box[0],compact[0])for box in rows)
            ordered_rows=sorted(rows,key=lambda box:box[1])
            art_between=any(any(left[3]<box[3] and box[1]<right[1]
                                for left,right in zip(ordered_rows,ordered_rows[1:]))
                and min(box[2],extent[2])>max(box[0],extent[0])
                for other in answer['blocks']if other['role']in ('figure','table')
                for box in [block_rect(other,display)])
            if same_column and art_between:corroborated=matched
        if corroborated:
            # Compact exact proof must retain an independently located divided
            # edge atom. The existing matcher proves that partial word beside
            # its exact same-row neighbour; it is not a distant text match.
            partial=[]
            if block['role']=='body' and valid:
                # A compact continuation cannot erase an exact hinted prefix
                # when original ink proves the divided word across artwork.
                # This joins source territories, never the intervening pixels.
                exact = sorted((i,j) for i,j in matched if source[i] == candidate[j])
                prefix = sorted(exact, key=lambda pair: pair[1])
                n = 0
                while n < len(prefix) and prefix[n][1] == n:
                    n += 1
                prefix = prefix[:n]
                tail_positions = {j for i,j in corroborated}
                if (n >= 3 and n < len(candidate) and
                        any(len(candidate[j]) >= 6 for i,j in prefix) and
                        set(range(n+1,len(candidate))) <= tail_positions):
                    ends = [i for i in local_indices if
                        optical[i]['text'].endswith(tuple(assemble.HYPHENS)) and
                        candidate[n].startswith(source[i]) and source[i] != candidate[n]]
                    pairs = []
                    for a in ends:
                        left = optical[a]['bbox'];h = max(1,left[3]-left[1])
                        previous = optical[prefix[-1][0]]['bbox']
                        if not (abs(sum(left[1::2])-sum(previous[1::2]))<=h and
                                0 <= left[0]-previous[2] <= 3*h):continue
                        if any(w['bbox'][0]>=left[2] and
                            abs(sum(w['bbox'][1::2])-sum(left[1::2]))<=h for w in optical):continue
                        for b in available:
                            right = optical[b]['bbox']
                            if source[a]+source[b] != candidate[n] or right[1] <= left[3]:continue
                            if any(w['bbox'][2]<=right[0] and
                                abs(sum(w['bbox'][1::2])-sum(right[1::2]))<=h for w in optical):continue
                            following = [optical[i]['bbox']for i,j in corroborated if j==n+1]
                            if len(following)!=1:continue
                            near=following[0]
                            if not (abs(sum(right[1::2])-sum(near[1::2]))<=2*h and
                                    0 <= near[0]-right[2] <= 3*h and
                                    abs(right[0]-optical[prefix[0][0]]['bbox'][0])<=3*h):continue
                            if not any(other['role'] in ('figure','table') and
                                left[3]<block_rect(other,display)[1] and
                                block_rect(other,display)[3]<right[1]for other in answer['blocks']):continue
                            pairs.append((a,b))
                    if len(pairs)==1:
                        a,b=pairs[0]
                        partial.extend(prefix+[(a,n),(b,n)])
            if block['role']=='body':
                for index,position in matched:
                    if position not in (0,len(candidate)-1):continue
                    raw=optical[index]['text'];atom=lex(raw);whole=candidate[position]
                    if len(atom)<2 or atom==whole or not whole.isalpha():continue
                    leading=position==0
                    if not (whole.endswith(atom) if leading else
                            raw.endswith(tuple(assemble.HYPHENS)) and whole.startswith(atom)):continue
                    neighbors=[i for i,j in corroborated if j==position+(1 if leading else -1)]
                    if len(neighbors)!=1:continue
                    box=optical[index]['bbox'];near=optical[neighbors[0]]['bbox']
                    height=max(1,box[3]-box[1],near[3]-near[1])
                    gap=near[0]-box[2] if leading else box[0]-near[2]
                    if abs((box[1]+box[3]-near[1]-near[3])/2)<=height*.55 and 0<=gap<=2*height:
                        partial.append((index,position))
            matched=sorted(set(corroborated+partial),key=lambda pair:pair[1])
        if not corroborated and block['role']=='body' and hasattr(display,'doc'):
            from .source_geometry import native_pixel_anchors
            if not hasattr(display,'_placement_words'):
                display._placement_words=layer_words(display.doc,display.pno,display)
            joint=native_pixel_anchors(display._placement_words,optical,available,candidate)
            if joint:
                matched=joint
                repairs.append(dict(block=block['id'],method='native_sequence_on_independent_pixel_ink'))
        borrowed=(block['role']=='body' and block['id'] not in owners and any(
            max(n for a,b,n in SequenceMatcher(None,[source[i] for i in range(len(optical))
                if reserved.get(i)==owner],candidate,autojunk=False).get_matching_blocks())>=vision_reading.UNALIGNED_RUN
            for owner in owners if owner!=block['id']))
        count=len({j for _,j in matched})
        local_owned=borrowed and count>=5 and count/max(1,len(local_indices))>=.6
        globally_relocated=False
        if count/max(1,len(candidate))<.6 and not local_owned:
            # A damaged marginal heading can repeat verbatim inside the prose.
            # Its valid source rectangle still owns that margin: a global text
            # match must not move furniture onto the repeated body occurrence.
            if block['role']=='caption' or valid and (block['role'] in ('running_head','folio') or
                          block['role']=='heading' and len(candidate)<=4):
                if valid:continue
                matched=[]
            else:
                local_indices=available
                if block['role']!='footnote':matched=_geometry_anchors(optical,local_indices,candidate)
                globally_relocated=block['role']=='body'
        anchors=matched
        if block['role']=='body':
            # A repeated list number elsewhere on the page cannot bridge a
            # displaced hint to its real paragraph. A numeric boundary needs
            # adjacent printed ink; otherwise keep its reading uncertain and
            # leave that remote physical occurrence with its original unit.
            for position in (0,len(candidate)-1):
                if not candidate or not re.fullmatch(r'[0-9]{1,4}',candidate[position]):continue
                edges=[(i,j) for i,j in anchors if j==position]
                rest=[(i,j) for i,j in anchors if j!=position]
                if not rest:continue
                if len(edges)==1:
                    index,_=edges[0]
                    neighbor=min(rest,key=lambda pair:abs(pair[1]-position))[0]
                    box=optical[index]['bbox'];near=optical[neighbor]['bbox']
                    height=max(1,box[3]-box[1],near[3]-near[1])
                    gap=max(0,max(box[1],near[1])-min(box[3],near[3]))
                    if gap<=2.5*height:continue
                # A compact proof can exclude the remote numeral before this
                # stage. Its absence is uncertainty, regardless of which binder
                # established the remaining paragraph's physical ownership.
                anchors=[pair for pair in anchors if pair not in edges]
                positions=[i for i,word in enumerate(block['text'].split())
                    for token in re.sub(r'<n:[^>]+>',' ',word).split()]
                block['low_confidence']=sorted(set(block['low_confidence']+[positions[position]]))
                repairs.append(dict(block=block['id'],method='unplaced_numeric_boundary',
                    candidate_position=position))
        if len(anchors)+bool(labelled)<min(5,len(candidate)) or (len(anchors)/max(1,len(candidate))<.6 and not (valid and borrowed)):
            # A syntactically valid note hint cannot identify a repeated or
            # unobserved occurrence. Keep it explicitly unplaced; source-unit
            # recovery retains its original page without publishing stale text.
            if valid and block['role']!='footnote':continue
            reject('Model block rectangle cannot be bound to printed pixels: '+block['id'])
        if block['role']=='body':
            # A tight hint can stop at the last letter. Its exact published
            # suffix still owns adjacent punctuation before artwork barriers
            # are derived; note reservations remain excluded from this pool.
            raw=re.sub(r'<n:[^>]+>',' ',block['text']).split()
            clusters=_source_punctuation_suffixes([optical[i] for i in available])
            lookup={id(optical[i]):i for i in available}
            additions=[]
            for index,position in anchors:
                candidates=[members for members in clusters.get(raw[position],[])
                            if members[0] is optical[index]]
                if len(candidates)==1:
                    additions.extend((lookup[id(w)],position) for w in candidates[0][1:])
            anchors=list(dict.fromkeys(anchors+additions))
            anchors.sort(key=lambda a:(a[1],optical[a[0]]['bbox'][0]))
        if block['role']=='body' and anchors:
            # A compact proof can locate the continuation but omit the entire
            # opening at the foot of the preceding printed column. Only a
            # complete unique prefix at the two column edges can extend it;
            # source instances reserved by another owner remain unavailable.
            edge=min(j for i,j in anchors)
            if 6<=edge<=32:
                opening=unique_anchors(optical,available,candidate[:edge])
                if {j for i,j in opening}==set(range(edge)):
                    first=optical[min(anchors,key=lambda pair:pair[1])[0]]['bbox']
                    head=[optical[i]['bbox']for i,j in opening]
                    envelope=vision_reading.union(head)
                    height=statistics.median(b[3]-b[1]for b in head)
                    column_return=(envelope[2]+3<first[0]
                        and envelope[1]>=display.rect.y0+.8*display.rect.height
                        and first[1]<=display.rect.y0+.25*display.rect.height
                        and .6*height<=statistics.median(optical[i]['bbox'][3]-optical[i]['bbox'][1]
                            for i,j in anchors)<=1.5*height)
                    if column_return:
                        anchors=sorted(set(opening+anchors),key=lambda pair:pair[1])
                        repairs.append(dict(block=block['id'],
                            method='complete_previous_column_opening',source_word_count=edge))
        core_anchors=set(anchors)
        if block['role']=='body' and anchors and regions is not None:
            # A valid model rectangle can begin/end inside a printed paragraph.
            # Its accepted later anchors cannot erase exact adjacent source words
            # at the reading's edges, or let those rows become figure pixels.
            height=statistics.median(max(1,optical[i]['bbox'][3]-optical[i]['bbox'][1]) for i,j in anchors)
            global_matches=SequenceMatcher(None,[source[i] for i in available],candidate,autojunk=False)
            candidates=[(available[i],j) for a,b,count in global_matches.get_matching_blocks()
                        for i,j in zip(range(a,a+count),range(b,b+count))]
            for leading in (True,False):
                edge=min(j for i,j in anchors) if leading else max(j for i,j in anchors)
                additions=sorted((x for x in candidates if (x[1]<edge if leading else x[1]>edge)),
                                 key=lambda x:x[1],reverse=leading)
                for index,position in additions:
                    near=min(anchors,key=lambda x:abs(x[1]-position))
                    left=optical[index]['bbox'];right=optical[near[0]]['bbox']
                    envelope=vision_reading.union([optical[i]['bbox'] for i,j in anchors])
                    same_column=left[0]<envelope[2]+height and left[2]>envelope[0]-height
                    gap=max(0,max(left[1],right[1])-min(left[3],right[3]))
                    size=left[3]-left[1]
                    ordered=(left[1]<=right[1]+height*.5 if leading else left[1]>=right[1]-height*.5)
                    if not (same_column and ordered and gap<=2.5*height and .6*height<=size<=1.5*height):break
                    anchors.append((index,position))
            anchors=sorted(set(anchors),key=lambda x:x[1])
        # A paragraph can cross columns/leaves. Keep ordered source territories;
        # their bounding union must never own the intervening paragraphs.
        import statistics
        height=statistics.median(max(1,w['bbox'][3]-w['bbox'][1]) for w in optical)
        components=[];bridged_instances=set()
        for anchor in anchors:
            if components:
                previous=optical[components[-1][-1][0]]['bbox'];current=optical[anchor[0]]['bbox']
                envelope=vision_reading.union([optical[i]['bbox'] for i,j in components[-1]])
                disjoint=current[0]>envelope[2]+3 or current[2]<envelope[0]-3
                if block['role']=='footnote':
                    transition=(abs((previous[1]+previous[3]-current[1]-current[3])/2)>height*.55
                                or current[0]<previous[0]-3 or current[0]-previous[2]>4*height)
                    if (transition and current[0]>previous[2]
                            and min(previous[3],current[3])-max(previous[1],current[1])>height*.5):
                        # A misspelled model word between two anchors is still
                        # original ink. Fill only a continuous, unclaimed row;
                        # a blank column gutter cannot bridge separate notes.
                        inside=sorted([i for i in available if optical[i]['bbox'][0]>=previous[2]
                            and optical[i]['bbox'][2]<=current[0]
                            and min(optical[i]['bbox'][3],previous[3])-max(optical[i]['bbox'][1],previous[1])>height*.5],
                            key=lambda i:optical[i]['bbox'][0])
                        chain=[previous]+[optical[i]['bbox'] for i in inside]+[current]
                        if inside and all(b[0]-a[2]<=height*1.5 for a,b in zip(chain,chain[1:])):
                            transition=False;bridged_instances.update(inside)
                else:
                    art_gap=(block['role']=='body' and current[1]-previous[3]>3*height and any(
                        box[0]<max(envelope[2],current[2]) and box[2]>min(envelope[0],current[0])
                        and box[1]<current[1] and box[3]>previous[3]
                        for other in answer['blocks'] if other['role'] in ('figure','table')
                        for box in [block_rect(other,display)]))
                    transition=(previous[1]-current[1]>4*height and disjoint) or art_gap
            else:transition=True
            if transition:components.append([])
            components[-1].append(anchor)
        if block['role']=='body' and len(components)>1:
            # A long unplaced continuation can match a few common words deep
            # inside another column. Those sparse suffix anchors do not prove
            # that the paragraph crossed the intervening, unprinted words.
            dominant=max(components,key=lambda component:len({j for i,j in component}))
            positions={j for i,j in dominant}
            if len(positions)>=8 and len(positions)/max(1,len(candidate))>=.5:
                own=vision_reading.union([optical[i]['bbox']for i,j in dominant])
                kept=[]
                for component in components:
                    slots={j for i,j in component}
                    rect=vision_reading.union([optical[i]['bbox']for i,j in component])
                    remote=rect[0]>own[2]+3 or rect[2]<own[0]-3
                    span=max(slots)-max(positions) if min(slots)>max(positions) else 0
                    sparse=(len(slots)<5 and span>=vision_reading.UNALIGNED_RUN
                            and len(slots)/span<.5)
                    if remote and sparse:
                        repairs.append(dict(block=block['id'],method='unproved_remote_continuation_excluded',
                            source_word_indices=[i for i,j in component]))
                        continue
                    kept.append(component)
                components=kept
                admitted={pair for component in components for pair in component}
                anchors=[pair for pair in anchors if pair in admitted]
                core_anchors.intersection_update(admitted)
        interior_rows=[]
        if block['role']=='footnote' and len(components)>1:
            # A damaged transcription may omit every matching word on a middle
            # row. Its ink still belongs between this note's proved rows; do
            # not emit it a second time as an unrelated recovered paragraph.
            # Fill only short, same-column gaps with no intervening unit or
            # independently printed opening label. Keep each row's own crop.
            for before,after in zip(components,components[1:]):
                a=vision_reading.union([optical[i]['bbox'] for i,j in before])
                b=vision_reading.union([optical[i]['bbox'] for i,j in after])
                gap=b[1]-a[3]
                if not (height*.5<gap<=height*2.5 and min(a[2],b[2])>max(a[0],b[0])):continue
                left=min(a[0],b[0]);right=max(a[2],b[2])
                between=[i for i,w in enumerate(optical) if a[3]<sum(w['bbox'][1::2])/2<b[1]
                         and w['bbox'][0]>=left-height and w['bbox'][2]<=right+height]
                if not between or any(i not in available for i in between):continue
                opening=note_openings.get(block['id'])
                if opening and any(re.fullmatch(r'[0-9]{1,4}|[*†‡]',lex(optical[i]['text']))
                    and abs(optical[i]['bbox'][0]-opening[0])<=height for i in between):continue
                rows=_pixel_lines([optical[i] for i in between])
                if any(any(y['bbox'][0]-x['bbox'][2]>4*height for x,y in zip(row,row[1:])) for row in rows):continue
                bridged_instances.update(between)
                interior_rows.extend(vision_reading.union([w['bbox'] for w in row]) for row in rows)
        territories=[]
        for component in components:
            rect=vision_reading.union([optical[i]['bbox'] for i,j in component])
            local=owned_words([optical[i] for i in available],rect)
            segment=candidate[component[0][1]:component[-1][1]+1]
            sm=SequenceMatcher(None,[lex(w['text']) for w in local],segment,autojunk=False)
            if not valid and any(max(a2-a1,b2-b1)>=vision_reading.UNALIGNED_RUN for tag,a1,a2,b1,b2 in sm.get_opcodes() if tag!='equal'):
                reject('Ambiguous printed region for model block: '+block['id'])
            if block['role']=='footnote' and block['marker'] and component is components[0]:
                # Include the independently printed opening label, if adjacent.
                labels=[w['bbox'] for w in optical if lex(w['text'])==lex(block['marker'])
                        and abs(w['bbox'][1]-rect[1])<height and 0<=rect[0]-w['bbox'][2]<3*height]
                if block['id'] in note_openings:labels.append(note_openings[block['id']])
                if labels:rect=vision_reading.union([rect]+labels)
            edge=[(i,j) for i,j in component if (i,j) not in core_anchors]
            core=[(i,j) for i,j in component if (i,j) in core_anchors]
            if edge and core:
                # A restored partial first/last row owns just its words. Its
                # union with later full rows must not swallow an omitted prefix
                # or a neighboring paragraph on that same physical baseline.
                first=min(j for i,j in core);last=max(j for i,j in core)
                restored=[]
                for leading in (True,False):
                    if not leading:restored.append(vision_reading.union([optical[i]['bbox'] for i,j in core]))
                    rows=_pixel_lines([optical[i] for i,j in edge if (j<first if leading else j>last)])
                    restored.extend(vision_reading.union([w['bbox'] for w in row]) for row in rows)
                # Model token positions can place the continuation of a
                # divided word before its upper-line stem. This component's
                # physical rows, rather than that lexical alignment, order its
                # crops. Remove contained regions so the same continuation
                # cannot be recognized twice. Separate columns remain separate
                # components in the transition proof above.
                restored=[r for i,r in enumerate(restored) if not any(
                    j!=i and q[0]<=r[0] and q[1]<=r[1] and q[2]>=r[2] and q[3]>=r[3]
                    and (q!=r or j<i) for j,q in enumerate(restored))]
                territories.extend(sorted(restored,key=lambda r:(r[1],r[0])))
            elif block['role']=='caption':
                # A caption's partial first row may sit beside unrelated prose.
                # Completing its later rows must not turn their bounding union
                # into ownership of that earlier neighbouring paragraph.
                territories.extend(vision_reading.union([w['bbox'] for w in row])
                    for row in _pixel_lines([optical[i] for i,j in component]))
            else:territories.append(rect)
        if interior_rows:
            territories=sorted(territories+interior_rows,key=lambda rect:(rect[1],rect[0]))
        # Absence of persistent column evidence is not single-column proof.
        # Reorder only the observed interleaving of redundant physical crops;
        # short, separately admitted columns retain their supplied flow.
        contained_regions=any(i!=j and q[0]<=r[0] and q[1]<=r[1]
            and q[2]>=r[2] and q[3]>=r[3]
            for i,r in enumerate(territories)for j,q in enumerate(territories))
        if (block['role']=='body' and contained_regions
                and any(b[1]<a[1]-height for a,b in zip(territories,territories[1:]))):
            owned=[optical[i]for i in available if any(owned_words([optical[i]],r)for r in territories)]
            _,columns_proved=_proved_recovery_columns(owned)
            if not columns_proved:
                # A lexical anchor for a divided word can put its lower-line
                # continuation before the stem, even across separate restored
                # components. A real column transition keeps its flow order;
                # a single-column return uses physical row order once only.
                territories=[r for i,r in enumerate(territories) if not any(
                    j!=i and q[0]<=r[0] and q[1]<=r[1] and q[2]>=r[2] and q[3]>=r[3]
                    and(q!=r or j<i)for j,q in enumerate(territories))]
                territories.sort(key=lambda r:(r[1],r[0]))
        if len(territories)>1 and regions is None:
            reject('Disjoint printed reading requires source region ownership')
        if regions is not None:regions[block['id']]=territories
        if block['role'] in ('body','footnote','heading') and regions is not None:
            for i in {i for i,j in anchors}|bridged_instances:
                reserved[i]=block['id']
                regions.setdefault('_instances',{})[_source_instance(optical[i])]=block['id']
        rect=vision_reading.union(territories);r=display.rect
        block['bbox']=[max(0,rect[0]/r.width*1000-2),max(0,rect[1]/r.height*1000-2),
                       min(1000,rect[2]/r.width*1000+2),min(1000,rect[3]/r.height*1000+2)]
        repairs.append(block['id'])
    if regions is not None:
        for ident,bounds in note_openings.items():
            originals=[w for w in optical if w['bbox']==bounds]
            block=next(b for b in answer['blocks'] if b['id']==ident)
            if len(originals)!=1:continue
            observed=originals[0]['text']
            label=lambda text:vision_reading.normalized(text).strip('.,:;()[]{}')
            regions.setdefault('_note_openings',{})[ident]=dict(source_bbox=list(bounds),
                observed_text=observed,expected_label=block['marker'],
                identity_status='PROVED' if label(observed)==label(block['marker']) else 'UNPROVED')
    if regions is not None:_reserve_object_instances(answer,optical,display,regions)
    vision_reading.validate(answer)
    return answer,repairs


def _seam_text(fragment):
    """The authored character stream, without generated controls or tag gaps."""
    authored=build_epub._READING_COPY.sub('',fragment or '')
    # A list item's closing boundary separates authored words. Inline style,
    # source and action tags still introduce no spaces within a printed word.
    authored=re.sub(r'</li\s*>', '</li> ', authored)
    return re.sub(r'\s+',' ',unescape(build_epub._TAG.sub('',authored))).strip()


def _completed_division(left,right,tail,head):
    """An already completed model word must own both physical divided atoms.

    Equality of model words alone proves nothing. Both original BODY endpoints
    must spell one literal divided word, and each emitted endpoint must be its
    own printed atom or that exact completed word. This preserves the reader's
    chosen closed/compound spelling and its uncertainty disclosure.
    """
    if left.get('role')!='body' or right.get('role')!='body':return None
    a,b=left.get('physical_tail'),right.get('physical_head')
    if not a or not b or not a.get('bbox') or not b.get('bbox'):return None
    prefix=re.fullmatch(r"([A-Za-z'’]+)["+assemble.HYPHENS+r']',a['text'])
    suffix=re.fullmatch(r"([A-Za-z'’]+)([.,;:!?]*)",b['text'])
    t,h=_seam_text(tail).split(),_seam_text(head).split()
    if not prefix or not suffix or not t or not h:return None
    lexical=lambda word:vision_reading.normalized(word).casefold()
    wholes={lexical(prefix[1]+suffix[1]+suffix[2]),
            lexical(prefix[1]+'-'+suffix[1]+suffix[2])}
    upper,lower=lexical(t[-1]),lexical(h[0])
    if upper not in wholes|{lexical(a['text'])} or lower not in wholes|{lexical(b['text'])}:return None
    if upper not in wholes and lower not in wholes:return None
    if upper in wholes and lower in wholes and upper!=lower:return None
    return dict(source_tail=a,source_head=b,keep='upper' if upper in wholes else 'lower')


def _seam_word_edit(fragment,last,remove=False,other=None):
    """Edit one proven visible word while preserving every original HTML node.

    Character offsets traverse text nodes and entities, never a global string
    replacement. Generated evidence controls retain their exact IDs and targets.
    The surviving word receives the other source owner, so its provenance spans
    both physical atoms even across a printed page boundary.
    """
    clean,copies=build_epub._reading_copy_parts(fragment)
    chars=[];cursor=0
    for tag in list(build_epub._TAG.finditer(clean))+[None]:
        end=tag.start() if tag else len(clean)
        for atom in re.finditer(r'&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z]+);|[^&]|&',clean[cursor:end]):
            start=cursor+atom.start();stop=cursor+atom.end()
            chars.extend((char,start,stop) for char in unescape(atom.group()))
        cursor=tag.end() if tag else end
    stream=''.join(char for char,_,_ in chars)
    matches=list(re.finditer(r'\S+',stream))
    if not matches:return fragment
    word=matches[-1 if last else 0]
    spans=[]
    for _,a,z in chars[word.start():word.end()]:
        if spans and a<=spans[-1][1]:spans[-1]=(spans[-1][0],max(z,spans[-1][1]))
        else:spans.append((a,z))
    owner=(re.search(r'<span (data-source-page="[^"]+" data-source-block="[^"]+" data-source-regions="[^"]+")>',other or ''))
    edits=[(a,z,'' if remove else '<span '+owner[1]+'>'+clean[a:z]+'</span>')
           for a,z in spans] if remove or owner else []
    # Offset projection retains even a disclosure located inside the consumed
    # atom. It follows the surviving reading at the same sentence boundary.
    def project(offset):
        delta=0
        for a,z,value in edits:
            if offset<a:break
            if offset<=z:return a+delta+len(value)
            delta+=len(value)-(z-a)
        return offset+delta
    for a,z,value in reversed(edits):clean=clean[:a]+value+clean[z:]
    for offset,copy in reversed([(project(offset),copy) for offset,copy in copies]):
        clean=clean[:offset]+copy+clean[offset:]
    return clean


def _repair_completed_division(left,right,blocks,tail,head):
    plan=_completed_division(left,right,tail,head)
    if not plan or not _source_continuation(left,right,blocks,tail,head):return tail,head
    if plan['keep']=='upper':
        return _seam_word_edit(tail,True,other=head),_seam_word_edit(head,False,remove=True)
    return _seam_word_edit(tail,True,remove=True),_seam_word_edit(head,False,other=tail)


def _nonterminal_period(unwrapped,opening):
    """Shared physical-period semantics for page and interior row seams."""
    last=unwrapped.split()[-1]
    title_period=bool(re.fullmatch(r'(?:Dr|Mr|Mrs|Ms|Prof|Rev|Hon|Capt|Col|Gen|Lt|Sgt|Fr)\.',last,re.IGNORECASE)
        and re.match(r'[A-ZÀ-ÖØ-Þ][\w\u2019\'-]*',opening))
    reference_label=bool(re.fullmatch(r'(?:Fig|Figs|Eq|Eqs|No|Nos|Vol|Vols|Ch|Chap)\.',last,re.IGNORECASE))
    if re.fullmatch(r'Nos?\.',last,re.IGNORECASE):
        # "No." can be a complete reply even before a numeric sentence.
        # Admit the numbered reading only with an explicit reference cue,
        # rather than allowing a numeral or paired flags to change its sense.
        reference_label=bool(re.search(r'\b(?:see|compare|consult|cf|vide|item|case|example|plate|table|diagram|figure|fig|number|chapter|section|reference|question|formula|experiment|proposition|rule|volume)s?\s+Nos?\.$',unwrapped,re.IGNORECASE))
    reference_period=bool(reference_label
        and re.match(r'(?:\d+|[IVXLCDM]+)(?=$|[\s.,;:)\]-])',opening))
    return title_period or reference_period


def _continuation_decision(left,right,blocks,tail,head):
    """Decide both ends of a paragraph; flags cannot waive punctuation or indent."""
    def decision(status,reason):return {'status':status,'reason':reason}
    if left.get('role')!='body' or right.get('role')!='body':
        return decision('split','structural_owner')
    if left.get('native_source_structure') or right.get('native_source_structure'):
        return decision('split','original_native_structural_boundary')
    tail_text,head_text=_seam_text(tail),_seam_text(head)
    if not tail_text or not head_text:return decision('unmapped','missing_visible_endpoint')
    divided=_completed_division(left,right,tail,head)
    if divided:
        a,b=divided['source_tail'],divided['source_head']
        proof_left=dict(left,printed_tail=a['text'],printed_tail_box=a['bbox'])
        proof_right=dict(right,printed_head=b['text'],printed_head_box=b['bbox'],
                         completed_division_source_proved=True)
        return _continuation_decision(proof_left,proof_right,blocks,
            ' '.join(tail_text.split()[:-1]+[a['text']]),
            ' '.join([b['text']]+head_text.split()[1:]))
    unwrapped=re.sub(r'[)\]"”’\']+$','',tail_text.rstrip())
    opening=head_text.lstrip('(["“‘\'').lstrip()
    if not unwrapped.strip():return decision('unmapped','missing_unwrapped_endpoint')
    # A title before a name, or a reference before its number, has a
    # nonterminal period. This lexical possibility still needs the same
    # physical endpoint, margin and page-edge proof as any continuation.
    abbreviation_period=_nonterminal_period(unwrapped,opening)
    # A closing wrapper does not finish a sentence; punctuation inside it does.
    if re.search(r'[.!?:…]$',unwrapped) and not abbreviation_period:
        return decision('split','terminal_punctuation')
    item=re.match(r'^[\(\[]([A-Za-z]+)[\)\]](?:\s|$)',head_text)
    roman=r'M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})'
    if item and (len(item[1])==1 or re.fullmatch(roman,item[1],re.IGNORECASE)):
        return decision('split','structural_item_opening')
    # A reference range at the end of one entry is not a lexical division
    # into the name of the next entry. Require the reference syntax on both
    # sides and exact printed endpoints; a continuing range starts with its
    # next numeral, while ordinary prose and divided words lack this pair.
    reference=r'(?:\d+|[IVXLCDM]+)'
    range_end=re.search(r',\s*('+reference+r')[-\u00ad\u2010\u2011\u2013]$',unwrapped,re.IGNORECASE)
    entry_start=re.match(r'^([^,.!?;:]{1,160}),\s*('+reference+r')(?=$|[\s,.\u2013-])',opening,re.IGNORECASE)
    valid_reference=lambda value:value.isdigit() or bool(re.fullmatch(roman,value,re.IGNORECASE))
    lexical_reference=lambda value:vision_reading.normalized(value).casefold()
    if (range_end and entry_start and valid_reference(range_end[1])
            and valid_reference(entry_start[2])
            and not valid_reference(entry_start[1].split()[0])
            and lexical_reference(tail_text.split()[-1])==lexical_reference(left.get('printed_tail') or '')
            and lexical_reference(head_text.split()[0])==lexical_reference(right.get('printed_head') or '')):
        return decision('split','distinct_reference_entry_after_range')
    if tail_text.endswith(tuple(assemble.HYPHENS)) and not right.get('completed_division_source_proved'):
        letters=lambda words:''.join(c for word in words for c in word.casefold()if c.isalpha())
        head_corrections=[d for d in right.get('adjudication',[]) if d.get('status')=='RESOLVED'
            and d.get('model_range',[None])[0]==0 and d.get('source')!=d.get('model')
            and letters(d.get('source',[]))!=letters(d.get('model',[]))]
        a,b=left.get('physical_tail') or {},right.get('physical_head') or {}
        prefix=re.fullmatch(r"([A-Za-z'’]+)["+assemble.HYPHENS+r']',a.get('text',''))
        lexical=lambda word:vision_reading.normalized(word).casefold()
        def restored_printed_division(change):
            # The reader may complete the next atom with this exact printed
            # prefix. Restoring its source suffix does not destroy that proof.
            return bool(prefix and a.get('bbox') and b.get('bbox')
                and change.get('source')==change.get('chosen')==[b.get('text')]
                and len(change.get('model',[]))==1
                and lexical(prefix[1]+b['text'])==lexical(change['model'][0]))
        if any(not restored_printed_division(change) for change in head_corrections):
            # Correcting letters at the next opening proves that word, not a
            # compound with an unrelated hyphenated tail on the previous page.
            return decision('split','corrected_head_without_compound_proof')
    variant=right.get('disputed_head_prefix')
    word_edge=bool(variant and head_text.split()[0]==variant['published']
        and re.search(r'[A-Za-z]{2,}[-­‐‑]$',tail_text)
        and left.get('printed_tail')==tail_text.split()[-1])
    first_x=variant['bbox'][0] if word_edge else right.get('first_line_x')
    if (left.get('native_source') and right.get('leading_source_space')
            and tail_text.endswith(tuple(assemble.HYPHENS)) and opening and opening[0].islower()
            and right.get('source_regions') and
            right.get('native_geometry_binding')!='unique_complete_source_text'):
        # Literal native span whitespace preceding a divided word is not a
        # first-line indent. Its painted row still starts at the element origin.
        first_x=right['source_regions'][0][0]
    if first_x is None:
        if abbreviation_period:return decision('unmapped','abbreviation_without_source_geometry')
        lexical=lambda word:vision_reading.normalized(word).casefold()
        if (left.get('printed_tail') and lexical(tail_text.split()[-1])!=lexical(left['printed_tail']) or
                right.get('printed_head') and lexical(head_text.split()[0])!=lexical(right['printed_head'])):
            return decision('unmapped','known_source_endpoint_disagreement')
        # With no independently mapped ink, retain the reader's two-sided
        # decision. Do not infer a seam from lowercase alone in that situation.
        if left.get('continues_next') and right.get('continues_previous'):
            return decision('joined','reader_pair_without_optical_mapping')
        return decision('unmapped','missing_opening_geometry')
    territories=right.get('source_regions') or []
    if not territories:return decision('unmapped','missing_source_territory')
    box=territories[0]
    column_left=(right.get('native_list_continuation_margin') if right.get('native_list_continuation')
                 else right.get('opening_column_left'))
    if (column_left is None and left.get('native_source') and right.get('native_source')
            and len(right.get('source_rows',[]))==1 and len(left.get('source_rows',[]))>1
            and left.get('native_page_foot') and right.get('native_page_top')
            and left.get('opening_column_left') is not None
            and abs(first_x-left['opening_column_left'])<=3):
        # A short continuation of an inset paragraph inherits that paragraph's
        # own row margin, not an unrelated later paragraph's general margin.
        column_left=left['opening_column_left']
    if column_left is None:
        column_left=min((b['source_regions'][0][0] for b in blocks
            if b.get('role')=='body' and b.get('source_regions')
            and b['source_regions'][0][0]>=box[0]-20
            and b['source_regions'][0][0]<box[2]),default=box[0])
    allowance=3
    if right.get('native_source') and right.get('printed_head_box'):
        # A single native word-space advance can slightly exceed three points.
        # Allow that small font-scaled offset, while keeping a real first-line
        # indentation measured from the uniquely bound printed row.
        head_box=right['printed_head_box']
        allowance=max(allowance,(head_box[3]-head_box[1])*(.5 if right.get('native_list_continuation') else .25))
    if first_x>column_left+allowance:
        lexical=lambda word:vision_reading.normalized(word).casefold()
        head_box=right.get('printed_head_box');tail_box=left.get('printed_tail_box')
        # A lone native orphan may retain a modest inset at the next page top.
        # Admit only exact painted comma/lowercase endpoints at both page edges;
        # headings, complete sentences and general indented paragraphs stay split.
        orphan=bool(left.get('native_source') and right.get('native_source')
            and left.get('native_page_foot') and right.get('native_page_top')
            and len(right.get('source_rows',[]))==1 and head_box and tail_box
            and unwrapped.endswith(',') and opening and opening[0].islower()
            and left.get('printed_tail') and right.get('printed_head')
            and lexical(tail_text.split()[-1])==lexical(left['printed_tail'])
            and lexical(head_text.split()[0])==lexical(right['printed_head'])
            and 0<head_box[3]-head_box[1]
            and first_x-column_left<=2*(head_box[3]-head_box[1]))
        if orphan:return decision('joined','exact_native_comma_lowercase_orphan_at_page_turn')
        return decision('split','indented_opening')
    lexical=lambda word:vision_reading.normalized(word).casefold()
    exact=bool(left.get('printed_tail') and right.get('printed_head')
        and lexical(tail_text.split()[-1])==lexical(left['printed_tail'])
        and lexical(head_text.split()[0])==lexical(right['printed_head']))
    ordinary=assemble.continues(tail_text,head_text)
    physical=bool(left.get('printed_tail_box') and right.get('printed_head_box'))
    native_edge=not left.get('native_source') or left.get('native_page_foot') and right.get('native_page_top')
    if abbreviation_period and not (exact and physical and native_edge):
        return decision('unmapped','abbreviation_without_source_endpoint_proof')
    if exact and physical and native_edge:
        # An unfinished, unindented source sentence can resume with a proper
        # name, numeral, quotation or semicolon clause. Case alone is no veto.
        compatible=bool(opening and (opening[0].isalnum() or opening[0] in ',;'))
        if opening and opening[0].isupper() and tail_text!=unwrapped:
            compatible=bool(left.get('continues_next') and right.get('continues_previous'))
        ordinary=ordinary or compatible
    if ordinary or word_edge:return decision('joined','source_opening_and_unfinished_sentence')
    return decision('unmapped','unproved_lexical_endpoint')


def _source_continuation(left,right,blocks,tail,head):
    return _continuation_decision(left,right,blocks,tail,head)['status']=='joined'


def _seam_hyphen(tail,head,words=(),compounds=()):
    """Delete a discretionary division only with a printed whole-word witness."""
    tail_text,head_text=_seam_text(tail),_seam_text(head)
    if not tail_text.endswith(tuple(assemble.HYPHENS)):return None
    a=re.search(r"([A-Za-z'’]+)["+assemble.HYPHENS+r']$',tail_text)
    b=re.match(r"[A-Za-z'’]+",head_text)
    if a and b and (a[1]+b[0]).casefold() in words and (a[1]+'-'+b[0]).casefold() not in compounds:
        return 'drop'
    return 'keep'


def _scan_wrap_vocabulary(scans):
    """Whole optical words owned by BODY, never arbitrary reader spellings."""
    words=set();compounds=set()
    for n,s in scans.items():
        with unit_recovery.guard(n,'scan_wrap_vocabulary'):
            for block in s['answer']['blocks']:
                if block['role']!='body':continue
                for word in _owned_block_words(block,s['answer'],s['optical'],s['display'],
                                               s['regions'],s['body_owners'],ordered=True):
                    token=word['text'].strip('.,;:!?()[]\"“”')
                    if re.fullmatch(r"[A-Za-z][A-Za-z'’]*",token):words.add(token.casefold())
                    elif re.fullmatch(r"[A-Za-z][A-Za-z'’]*-[A-Za-z][A-Za-z'’]*",token):compounds.add(token.casefold())
    return words,compounds


def _native_paragraph_source_model(rows,spans,rect,body_size):
    """Original row cadence and repeated fields fence native prose joins.

    This is presentation evidence, not text recognition. A short number or bold
    run is never itself a boundary. Only complete original row ownership admits
    a display/table veto; ordinary spacing learns the source's own body cadence.
    """
    import statistics
    records=[]
    for row in rows:
        box=vision_reading.union([w['bbox']for w in row])
        height=box[3]-box[1]
        local=[s for s in spans if s['bbox'][0]<box[2]+.5 and box[0]-.5<s['bbox'][2]
            and abs((s['bbox'][1]+s['bbox'][3]-box[1]-box[3])/2)<height*.45]
        baseline=[s for s in local if not body_size or s['size']>=body_size*.85]
        size=statistics.median(s['size']for s in baseline)if baseline else None
        starts=[row[0]['bbox'][0]]+[b['bbox'][0]for a,b in zip(row,row[1:])
            if b['bbox'][0]-a['bbox'][2]>height*1.2]
        display=bool(body_size and local and len(row)<=12
            and all(s['flags']&16 for s in local)
            and min(s['size']for s in local)>body_size*1.15
            and max(s['size']for s in local)<=min(s['size']for s in local)*1.05
            and abs((box[0]+box[2]-rect.width)/2)<=height*1.5
            and box[2]-box[0]<rect.width*.6)
        ordered=sorted(baseline,key=lambda span:span['bbox'][0])
        label=bool(ordered and ordered[0]['flags']&16
            and any(not span['flags']&16 for span in ordered[1:]))
        label=label or bool(row[0]['text'][:1].isupper()
            and any(word['text'].endswith(':')for word in row[:3]))
        records.append(dict(box=box,height=height,size=size,starts=starts,display=display,table=False,
            labelled_opening=label,lowercase_opening=row[0]['text'][:1].islower()))
    for row in records:
        if len(row['starts'])<3:continue
        for other in records:
            if row is other or len(other['starts'])<3:continue
            height=max(row['height'],other['height'])
            if not 0<abs(other['box'][1]-row['box'][1])<=height*3:continue
            if abs(row['starts'][0]-other['starts'][0])>height*.3:continue
            matches=sum(any(abs(x-y)<=height*.3 for y in other['starts'][1:])for x in row['starts'][1:])
            if matches>=2:row['table']=True;break
    gaps=[]
    for a,b in zip(records,records[1:]):
        height=max(a['height'],b['height'])
        gap=b['box'][1]-a['box'][3]
        if (a['table']or b['table']or a['display']or b['display']or
                not a['size']or not b['size']or not body_size or
                not body_size*.95<=a['size']<=body_size*1.05 or
                not body_size*.95<=b['size']<=body_size*1.05 or
                abs(a['box'][0]-b['box'][0])>height*.65 or
                not 0<=gap<=height*1.5):continue
        gaps.append(gap)
    # A lower-quartile source regime needs several independent adjacent pairs.
    # Uniform double spacing learns that same wider cadence; isolated blank
    # gaps cannot establish ordinary prose cadence by themselves.
    normal=sorted(gaps)[(len(gaps)-1)//4]if len(gaps)>=3 else None
    return dict(rows=records,normal_gap=normal,body_size=body_size)


def _native_source_row_record(box,model,complete=False):
    if not model:return None
    candidates=[row for row in model['rows']if
        abs(row['box'][1]-box[1])<=.5 and abs(row['box'][3]-box[3])<=.5
        and row['box'][0]-.5<=box[0] and box[2]<=row['box'][2]+.5
        and (not complete or abs(row['box'][0]-box[0])<=.5
             and abs(row['box'][2]-box[2])<=.5)]
    return candidates[0]if len(candidates)==1 else None


def _native_source_structural_row(box,model):
    row=_native_source_row_record(box,model,complete=True)
    return 'table'if row and row['table']else'display'if row and row['display']else None


def _native_source_paragraph_gap(previous,current,model):
    if not model or model['normal_gap']is None:return False
    a=_native_source_row_record(previous,model);b=_native_source_row_record(current,model)
    size=model['body_size']
    if (not a or not b or not size or not a['size']or not b['size']or
            not size*.95<=a['size']<=size*1.05 or
            not size*.95<=b['size']<=size*1.05):return False
    height=max(a['height'],b['height']);gap=current[1]-previous[3]
    normal=model['normal_gap'];local=[];lowercase=False
    midpoint=(previous[3]+current[1])/2
    for left,right in zip(model['rows'],model['rows'][1:]):
        local_height=max(left['height'],right['height'])
        distance=abs((left['box'][3]+right['box'][1])/2-midpoint)
        spacing=right['box'][1]-left['box'][3]
        if (distance>height*4 or left['table']or right['table']or left['display']or right['display']
                or left['labelled_opening']or right['labelled_opening']
                or not left['size']or not right['size']
                or not size*.95<=left['size']<=size*1.05
                or not size*.95<=right['size']<=size*1.05
                or abs(left['box'][0]-right['box'][0])>local_height*.65
                or abs(left['box'][0]-current[0])>local_height*.65
                or not 0<=spacing<=local_height*1.5):continue
        local.append(spacing);lowercase=lowercase or right['lowercase_opening']
    # An independently coherent neighboring run can use a different cadence
    # from the rest of the page. Printed label starts and all-capital opening
    # lines do not establish that run; inline emphasis is not itself a veto.
    if len(local)>=2 and lowercase and max(local)-min(local)<=height*.25:
        import statistics
        cadence=statistics.median(local)
        if abs(gap-cadence)<=height*.25:normal=cadence
    return gap>normal+height*.5


def _native_fraction_ownership(doc,book,pno,physical):
    """Restore original fraction placement without adopting optical spellings."""
    from .native_text import drawn_word_authority
    if book.has_source_navigation(pno):return []
    elements=book.pages.get(pno,[])
    candidates=[e for e in elements if e.kind=='p' and e.bbox and
        all(r[0]in('t','raised')for r in e.runs) and any(r[0]=='raised'for r in e.runs)
        and '/'in e.text]
    if not candidates:return []
    raw=extract.read_page(doc,pno,keep_char_boxes=True)
    lines=[word['_line']for row in _pixel_lines([dict(text=line.text,bbox=line.bbox,_line=line)
        for b in raw.blocks for line in b.lines])for word in row]
    flat=lambda value:''.join(vision_reading.normalized(value).split())
    repairs=[]
    for element in candidates:
        original=''.join(r[1]for r in element.runs);proposals=[]
        for first,line in enumerate(lines):
            if not owned_words([dict(bbox=line.bbox)],element.bbox):continue
            for last in range(first,len(lines)):
                selected=lines[first:last+1]
                if len(flat(' '.join(ln.text for ln in selected)))>len(flat(original)):break
                parts=[part for ln in selected for span in ln.spans
                    for part in getattr(span,'source_fraction_parts',())]
                if len(parts)!=1:continue
                num,den,*_=parts[0]
                for pair in re.finditer(re.escape(den)+r'\s*/',original):
                    for digit in re.finditer(r'(?<!\w)'+re.escape(num)+r'(?!\w)',original):
                        if digit.start()<pair.end():continue
                        proposed=original[:pair.start()]+num+'/'+den+original[pair.end():digit.start()]+original[digit.end():]
                        source=' '.join(ln.text for ln in selected)
                        if flat(proposed)!=flat(source):continue
                        own=[w for w in physical if any(owned_words([w],ln.bbox)for ln in selected)]
                        # The decoded stream can reverse a fraction's atoms;
                        # the lexical view binds those exact original glyphs.
                        from .native_text import lexical_word_view
                        lexical=lexical_word_view(doc[pno],own)
                        if not drawn_word_authority(doc[pno],lexical):continue
                        if any(other is not element and other.kind in('p','h','caption')and
                               other.bbox and owned_words(own,other.bbox)for other in elements):continue
                        proposals.append((selected,source,own))
        if len(proposals)!=1:continue
        selected,source,own=proposals[0];runs=[]
        for ln in selected:
            if runs:runs.append(['t',' '])
            runs.extend(assemble._line_runs(ln,pno,set(),set(),[],[],preserve_style=True))
        if flat(assemble.plain_text(runs))!=flat(source):continue
        element.runs=runs;element.bbox=tuple(vision_reading.union([w['bbox']for w in own]))
        element.line_boxes=[ln.bbox for ln in selected]
        repairs.append(dict(page=pno,kind='original_stacked_fraction_placement',
            source_fraction_parts=1,complete_visible_native_words=True,
            original_source_typography=True))
    return repairs


def _native_prefix_label_ownership(doc,book,pno,physical):
    """Restore a detached original prefix without changing prose or styles."""
    from .native_text import lexical_word_view,drawn_word_authority
    if book.has_source_navigation(pno):return []
    words=_pixel_line_order(lexical_word_view(doc[pno],physical))
    norm=vision_reading.normalized
    elements=book.pages.get(pno,[]);repairs=[]
    for index,element in enumerate(elements):
        if (element.kind!='p' or not element.bbox or not element.runs or
                any(run[0]!='t' or len(run)>3 for run in element.runs)):continue
        raw=''.join(run[1]for run in element.runs)
        tokens=list(re.finditer(r'\S+',raw))
        if len(tokens)<8 or tokens[0].group().isdigit():continue
        proposed=[norm(t.group())for t in tokens];proofs=[]
        for position,token in enumerate(tokens):
            label=token.group()
            if not re.fullmatch(r'\d{1,3}',label)or proposed.count(label)!=1:continue
            body=proposed[:position]+proposed[position+1:]
            for start,head in enumerate(words):
                source=words[start:start+len(tokens)]
                if len(source)!=len(tokens)or head['text']!=label:continue
                if [norm(w['text'])for w in source[1:]]!=body:continue
                a,b=head['bbox'],source[1]['bbox'];height=b[3]-b[1]
                if not (0<a[3]-a[1]<height*.9 and 0<=b[0]-a[2]<=height*.5
                        and min(a[3],b[3])>max(a[1],b[1])
                        and a[3]<b[3]-height*.15):continue
                if not drawn_word_authority(doc[pno],source):continue
                proofs.append((position,source))
        if len(proofs)!=1:continue
        position,source=proofs[0]
        if not owned_words([source[1]],element.bbox):continue
        if any(other is not element and other.kind in('p','h','caption')and other.bbox
                and owned_words(source,other.bbox)for other in elements):continue
        token=tokens[position];cursor=0;run_position=None
        for ri,run in enumerate(element.runs):
            if cursor<=token.start()<token.end()<=cursor+len(run[1]):
                if len(run)>2 and run[2]:break
                run_position=(ri,token.start()-cursor,token.end()-cursor);break
            cursor+=len(run[1])
        if run_position is None:continue
        ri,a,z=run_position;run=element.runs[ri]
        run[1]=run[1][:a]+run[1][z:]
        element.runs.insert(0,['t',tokens[position].group()+' '])
        rows=_pixel_lines(source);element.bbox=tuple(vision_reading.union([w['bbox']for w in source]))
        element.line_boxes=[tuple(vision_reading.union([w['bbox']for w in row]))for row in rows]
        repairs.append(dict(page=pno,block='native-element-'+str(index),
            kind='original_detached_native_prefix',source_label=source[0]['text'],
            source_label_bbox=source[0]['bbox'],source_words=len(source),
            previous_token_index=position,complete_visible_native_words=True,
            original_styles_preserved=True))
    return repairs


def _native_inline_paragraph_ownership(doc,book,pno,physical):
    """Keep uniquely source-bound native prose and semantic atoms together.

    Cached merged rectangles can omit earlier/later rows. The ORIGINAL ordered
    characters establish their complete extent; a repeated phrase never selects
    a different physical owner. Runs remain unchanged, including raised atoms,
    typography, literal hyphens and annotation anchors.
    """
    import statistics
    flat=lambda value:''.join(vision_reading.normalized(value).split())
    body_size=getattr(book.style,'body_size',0) if book.style else 0
    elements=book.pages.get(pno,[]);repairs=[]
    ambiguous=book.ambiguous_note_numbers(pno)
    available={str(n.num) for n in book.notes if n.pno==pno and n.num is not None}-ambiguous
    def published_characters(e):
        # The source inventory's bracketed note token is semantic notation.
        # Only the existing factory's actual original marker display can bind
        # the original characters. Literal brackets remain literal characters.
        if any(r[0] not in ('t','raised','sup') for r in e.runs):return None
        if any(r[0]=='sup' and (str(r[1]) not in available or
                len(r)>3 and r[3]=='uncertain') for r in e.runs):return None
        return flat(_seam_text(build_epub._runs_html(e.runs,available,{},ambiguous)))
    def terminal_prose(e):
        return ''.join(r[1]for r in e.runs if r[0]!='sup').strip()
    with unit_recovery.guard(pno,'native_inline_source'):
        spans=[s for b in doc[pno].get_text('dict',flags=extract.pymupdf.TEXTFLAGS_TEXT)['blocks']
               for line in b.get('lines',[]) for s in line['spans'] if s['text'].strip()]
        original_rows=_pixel_lines(physical)
        model=_native_paragraph_source_model(original_rows,spans,doc[pno].rect,body_size)
        evidence=getattr(book,'native_paragraph_source_evidence',{})
        evidence[pno]=model;book.native_paragraph_source_evidence=evidence
        word_records=[];characters=[];character_words=[]
        optional_hyphens=set()
        for ri,row in enumerate(original_rows):
            for wi,word in enumerate(row):
                identifier=len(word_records);word_records.append((ri,word))
                value=flat(word['text'])
                for char in value:
                    characters.append(char);character_words.append(identifier)
                if (wi==len(row)-1 and value.endswith('-') and ri+1<len(original_rows)):
                    following=original_rows[ri+1];head=following[0]
                    a=word['bbox'];b=head['bbox'];height=max(a[3]-a[1],b[3]-b[1],1)
                    if (head['text'][:1].islower() and 0<=b[1]-a[3]<=height*1.5
                            and abs(b[0]-row[0]['bbox'][0])<=height*.65):
                        # This does not heal exported text. It proves ownership
                        # for a native run already joined by its prior factory.
                        optional_hyphens.add(len(characters)-1)
        original=''.join(characters)
        variants=[(original,list(range(len(original))))]
        if optional_hyphens:
            positions=[i for i in range(len(original)) if i not in optional_hyphens]
            variants.append((''.join(original[i] for i in positions),positions))
    def source_rows(e):
        if (e.table_row or not e.line_boxes or not e.runs or
                e.punctuation_uncertain or e.list_items):return None
        candidate=published_characters(e)
        if not candidate:return None
        matches=set()
        for sequence,positions in variants:
            offset=0
            while True:
                found=sequence.find(candidate,offset)
                if found<0:break
                last=found+len(candidate)-1
                a,z=positions[found],positions[last]+1
                matches.add((a,z));offset=found+1
        if len(matches)!=1:return None
        a,z=next(iter(matches))
        # Whole original word instances fence the textual proof. Character
        # equality alone must not borrow a clipped foreign word at an edge.
        first,last=character_words[a],character_words[z-1]
        if (a and character_words[a-1]==first or
                z<len(character_words) and character_words[z]==last):return None
        selected=word_records[first:last+1]
        source_overlap=False
        for _,word in selected:
            for box in e.line_boxes:
                if owned_words([word],box):
                    source_overlap=True
                    break
            if source_overlap:break
        if not source_overlap:return None
        rows=[];boxes=[];indices=[]
        for ri,word in selected:
            if not indices or ri!=indices[-1]:indices.append(ri);rows.append([])
            rows[-1].append(word)
        boxes=[vision_reading.union([word['bbox']for word in row])for row in rows]
        if boxes!=sorted(boxes,key=lambda row:(row[1],row[0])):return None
        return rows,boxes,indices,a,z
    output=[];index=0
    while index<len(elements):
        left=elements[index];start=index;index+=1
        while index<len(elements):
            right=elements[index]
            if left.kind not in ('h','p') or right.kind!='p' or left.column!=right.column:break
            with unit_recovery.guard(pno,'native_inline_paragraph',block='native-element-'+str(start)):
                a=source_rows(left);b=source_rows(right)
                if not a or not b:break
                rows,boxes,row_ids,a_start,a_end=a
                next_rows,next_boxes,next_ids,b_start,b_end=b
                tail=boxes[-1];head=next_boxes[0]
                height=statistics.median([q[3]-q[1]for q in boxes+next_boxes])
                following=statistics.median([q[0]for q in next_boxes[1:4]]or[tail[0]])
                gap=head[1]-tail[3]
                same_row=row_ids[-1]==next_ids[0]
                consecutive=(a_end==b_start and (same_row or next_ids[0]==row_ids[-1]+1))
                structural=(not same_row and (_native_source_structural_row(tail,model)
                    or _native_source_structural_row(head,model)
                    or _native_source_paragraph_gap(tail,head,model)))
                if (structural or not consecutive or re.search(r'[.!?][\)\]”’"\']*$',terminal_prose(left)) or
                        same_row and not -.5<=head[0]-tail[2]<=height*1.5 or
                        not same_row and (not 0<=gap<=height*1.5 or abs(head[0]-tail[0])>height*4
                            or head[0]-following>height*.65)):
                    break
                kind=left.kind
                if kind=='h':
                    owned_spans=[s for s in spans if all((s['bbox'][i]>=boxes[0][i]-.5
                        if i<2 else s['bbox'][i]<=boxes[0][i]+.5)for i in range(4))]
                    styles=[bool(s['flags']&16)for s in owned_spans]
                    if (len(boxes)!=1 or not body_size or not owned_spans or
                            not styles[0] or not any(not bold for bold in styles) or
                            max(s['size']for s in owned_spans)>body_size*1.05 or
                            min(s['size']for s in owned_spans)<body_size*.95 or
                            boxes[0][0]-following<=height*.65):break
                left=copy.deepcopy(left);left.kind='p';left.level=None
                left.runs=copy.deepcopy(left.runs)+[['t',' ']]+copy.deepcopy(right.runs)
                joined_boxes=list(boxes)
                if same_row:joined_boxes[-1]=vision_reading.union([joined_boxes[-1],next_boxes[0]]);joined_boxes.extend(next_boxes[1:])
                else:joined_boxes.extend(next_boxes)
                left.line_boxes=[tuple(box)for box in joined_boxes]
                left.bbox=tuple(vision_reading.union(joined_boxes))
                left.pages=sorted(set((left.pages or[pno])+(right.pages or[pno])))
                index+=1
                repairs.append(dict(page=pno,block='native-element-'+str(start),kind='source_proven_inline_native_paragraph',
                    original_kind=kind,published_kind='p',same_original_row_mixed_body_type=kind=='h',
                    original_styles_preserved=True,source_indented_opening=boxes[0][0]-following>height*.65,
                    source_continuation_gap=gap,source_rows=len(joined_boxes),source_exact_order=True,
                    source_complete_native_extent=True,source_semantic_atoms_preserved=True))
        output.append(left)
    book.pages[pno]=output
    return repairs


def _native_navigation_publication_proved(book, original, raw_pages):
    """Conserve every annotation and independently verify its published homes.

    A nominally resolved status is insufficient. The original printed line and
    destination viewport must still have their unique, literal indexed owners.
    """
    from collections import Counter
    current=book.source_navigation
    if Counter(r['id'] for r in current)!=Counter(r['id'] for r in original):return False
    indexed={r['id']:r for r in current}
    raw_by_page={raw.pno:raw for raw in raw_pages}
    binding={'status','source_element','source_item','source_offset','source_extent',
             'dest_element','dest_item','dest_offset','dest_note','destination_evidence'}
    for old in original:
        new=indexed[old['id']]
        if any(new.get(k)!=v for k,v in old.items() if k not in binding):return False
        if old['status']!='resolved':continue
        if new['status']!='resolved':return False
        source_homes=[]
        for ei,e in enumerate(book.pages.get(new['pno'],[])):
            items=list(zip(e.list_items,e.list_item_boxes)) if e.kind=='list' else [(e.runs,e.line_boxes)]
            for ii,(runs,boxes) in enumerate(items):
                if not any(abs(b[1]-new['line_box'][1])<1.5 and abs(b[3]-new['line_box'][3])<1.5 for b in boxes):continue
                text=assemble.plain_text(runs)
                offset=assemble._source_label_offset(text,new['line_text'],new['label'])
                if offset is None:continue
                token='['+new['label']+']';extent=len(new['label'])
                if text.startswith(token,offset):extent=len(token)
                elif text[offset-1:offset+len(new['label'])+1]==token:offset-=1;extent=len(token)
                source_homes.append((ei,ii if e.kind=='list' else None,offset,extent))
        if source_homes!=[(new['source_element'],new['source_item'],new['source_offset'],new['source_extent'])]:return False
        target=raw_by_page.get(new['dest_page'])
        if target is None:return False
        x,y=new['dest_point'];candidates=[]
        lines=[line for block in target.text_blocks for line in block.lines]
        for line in lines:
            b=line.bbox;gap=max(b[1]-y,y-b[3],0)
            if gap<=10 and b[0]-12<=x<=b[2]+12:
                direction=0 if b[1]<=y<=b[3] else (1 if y<b[1] else 2)
                candidates.append((direction,gap,line))
        candidates.sort(key=lambda item:item[:2])
        if not candidates or len(candidates)>1 and candidates[0][:2]==candidates[1][:2]:return False
        line=candidates[0][2];homes=[]
        for ei,e in enumerate(book.pages.get(new['dest_page'],[])):
            if e.kind=='list':
                for ii,boxes in enumerate(e.list_item_boxes):
                    if any(abs(b[1]-line.bbox[1])<1.5 and abs(b[3]-line.bbox[3])<1.5 for b in boxes):homes.append((ei,ii,0,None))
            elif e.kind in ('p','h','caption') and any(abs(b[1]-line.bbox[1])<1.5 and abs(b[3]-line.bbox[3])<1.5 for b in e.line_boxes):
                offset=assemble._line_owned_offset(e.text,line.stripped,line.bbox,e.line_boxes,lines)
                if offset is not None:homes.append((ei,None,offset,None))
        for ni,note in enumerate(n for n in book.notes if n.pno==new['dest_page']):
            b=note.bbox
            if b[0]-1.5<=line.bbox[0] and line.bbox[2]<=b[2]+1.5 and abs(line.bbox[1]-b[1])<3 and line.bbox[3]<=b[3]+1.5:
                homes.append((None,None,0,ni))
        if homes!=[(new.get('dest_element'),new.get('dest_item'),new.get('dest_offset'),new.get('dest_note'))]:return False
    return True


def _native_list_head_ownership(doc,book,pno):
    """Separate a continued list suffix from independently printed next items.

    Only exact native words, their original rows and the preceding source list
    can establish this boundary. No source words or semantic runs change pages
    here; the later source-bound markup seam preserves their existing homes.
    """
    import statistics
    previous=book.pages.get(pno-1,[]);elements=book.pages.get(pno,[])
    if not previous or not elements or previous[-1].kind!='list':return []
    left=previous[-1];at=next((i for i,e in enumerate(elements)if e.kind not in ('fig','caption')),None)
    if at is None or elements[at].kind!='p':return []
    right=elements[at]
    if (left.column!=right.column or not left.list_items or not left.list_item_boxes or
            not right.line_boxes or right.list_items or right.punctuation_uncertain or
            right.table_row or any(r[0]!='t'for r in right.runs)):return []
    last=assemble.plain_text(left.list_items[-1]).strip()
    number=re.match(r'^(\d+)([.)])(?:\s|$)',last)
    if not number or re.search(r'[.!?:…][\)\]”’"\']*$',last):return []
    controller=unit_recovery.current();units=[dict(page=pno-1,block='native-element-'+str(len(previous)-1)),dict(page=pno,block='native-element-'+str(at))]
    if controller and any(u['block']in controller.blocked(u['page'])for u in units):return []
    with unit_recovery.guard([pno-1,pno],'native_list_head_ownership',units=units):
        def source(e,page,boxes,text):
            words=[dict(text=w[4],bbox=list(w[:4]))for w in doc[page].get_text('words')]
            owned=_pixel_line_order([w for w in words if any(owned_words([w],b)for b in boxes)])
            norm=lambda value:vision_reading.normalized(value).split()
            if not owned or norm(text)!=norm(' '.join(w['text']for w in owned)):return None
            rows=_pixel_lines(owned)
            if any(min(a[-1]['bbox'][3],b[-1]['bbox'][3])-max(a[0]['bbox'][1],b[0]['bbox'][1])>0
                    for a,b in zip(rows,rows[1:])):return None
            return owned,rows
        a=source(left,pno-1,left.list_item_boxes[-1],last)
        b=source(right,pno,right.line_boxes,right.text)
        if not a or not b:return []
        left_words,left_rows=a;right_words,right_rows=b
        height=statistics.median(w['bbox'][3]-w['bbox'][1]for w in left_words+right_words)
        if left_words[-1]['bbox'][3]<=doc[pno-1].rect.height*.7 or right_words[0]['bbox'][1]>=doc[pno].rect.height*.4:return []
        expected=int(number[1])+1;cuts=[];cursor=0
        label_margin=left_rows[0][0]['bbox'][0]
        for ri,row in enumerate(right_rows):
            match=re.fullmatch(r'(\d+)'+re.escape(number[2]),row[0]['text'])
            if match:
                if (ri==0 or int(match[1])!=expected or abs(row[0]['bbox'][0]-label_margin)>max(1.5,height*.25)):return []
                cuts.append((cursor,ri));expected+=1
            cursor+=len(row)
        if not cuts:return []
        first_cut,first_row=cuts[0];prefix_words=right_words[:first_cut]
        prefix=' '.join(w['text']for w in prefix_words)
        left_margin=(statistics.median(row[0]['bbox'][0]for row in left_rows[1:])
                     if len(left_rows)>1 else left_rows[0][1]['bbox'][0]
                     if len(left_rows[0])>1 else None)
        if left_margin is None:return []
        l=dict(role='body',native_source=True,native_page_foot=True,printed_tail=left_words[-1]['text'],printed_tail_box=left_words[-1]['bbox'],source_rows=[vision_reading.union([w['bbox']for w in row])for row in left_rows],opening_column_left=left_margin)
        prefix_rows=right_rows[:first_row];prefix_box=vision_reading.union([w['bbox']for w in prefix_words])
        r=dict(role='body',native_source=True,native_list_continuation=True,native_list_continuation_margin=left_margin,native_page_top=True,printed_head=prefix_words[0]['text'],printed_head_box=prefix_words[0]['bbox'],first_line_x=prefix_rows[0][0]['bbox'][0],opening_column_left=left_margin,source_regions=[prefix_box],source_rows=[vision_reading.union([w['bbox']for w in row])for row in prefix_rows],native_geometry_binding='unique_complete_source_text')
        decision=_continuation_decision(l,r,[r],last,prefix)
        if decision['status']!='joined':return []
        tokens=list(re.finditer(r'\S+',right.text));bounds=[tokens[count].start()for count,_ in cuts]+[len(right.text)]
        def slice_runs(start,end):
            out=[];offset=0
            for run in right.runs:
                length=len(run[1]);a=max(0,start-offset);z=min(length,end-offset)
                if a<z:out.append([run[0],run[1][a:z],*copy.deepcopy(run[2:])])
                offset+=length
            return out
        prefix_element=copy.deepcopy(right);prefix_element.runs=slice_runs(0,bounds[0]);prefix_element.line_boxes=[tuple(q)for q in r['source_rows']];prefix_element.bbox=tuple(prefix_box)
        prefix_element.native_list_continuation=True;prefix_element.native_list_continuation_margin=left_margin
        prefix_element.native_list_continuation_from=pno-1
        new_list=copy.deepcopy(right);new_list.kind='list';new_list.level=None
        new_list.list_items=[slice_runs(a,z)for a,z in zip(bounds,bounds[1:])]
        new_list.list_item_boxes=[[tuple(vision_reading.union([w['bbox']for w in row]))for row in right_rows[ri:next_ri]]for (_,ri),(_,next_ri)in zip(cuts,cuts[1:]+[(len(right_words),len(right_rows))])]
        new_list.runs=[]
        for item in new_list.list_items:
            if new_list.runs:new_list.runs.append(['t',' '])
            # Only factory separator whitespace changes representation.
            cleaned=copy.deepcopy(item)
            if cleaned:cleaned[0][1]=cleaned[0][1].lstrip();cleaned[-1][1]=cleaned[-1][1].rstrip()
            item[:]=cleaned;new_list.runs.extend(copy.deepcopy(cleaned))
        new_list.line_boxes=[box for boxes in new_list.list_item_boxes for box in boxes]
        new_list.bbox=tuple(vision_reading.union(new_list.line_boxes))
        if vision_reading.normalized(prefix_element.text+' '+new_list.text).split()!=vision_reading.normalized(right.text).split():raise ValueError('Original native list split did not conserve source words')
        # Above-page art floats after the now completed original list item.
        book.pages[pno]=[prefix_element]+elements[:at]+[new_list]+elements[at+1:]
        return [dict(page=pno,previous_page=pno-1,block='native-element-'+str(at),kind='source_proven_native_list_head_split',source_words=len(right_words),source_prefix_rows=len(prefix_rows),published_next_items=len(new_list.list_items),source_continuation_reason=decision['reason'],original_native_runs_preserved=True,original_list_suffix_owned=True,art_deferred_to_item_boundary=bool(at))]


def _native_publication_ownership(doc,book,numbers,raw_pages=None):
    """Repair cached native publication only from complete original ownership.

    Runs with semantic actions or uncertain native text are never reconstructed.
    Exact plain-text rows permit ordering and paragraph repair; connected original
    vector paths permit a direct crop only outside every original text territory.
    Unproved vector separation gets a visible optional whole-source context.
    """
    from collections import Counter
    import statistics
    repairs=[];contexts=[]
    norm=lambda value:vision_reading.normalized(value).split()
    for pno in numbers:
        # Cached navigation indexes are semantic ownership, even on plain runs.
        # Without original annotation evidence retain the validated units.
        if book.has_source_navigation(pno) and not raw_pages:
            continue
        with unit_recovery.guard(pno,'native_publication'):
            with unit_recovery.guard(pno,'native_publication_source'):
                page=doc[pno];elements=book.pages.get(pno,[])
                physical=[dict(text=w[4],bbox=list(w[:4])) for w in page.get_text('words')]
                drawings=page.get_drawings()
                # Original connected-path groups, not the cached figure inventory,
                # establish the complete vector extent. Single rules/borders do not
                # prove artwork; readable original words forbid a direct crop.
                art_paths=[d for d in drawings if not (d['rect'].width>page.rect.width*.9
                    and d['rect'].height>page.rect.height*.9)]
                clusters=page.cluster_drawings(drawings=art_paths) if art_paths else []
            indexed_source=bool(raw_pages and book.has_source_navigation(pno))
            if indexed_source:
                original_page=copy.deepcopy(book.pages[pno])
                original_figures=copy.deepcopy(book.figures)
                original_navigation=copy.deepcopy(book.source_navigation)
                repair_start=len(repairs)
            additions=[]
            for index,cluster in enumerate(clusters):
                bid='native-vector-'+str(index)
                controller=unit_recovery.current()
                rejected=controller.blocked(pno).get(bid) if controller else None
                if rejected:
                    # This source-derived vector has no cached native element to
                    # replace. Preserve its exact original failure owner and render
                    # an optional whole-source context, retaining all live prose.
                    contexts.append(dict(rejected,seed_bbox=list(cluster)))
                    continue
                with unit_recovery.guard(pno,'native_vector_ownership',block=bid):
                    rect=extract.pymupdf.Rect(cluster)
                    paths=[d for d in art_paths if rect.contains(d['rect'])]
                    pad=max([1.]+[float(d.get('width') or 0)/2 for d in paths])
                    box=list(extract.pymupdf.Rect(rect.x0-pad,rect.y0-pad,rect.x1+pad,rect.y1+pad)&page.rect)
                    rect=extract.pymupdf.Rect(box)
                    if len(paths)<3 or rect.width<20 or rect.height<20:continue
                    if any(e.kind=='fig' and e.bbox and extract.pymupdf.Rect(e.bbox).intersects(rect) for e in elements):continue
                    # Page-border clusters can enclose disconnected prose and art;
                    # they must not turn a page frame into a primary illustration.
                    if rect.width>page.rect.width*.9 and rect.height>page.rect.height*.9:continue
                    foreign=[w for w in physical if extract.pymupdf.Rect(w['bbox']).intersects(rect)]
                    if foreign:
                        contexts.append(dict(page=pno,block='native-vector-'+str(index),seed_bbox=box,
                            reason='Connected original vector artwork overlaps original text; separation unproved',
                            status='DEGRADED_SOURCE_CONTEXT'))
                        continue
                    element=assemble.Element('fig',pno=pno,bbox=tuple(box),placed='geometry')
                    at=next((i for i,e in enumerate(elements) if e.bbox and e.bbox[1]>=box[3]),len(elements))
                    elements.insert(at,element);additions.append(dict(pno=pno,bbox=box,found='native'))
                    repairs.append(dict(page=pno,block='native-vector-'+str(index),kind='complete_original_vector_crop',
                        source_bbox=box,source_paths=len(paths),source_body_overlap=False))
            if additions:
                existing=[f for f in book.figures if f['pno']==pno]+additions
                ordered=[]
                for e in elements:
                    if e.kind!='fig':continue
                    matches=[f for f in existing if tuple(f['bbox'])==tuple(e.bbox)]
                    if len(matches)!=1:raise ValueError('Native figure source inventory is not one-to-one')
                    ordered.append(matches[0])
                book.figures=[f for f in book.figures if f['pno']!=pno]+ordered
            repairs.extend(_native_fraction_ownership(doc,book,pno,physical))
            repairs.extend(_native_prefix_label_ownership(doc,book,pno,physical))
            repairs.extend(_native_inline_paragraph_ownership(doc,book,pno,physical))
            elements=book.pages[pno]
            # Reconstruct only a complete, exact plain-text same-column run. The
            # source rows, not overlapping enclosing boxes, own lexical content.
            result=[];index=0
            while index<len(elements):
                start=index;group=[]
                while index<len(elements):
                    e=elements[index]
                    if (e.kind!='p' or not e.line_boxes or not e.runs or e.table_row or
                        any(r[0]!='t' or (len(r)>2 and r[2]) for r in e.runs) or
                        (group and e.column!=group[0].column)):break
                    group.append(e);index+=1
                if not group:
                    result.append(elements[index]);index+=1;continue
                if len(group)<2:result.extend(group);continue
                with unit_recovery.guard(pno,'native_row_ownership',block='native-element-'+str(start)):
                    rows={};owners={};proved=True
                    for ei,e in enumerate(group):
                        selected=[]
                        for line in e.line_boxes:
                            owned=owned_words(physical,line)
                            if not owned:proved=False;break
                            row=tuple(vision_reading.union([w['bbox'] for w in owned]))
                            if row in rows and owners[row]!=ei:proved=False;break
                            rows[row]=owned;owners[row]=ei;selected.extend(owned)
                        if not proved or Counter(norm(' '.join(w['text'] for w in selected)))!=Counter(norm(e.text)):
                            proved=False;break
                    if not proved:result.extend(group);continue
                    ordered=sorted(rows,key=lambda row:(row[1],row[0]))
                    heights=[r[3]-r[1] for r in ordered];height=statistics.median(heights)
                    # A foreign side-by-side column cannot be sorted by page y.
                    if any(min(a[3],b[3])-max(a[1],b[1])>height*.3 for a,b in zip(ordered,ordered[1:])):
                        result.extend(group);continue
                    old_order=[tuple(vision_reading.union([w['bbox'] for w in owned_words(physical,line)]))
                               for e in group for line in e.line_boxes]
                    reordered=old_order!=ordered
                    rebuilt=[];pending=[]
                    def emit():
                        if not pending:return
                        texts=[' '.join(w['text'] for w in rows[r]) for r in pending]
                        e=copy.deepcopy(group[owners[pending[0]]]);e.runs=[['t',' '.join(texts)]]
                        e.bbox=tuple(vision_reading.union(pending));e.line_boxes=list(pending)
                        rebuilt.append(e);pending.clear()
                    for row in ordered:
                        if pending:
                            previous=pending[-1];tail=' '.join(w['text'] for w in rows[previous]);head=rows[row]
                            # Compare the next row with nearby subsequent starts in
                            # its same source territory, preserving printed indents.
                            following=[r for r in ordered if row[1]<r[1]<=row[1]+height*3 and abs(r[0]-row[0])<height*4]
                            baseline=statistics.median([r[0] for r in following] or [previous[0]])
                            indented=row[0]-baseline>height*.65
                            gap=row[1]-previous[3]
                            different=owners[row]!=owners[previous]
                            model=getattr(book,'native_paragraph_source_evidence',{}).get(pno)
                            structural=(_native_source_structural_row(previous,model)
                                or _native_source_structural_row(row,model)
                                or _native_source_paragraph_gap(previous,row,model))
                            boundary=(structural or gap>height*1.5 or row[0]-previous[0]>height*.65 or
                                      indented or (different and re.search(r'[.!?][\)\]”’"\']*$',tail)))
                            if boundary:emit()
                        pending.append(row)
                    emit()
                    before=[' '.join(norm(e.text)) for e in group];after=[' '.join(norm(e.text)) for e in rebuilt]
                    if before==after:result.extend(group);continue
                    result.extend(rebuilt)
                    repairs.append(dict(page=pno,block='native-element-'+str(start),kind='source_bound_native_paragraphs',
                        original_elements=len(group),published_elements=len(rebuilt),source_rows=len(ordered),
                        reordered_source_rows=reordered,complete_plain_text_correspondence=True))
            book.pages[pno]=result
            repairs.extend(_native_list_head_ownership(doc,book,pno))
            if indexed_source:
                # The original binder proves annotation labels and viewport lines
                # against the final units; cached indexes/offsets cannot survive
                # an insertion, join or row reorder without this proof. Its finer
                # source/destination guards retain ownership on a failed unit.
                selected_raw=[raw for raw in raw_pages if raw.pno in numbers]
                def restore():
                    book.pages[pno]=original_page
                    book.figures=original_figures
                    book.source_navigation=original_navigation
                    del repairs[repair_start:]
                try:
                    book.source_navigation=[]
                    assemble._bind_source_navigation(book,selected_raw)
                    proved=_native_navigation_publication_proved(book,original_navigation,selected_raw)
                except Exception:
                    # Existing owned recovery receives the exact typed cause;
                    # resource/provenance errors retain their fatal identity.
                    restore()
                    raise
                if not proved:
                    restore()
                    repairs.append(dict(page=pno,kind='native_publication_navigation_retained_original',
                        reason='Final original-source navigation did not uniquely conserve every validated annotation; original indexed units retained',
                        original_annotation_geometry=True))
                    for added in additions:
                        contexts.append(dict(page=pno,block='native-vector-navigation-retained-'+str(len(contexts)),
                            seed_bbox=added['bbox'],reason='Original vector extent retained in source context because navigation ownership prevented insertion',
                            status='DEGRADED_SOURCE_CONTEXT'))
                    continue
                repairs.append(dict(page=pno,kind='original_source_native_navigation_rebound',
                    original_annotation_geometry=True,published_unit_ownership=True))
    return repairs,contexts


def _native_caption_ownership(doc,book,numbers):
    """Bind a short native caption to its uniquely adjacent original artwork.

    Cached preparation may label an unnumbered plate title as a heading. Only
    the complete native text, its source type size and an unambiguous same-leaf
    geometric attachment can change that role. Runs and original pixels stay
    untouched, and real or unproved headings keep their authored boundary.
    """
    import statistics
    repairs=[]
    body_size=getattr(book.style,'body_size',0) if book.style else 0
    if not body_size:return repairs
    for pno in numbers:
        elements=book.pages.get(pno,[])
        if not any(e.kind=='fig' for e in elements):continue
        with unit_recovery.guard(pno,'native_caption_source'):
            physical=[dict(text=w[4],bbox=list(w[:4])) for w in doc[pno].get_text('words')]
            spans=[span for block in doc[pno].get_text('dict',flags=extract.pymupdf.TEXTFLAGS_TEXT)['blocks']
                   for line in block.get('lines',[]) for span in line['spans'] if span['text'].strip()]
        for index,element in enumerate(elements):
            if (element.kind not in ('p','h') or not element.bbox or not index
                    or elements[index-1].kind!='fig' or element.pno!=pno):continue
            with unit_recovery.guard(pno,'native_caption_ownership',block='native-element-'+str(index)):
                owned=_pixel_line_order(owned_words(physical,element.bbox))
                exact=lambda text:re.sub(r'\s+',' ',vision_reading.normalized(text)).strip()
                if not owned or exact(' '.join(w['text'] for w in owned))!=exact(element.text):continue
                rows=_pixel_lines(owned)
                if not 1<=len(rows)<=2 or len(owned)>10:continue
                box=vision_reading.union([w['bbox'] for w in owned])
                local_spans=[sp for sp in spans if all((sp['bbox'][i]>=element.bbox[i]-.5
                    if i<2 else sp['bbox'][i]<=element.bbox[i]+.5) for i in range(4))]
                if not local_spans or max(sp['size'] for sp in local_spans)>body_size*1.05:continue
                row_boxes=[vision_reading.union([w['bbox'] for w in row]) for row in rows]
                height=statistics.median(row[3]-row[1] for row in row_boxes)
                if height<=0:continue
                owners=[art for art in elements if art.kind=='fig' and art.pno==pno and art.bbox
                    and 0<=box[1]-art.bbox[3]<=2*height
                    and box[0]>=art.bbox[0] and box[2]<=art.bbox[2]
                    and abs((box[0]+box[2]-art.bbox[0]-art.bbox[2])/2)<=height]
                if len(owners)!=1 or owners[0] is not elements[index-1]:continue
                original_kind=element.kind;element.kind='caption';element.level=None
                repairs.append(dict(page=pno,block='native-element-'+str(index),kind_before=original_kind,
                    kind_after='caption',caption_bbox=box,figure_bbox=list(owners[0].bbox),
                    source_line_count=len(rows),source_font_size=max(sp['size'] for sp in local_spans),
                    body_font_size=body_size,source_text_proved=True,
                    method='unique_complete_native_caption_below_adjacent_original_artwork'))
    return repairs


def _native_seam_blocks(doc,book,pno,page):
    """Bind published native paragraphs to their own physical source rows."""
    import statistics
    elements=book.pages.get(pno,[])
    # Paint-object order can serialize a raised note label after its line.
    # Complete occurrence identity follows the printed spatial word order.
    stream=[{'text':w[4],'bbox':list(w[:4])}for w in doc[pno].get_text('words')]
    physical=_pixel_line_order(stream)
    # Cached native elements can retain a removed running title's box, or an
    # emitted glyph/span can make element-text equality unavailable. Bind the
    # complete emitted reading to unique source words before trusting that box.
    compact=lambda text:re.sub(r'\s+','',vision_reading.normalized(text))
    # Occurrence identity and cached element boxes use original PDF space.
    # Group there directly: rotating and derotating introduces float aliases
    # that can make one physical occurrence appear to be two distinct ones.
    from .native_text import lexical_word_view
    lexical=lexical_word_view(doc[pno],physical)if doc[pno].get_texttrace()else physical
    def complete_occurrences(text,element=None):
        target=compact(text)
        if not target:return []
        # The emitted native paragraph can join an original line-end division
        # and a drawn raised suffix. Match those exact physical components
        # before allowing a stale first-line rectangle to truncate its owner.
        views=[(physical,False),(_native_wrap_view(lexical,text.split()),False)]
        positions={i for i,item in enumerate(elements)if item is element}
        own_navigation=any((link['pno']==pno and link.get('source_element')in positions)or
                           (link['dest_page']==pno and link.get('dest_element')in positions)
                           for link in navigation)
        # A link on another element (including an incoming page link) cannot
        # hide these complete original cells. Keep the semantic-action guard
        # for every source/destination item belonging to this actual element.
        if (element is not None and not own_navigation
                and all(run[0]in('t','raised')for run in element.runs)):
            views.append((stream,True))
        found={}
        for words,components_only in views:
            source='';starts={};ends={}
            for index,word in enumerate(words):
                value=compact(word['text'])
                if not value:continue
                starts[len(source)]=index;source+=value;ends[len(source)]=index+1
            offset=source.find(target)
            while offset>=0:
                end=offset+len(target)
                if offset in starts and end in ends:
                    owned=words[starts[offset]:ends[end]]
                    if owned:
                        allowed=True
                        if components_only:
                            # Independent cells can serialize by column. The
                            # original stream still cannot move a raised atom
                            # behind words on its own row or borrow another
                            # paragraph's source component.
                            positions={id(word):i for i,word in enumerate(owned)}
                            allowed=not any(positions[id(left)]>positions[id(right)]
                                for row in _pixel_lines(owned)for left,right in zip(row,row[1:]))
                            allowed=allowed and bool(owned_words([owned[0]],element.bbox))
                            allowed=allowed and not any(other is not element and
                                other.kind in('p','h','caption')and other.bbox and
                                owned_words(owned,other.bbox)for other in elements)
                        if allowed:
                            components=tuple(sorted(tuple(part['bbox'])for word in owned
                                for part in word.get('native_parts',[word])))
                            bound=([dict(word,_native_component_bound=True)for word in owned]
                                   if components_only else owned)
                            found.setdefault(components,bound)
                offset=source.find(target,offset+1)
        return list(found.values())
    # A typed native note atom has a literal rendered symbol, whereas its
    # inventory uses a bracketed semantic token. Compare the actual factory
    # rendering, never strip brackets from source text or guess an owner.
    ambiguous=book.ambiguous_note_numbers(pno)
    available={str(n.num) for n in book.notes if n.pno==pno and n.num is not None}-ambiguous
    navigation=[link for link in book.source_navigation if link['status']=='resolved' and
                (link['pno']==pno or link['dest_page']==pno)]
    def published_identity(element,element_index):
        if element.kind!='list':
            if (element.kind in ('p','h') and any(r[0]=='sup'for r in element.runs)
                    and all(r[0]in ('t','raised','sup') for r in element.runs)
                    and all(r[0]!='sup' or str(r[1])in available and
                            not(len(r)>3 and r[3]=='uncertain') for r in element.runs)):
                return _seam_text(build_epub._nav_runs_html(element.runs,
                    build_epub._source_nav_marks(navigation,pno,element_index,None),
                    available,{},ambiguous))
            return re.sub(r'\s+',' ',element.text).strip()
        refs={}
        return _seam_text(''.join('<li>'+build_epub._nav_runs_html(r,
            build_epub._source_nav_marks(navigation,pno,element_index,item_index),
            available,refs,ambiguous)+'</li>'
            for item_index,r in enumerate(element.list_items)))
    rows=_pixel_row_ids(physical);result=[]
    for index,fragment in enumerate(page['body']):
        # Factory notices describe source evidence; they are not authored
        # paragraphs and cannot have a printed lexical occurrence. Keep their
        # body position, but never make their missing source box reject healthy
        # neighboring prose during the independent native pixel audit.
        role=('other' if build_epub._LEADING_NOTICE.match(fragment) or not _seam_text(fragment) else
              'heading' if re.match(r'<h[1-6](?:>|\s)',fragment) else
              'caption' if fragment.startswith('<p class="caption"') else
              'body' if build_epub._is_paragraph(fragment) or fragment.startswith('<ol class="source-list"') else 'other')
        text=_seam_text(fragment)
        # Equality identifies the actual emitted element, rather than guessing
        # its index after source warnings, figures and note controls are added.
        candidates=[e for element_index,e in enumerate(elements) if e.bbox and e.pno==pno and
                    published_identity(e,element_index)==text]
        record=dict(id='native-'+str(index),role=role,native_source=True,
                    fragment_sha256=hashlib.sha256(fragment.encode()).hexdigest(),continues_previous=False,continues_next=False,
                    source_regions=[],first_line_x=None,printed_head=None,printed_tail=None)
        element=candidates[0] if len(candidates)==1 else None
        repeated_source=None
        if len(candidates)>1:
            # Factory order disambiguates repeated labels only with equal
            # fragment/element counts and complete text inside the own box.
            # Global row-major text can interleave columns or even fabricate a
            # matching sequence from the tail of one cell and another's head.
            positions=[i for i,part in enumerate(page['body']) if _seam_text(part)==text]
            if len(positions)==len(candidates):
                proposed=candidates[positions.index(index)]
                local=_pixel_line_order(owned_words(physical,proposed.bbox))
                if compact(' '.join(w['text']for w in local))==compact(text):
                    element=proposed;repeated_source=local
        occurrences=complete_occurrences(text,element if len(candidates)==1 else None)
        owned=(repeated_source if repeated_source is not None else
               occurrences[0] if len(occurrences)==1 and len(candidates)<=1 else [])
        record['native_occurrence_count']=len(occurrences)
        binding=('factory_order_complete_local_source' if repeated_source is not None else
                 'unique_complete_source_text' if owned else 'native_element_box')
        if not owned and element is not None:
            owned=owned_words(physical,element.bbox)
            if len(occurrences)>1 and compact(' '.join(w['text']for w in owned))!=compact(text):
                # The literal exists, but a stale rectangle does not choose
                # between its repeated original occurrences. Keep its existing
                # unplaced-reading disclosure instead of borrowing header ink.
                owned=[]
        if owned:
            owned=_pixel_line_order(owned)
            box=(vision_reading.union([w['bbox'] for w in owned])
                 if binding=='unique_complete_source_text' else list(element.bbox))
            head,tail=_published_pixel_edges(text.split(),owned)
            line_groups=_pixel_lines(owned)
            model=getattr(book,'native_paragraph_source_evidence',{}).get(pno)
            structure=('table'if element is not None and element.table_row else
                next((_native_source_structural_row(vision_reading.union([w['bbox']for w in row]),model)
                    for row in line_groups if _native_source_structural_row(
                        vision_reading.union([w['bbox']for w in row]),model)),None))
            territories=([list(part['bbox'])for word in owned for part in word.get('native_parts',[word])]
                if any(word.get('_native_component_bound')for word in owned)else[box])
            record.update(bbox=box,source_regions=territories,native_geometry_binding=binding,
                native_source_structure=structure,
                cached_element_bbox=list(element.bbox) if element is not None else None,
                native_source_list=bool(element is not None and element.kind=='list'),
                native_source_list_regions=([list(map(list,boxes))for boxes in element.list_item_boxes] if element is not None and element.kind=='list' else []),
                native_list_continuation=bool(element is not None and getattr(element,'native_list_continuation',False)),
                native_list_continuation_margin=getattr(element,'native_list_continuation_margin',None),
                leading_source_space=bool(element is not None and element.runs and element.runs[0][0] in ('t','raised')
                                          and element.runs[0][1][:1].isspace()),
                native_page_foot=box[3]>doc[pno].rect.height*.7,
                native_page_top=box[1]<doc[pno].rect.height*.4,
                source_rows=[vision_reading.union([w['bbox'] for w in row]) for row in line_groups],
                first_line_x=min(w['bbox'][0] for w in line_groups[0]) if line_groups else None,
                opening_column_left=statistics.median(min(w['bbox'][0] for w in row) for row in line_groups[1:4]) if len(line_groups)>1 else None,
                printed_head=head['text'] if head is not None else None,
                printed_tail=tail['text'] if tail is not None else None,
                printed_head_box=head['bbox'] if head is not None else None,
                printed_tail_box=tail['bbox'] if tail is not None else None,
                printed_head_row=rows.get(id(head.get('_source_word',head))) if head is not None else None,
                printed_tail_row=rows.get(id(tail.get('_source_word',tail))) if tail is not None else None)
        result.append(record)
    return result


def _audit_native_pixels(doc,page,records,cache,package,images):
    """Inspect native live words against their own independent pixel crop."""
    from . import pixel_adjudication,vision_pixels
    display=SourceDisplay(doc,page['pno'])
    optical=None
    controller=unit_recovery.current()
    physical=[dict(text=w[4],bbox=list(display.reading_rect(w[:4])))
              for w in doc[page['pno']].get_text('words')]
    for position,record in enumerate(records):
        if record.get('role') not in ('body','heading','caption'):continue
        # The caller replaces blocked native units after auditing their healthy
        # siblings. Rechecking the same failed unit would escalate its page
        # before that bounded source replacement can be published.
        if controller and record['id'] in controller.blocked(page['pno']):continue
        territories=record.get('source_regions')
        unit=dict(page=page['pno'],block=record['id'],
                  fragment_sha256=hashlib.sha256(page['body'][position].encode()).hexdigest())
        with unit_recovery.guard(page['pno'],'native_pixel_adjudication',units=[unit]):
            if not territories:
                if 'native_occurrence_count' not in record or unit_recovery.current() is None:
                    raise ValueError('Native live text has no uniquely bound source occurrence')
                from .unit_source import source_unit
                reason=('Native reading cannot be audited at a unique printed occurrence; inspect the original source'
                    if record['native_occurrence_count'] else
                    'Native text has no printed occurrence; only its original source is published')
                fragment,asides,unit=source_unit(display,page['pno'],record['id'],record['role'],package,images,reason)
                if record['native_occurrence_count']:
                    # The complete literal inventory exists in the original,
                    # but it cannot earn a guessed block/crop identity. Keep
                    # that inventory with an explicitly unplaced page context.
                    original=page['body'][position]
                    tokens=_seam_text(original).split()
                    page['body'][position]=re.sub(r'(<(?:p|h[1-6])\b[^>]*>)(.*)(</(?:p|h[1-6])>)',
                        lambda m:m[1]+'<span class="uncertain" title="Unplaced native reading; inspect original source">'+m[2]+'</span>'+m[3],
                        original,flags=re.S)+fragment
                    record.update(published_tokens=tokens,marked=list(range(len(tokens))),
                        adjudication=[dict(source=[],model=[],chosen=[],status='UNRESOLVED',
                            method='unplaced_native_reading',mapping='unplaced_within_span')])
                else:
                    page['body'][position]=fragment
                    record['published_tokens']=[]
                page['asides'].extend(asides)
                record.update(degraded_source_context=True,unplaced_source_context=unit,
                    pixel_adjudication_status='UNPLACED_SOURCE_CONTEXT')
                continue
            # Proved original note rows need exact word ownership. The model-box
            # rounding allowance can otherwise borrow a nearby raised label.
            tolerance=.2 if record.get('native_exact_territories')else 3
            source=_pixel_line_order([w for w in physical if any(
                box[0]-tolerance <= (w['bbox'][0]+w['bbox'][2])/2 <= box[2]+tolerance and
                box[1]-tolerance <= (w['bbox'][1]+w['bbox'][3])/2 <= box[3]+tolerance
                for box in territories)])
            source=_native_lexical_view(display,source)
            record['native_source_occurrences']=copy.deepcopy(source)
            tokens=_seam_text(page['body'][position]).split()
            record['published_tokens']=tokens
            # A scan-to-vector producer authors its Unicode map by recognition.
            # Repeating a suspicious OCR spelling is not independent letter
            # identity, even though those visible vectors are original paint.
            metadata=doc.metadata or {}
            optical_map=bool(re.search(r'ClearScan|Paper Capture|ABBYY|OCRmyPDF',
                str(metadata.get('producer',''))+' '+str(metadata.get('creator','')),re.I))
            lexical_doubts=pixel_adjudication.optical_map_doubts(tokens)if optical_map else{}
            from .native_text import drawn_word_authority,drawn_word_authorities
            original=[dict(w,bbox=list(display.source_rect(w['bbox'])*display.page.derotation_matrix),
                **({'native_parts':[dict(p,bbox=list(display.source_rect(p['bbox'])*display.page.derotation_matrix))for p in w['native_parts']]}if 'native_parts'in w else{}))for w in source]
            if drawn_word_authority(doc[page['pno']],original) and not lexical_doubts:
                literal=lambda values:''.join(vision_reading.normalized(t)for t in values)
                native_view=_native_wrap_view(source,tokens)
                from collections import Counter
                covered=Counter(vision_reading.normalized(w['text'])for w in native_view)
                atoms=lambda values:re.findall(r'\w+|[^\w\s]',
                    ' '.join(vision_reading.normalized(t)for t in values))
                if (covered==Counter(vision_reading.normalized(t)for t in tokens)
                        or atoms(w['text']for w in native_view)==atoms(tokens)
                        or re.search(r'<(?:a|sup)\b[^>]*class="noteref(?:-unresolved)?"',page['body'][position])
                        and literal(w['text']for w in native_view)==literal(tokens)):
                    record['pixel_adjudication_status']='VISIBLE_NATIVE_PRINT'
                    continue
                # Authority belongs to the covered characters, never to extra
                # factory runs outside that territory. A detached legacy run
                # may retain its ink only at a unique visible original glyph;
                # its placement in this factory paragraph remains unproved.
                from difflib import SequenceMatcher
                from .native_text import unproved_legacy_word,visible_word_ink
                mapped={};unbound=[]
                for kind,a,z,b,y in SequenceMatcher(None,
                        [w['text']for w in source],tokens,autojunk=False).get_opcodes():
                    if kind=='equal':mapped.update({b+i:source[a+i]for i in range(z-a)})
                    elif kind=='insert':unbound.extend(range(b,y))
                    else:raise ValueError('Native factory output has no complete printed binding')
                used={tuple(w['bbox'])for w in source};proof=[]
                for index in unbound:
                    candidates=[]
                    for word in physical:
                        if word['text']!=tokens[index]:continue
                        raw=dict(word,bbox=list(display.source_rect(word['bbox'])*display.page.derotation_matrix))
                        if unproved_legacy_word(display.page,raw)and visible_word_ink(display.page,[raw])[0]:
                            candidates.append(word)
                    if len(candidates)!=1 or tuple(candidates[0]['bbox'])in used:
                        raise ValueError('Native factory output has no complete printed binding')
                    word=candidates[0];mapped[index]=word;used.add(tuple(word['bbox']))
                    proof.append(dict(index=index,source_bbox=list(word['bbox']),
                        method='unique_visible_legacy_glyph',mapping='unplaced_native_factory_run'))
                if not unbound or len(mapped)!=len(tokens):
                    raise ValueError('Native factory output has no complete printed binding')
                source=[mapped[i]for i in range(len(tokens))]
                original=[dict(w,bbox=list(display.source_rect(w['bbox'])*display.page.derotation_matrix))for w in source]
                territories=list(territories)+[item['source_bbox']for item in proof]
                record.update(source_regions=territories,unplaced_native_factory_glyphs=proof,
                    pixel_adjudication_status='UNPLACED_NATIVE_FACTORY_GLYPHS')
            witness=pixel_adjudication.read_crop(display,territories,Path(cache)/'adjudication')
            from .native_text import unproved_legacy_word
            legacy={i for i,w in enumerate(original)if unproved_legacy_word(doc[page['pno']],w)}
            reliable=drawn_word_authorities(doc[page['pno']],original)
            output_sources={i:w for i,w in enumerate(source)}
            if [w['text'] for w in source]!=tokens:
                # The factory may join line wraps or preserve inline actions.
                # Bind unchanged occurrences through that transform instead of
                # treating one unreliable glyph as a failure of all its peers.
                from difflib import SequenceMatcher
                mapping={};joined_sources={}
                for kind,a,z,b,y in SequenceMatcher(None,
                        [w['text']for w in source],tokens,autojunk=False).get_opcodes():
                    if kind=='equal':
                        mapping.update({a+i:b+i for i in range(z-a)})
                    elif (kind=='replace' and y-b==1 and z>a and
                            ''.join(w['text']for w in source[a:z])==tokens[b]):
                        # Native inline markup can join an expression across a
                        # wrap. Exact unchanged letters bind its original parts
                        # to one output word; no neighboring prose is rejected.
                        parts=[part for w in source[a:z]for part in w.get('native_parts',[w])]
                        joined_sources[b]=dict(text=tokens[b],
                            bbox=vision_reading.union([part['bbox']for part in parts]),
                            native_parts=parts)
                        mapping.update({i:b for i in range(a,z)})
                conflicts=pixel_adjudication.audit_agreement([w['text']for w in source],source,witness)
                for i in legacy:
                    conflicts[i]=dict(source=[source[i]['text']],model=[source[i]['text']],
                        chosen=[source[i]['text']],status='UNRESOLVED',method='unproved_native_character_map')
                conflicts={i:d for i,d in conflicts.items()if not reliable[i]}
                if any(i not in mapping for i in conflicts):
                    raise ValueError('Unreliable native occurrence has no unique published word binding')
                output_sources={mapping[i]:source[i]for i in mapping}
                output_sources.update(joined_sources)
                conflicts={mapping[i]:d for i,d in conflicts.items()}
                for i in conflicts.keys() & joined_sources.keys():
                    conflicts[i]=dict(source=[tokens[i]],model=[tokens[i]],chosen=[tokens[i]],
                        status='UNRESOLVED',method='unproved_native_character_map')
                legacy={mapping[i]for i in legacy if i in mapping}
                record['pixel_adjudication_status']='SOURCE_OCCURRENCES_CHECKED_BEFORE_NATIVE_TRANSFORM'
            else:
                conflicts=None
            if conflicts is None:
                refined=pixel_adjudication.refine_fallback(tokens,witness,text_quality.english_lexicon(),source=source)
                if (refined and any(d['status']=='UNRESOLVED' and
                        len(d['source'])==len(d['chosen'])==1 for d in refined['decisions'])
                        and any(w.get('confidence',0)>=90 for w in witness)):
                    # Unknown spellings need a second pixel view at this same
                    # occurrence. Cache the independent page segmentation once;
                    # a dictionary preference cannot stand in for visible ink.
                    if optical is None:optical=vision_pixels.words(display,cache)
                    corroboration=[w for w in optical if any(owned_words([w],box) for box in territories)]
                    refined=pixel_adjudication.refine_fallback(tokens,witness,
                        text_quality.english_lexicon(),source=source,corroboration=corroboration)
                # Native fragments retain their structure and word inventory. Only
                # one-for-one, independently corroborated spelling edits fit here.
                repairs={i:d for i,d in (refined or {}).get('word_notes',{}).items()
                         if d['status']=='RESOLVED' and len(d['source'])==len(d['chosen'])==1}
                if refined is None or len(refined['tokens'])!=len(tokens) or any(
                        len(d['source'])!=len(d['chosen']) for d in refined['decisions']):repairs={}
                conflicts=pixel_adjudication.audit_agreement(tokens,source,witness)
                conflicts.update(repairs)
                reliable=drawn_word_authorities(doc[page['pno']],original)
                conflicts={i:d for i,d in conflicts.items()if not reliable[i]}
                for i in legacy:
                    conflicts[i]=dict(source=[source[i]['text']],model=[source[i]['text']],
                        chosen=[source[i]['text']],status='UNRESOLVED',method='unproved_native_character_map')
            # An independent complete repair can resolve the spelling. Equal
            # suspect readings merely repeat the optically authored map.
            for i,decision in lexical_doubts.items():
                if not(conflicts.get(i,{}).get('status')=='RESOLVED' and
                       conflicts[i].get('chosen')!=decision['chosen']):
                    conflicts[i]=decision
            record['adjudication']=list(conflicts.values())
            if not conflicts:continue
            fragment=page['body'][position]
            ranges=pixel_adjudication.fragment_word_ranges(fragment,tokens)
            if ranges is None:
                raise ValueError('Native pixel conflict has no unique published occurrence')
            # A verified typed reference is not a lexical suffix. OCR may
            # recognize its raised number as punctuation, but cannot rewrite
            # that separately owned action through a prose-word correction.
            for index,decision in list(conflicts.items()):
                a,z=ranges[index];raw=fragment[a:z]
                action=re.search(r'<a\b[^>]*class="noteref"[^>]*>.*?</a>',raw,re.S)
                alternative=decision.get('alternative') or decision.get('chosen') or []
                if action and len(alternative)==1 and index not in legacy:
                    prefix=_seam_text(raw[:action.start()])
                    suffix=_seam_text(raw[action.end():])
                    value=alternative[0]
                    tail=value[len(prefix):] if prefix and value.startswith(prefix) else None
                    if (tail is not None and not any(c.isalpha()for c in tail) and
                            not any(c.isalnum()for c in suffix)):
                        del conflicts[index]
            record['adjudication']=list(conflicts.values())
            if not conflicts:continue
            edits=[]
            for index,decision in conflicts.items():
                a,z=ranges[index]
                from . import reading_tiers
                reading_tiers.qualify_resolution(decision)
                formatted=None
                if decision['status']=='RESOLVED' and '<' in fragment[a:z]:
                    formatted=pixel_adjudication.replace_formatted_word(fragment[a:z],decision['chosen'][0])
                if decision['status']=='RESOLVED' and '<' in fragment[a:z] and formatted is None:
                    # Keep ordinal formatting and typed reference actions. A
                    # lexical replacement cannot delete their inline markup;
                    # retain the authored word with an owned unresolved note.
                    decision.update(alternative=decision['chosen'],chosen=[tokens[index]],
                        status='UNRESOLVED',method='formatted_native_word_requires_typed_edit')
                ident=f"v4-native-d-{page['pno']}-{record['id']}-{index}";origin='r-'+ident
                src='images/'+ident+'.jpg';box=output_sources[index]['bbox']
                retained_action='';label_box=None
                if index in legacy:
                    action=re.search(r'<a\b[^>]*class="noteref"[^>]*>.*?</a>',fragment[a:z],re.S)
                    if action:
                        from .native_text import word_prefix_ink
                        physical_word=dict(output_sources[index],bbox=list(display.source_rect(box)*display.page.derotation_matrix))
                        cut=word_prefix_ink(doc[page['pno']],physical_word,_seam_text(action[0]))
                        if cut is None:
                            raise ValueError('Unmapped glyph cannot be separated from its proved note action')
                        box=list(display.reading_rect(cut[0]));label_box=display.reading_rect(cut[1])
                        retained_action=action[0]
                clip=extract.pymupdf.Rect(box);clip.x0-=2;clip.y0-=2;clip.x1+=2;clip.y1+=2;clip &= display.rect
                if label_box is not None:
                    if box[2]<=label_box.x0+1e-3:clip.x1=min(clip.x1,label_box.x0)
                    elif box[0]>=label_box.x1-1e-3:clip.x0=max(clip.x0,label_box.x1)
                    elif box[3]<=label_box.y0+1e-3:clip.y1=min(clip.y1,label_box.y0)
                    elif box[1]>=label_box.y1-1e-3:clip.y0=max(clip.y0,label_box.y1)
                    else:raise ValueError('Unmapped glyph overlaps its proved note action')
                _word_disclosure_image(package,src,display,clip,output_sources[index].get('native_parts'))
                images[src]=dict(page=page['pno'],bbox=list(clip),kind='vision_disclosure',
                    **({'source_parts':copy.deepcopy(output_sources[index]['native_parts'])}
                       if output_sources[index].get('native_parts')else {}))
                label='Pixel-verified correction' if decision['status']=='RESOLVED' else 'Unresolved source reading'
                chosen=decision['chosen'][0] if decision['status']=='RESOLVED' else tokens[index]
                explanation=(escape(tokens[index])+' → '+escape(chosen)+'.' if chosen!=tokens[index]
                             else escape(chosen)+'. Consult the original printed word in the image.')
                explanation+=' An independent upright crop supports this correction.' if decision['status']=='RESOLVED' else ' An independent upright crop could not settle this difference.'
                page['asides'].append(f'<aside id="{ident}" class="footnote vision-disclosure" data-reflow-owner-page="{page["pno"]}" data-reflow-owner-block="{escape(record["id"],quote=True)}" epub:type="footnote"><p>{label}: {explanation}</p><img src="{src}" alt="Original printed reading"/><a href="#{origin}">Return</a></aside>')
                from .native_text import unproved_legacy_word
                legacy_ink=(decision['status']=='UNRESOLVED' and
                            index in legacy)
                if legacy_ink:
                    edits.append((a,z,f'<a class="source-word-image" href="#{ident}"><img src="{src}" alt="Unmapped printed glyph; inspect original"/></a>'+retained_action))
                    decision['source_image_only']=True
                elif decision['status']=='UNRESOLVED':
                    from . import reading_tiers,inline_print
                    choice=reading_tiers.choose(tokens[index],decision,rank_lexical=False)
                    decision['publication']=choice
                    if choice['tier']=='flagged':
                        chosen=choice['reading'];inline_src=src
                        style='height:1.4em;vertical-align:-0.25em;width:auto'
                        preview=(inline_print.preview(display,box)
                            if not output_sources[index].get('native_parts')else None)
                        if preview is not None and hasattr(package,'image'):
                            data,metrics=preview;inline_src=f'images/{ident}-inline.png'
                            package.image(inline_src,data)
                            images[inline_src]=dict(page=page['pno'],kind='inline_print_preview',coverage_eligible=False,
                                inspection_image=src,**metrics)
                            style=metrics['style']
                        replacement=reading_tiers.flagged_html(chosen,inline_src,ident,style)
                        edits.append((a,z,replacement))
                        record.setdefault('flagged_readings',{})[str(index)]=choice
                    elif choice['tier']=='print':
                        edits.append((a,z,f'<a class="source-word-image" href="#{ident}"><img src="{src}" alt="Unresolved printed word; inspect original"/></a>'))
                        decision['source_image_only']=True
                elif chosen!=tokens[index]:edits.append((a,z,formatted if formatted is not None else escape(chosen)))
                # Add the control after any enclosing inline action closes;
                # never nest a new link inside a typed source reference.
                closing=re.match(r'(?:</(?:sup|sub|em|strong|i|b|a|span)\s*>)+',fragment[z:],re.I)
                at=z+(closing.end() if closing else 0)
                edits.append((at,at,f'<a id="{origin}" class="source-disclosure-ref" epub:type="noteref" href="#{ident}"><sup>†</sup></a>'))
                tokens[index]=chosen
            for a,z,value in sorted(edits,reverse=True):fragment=fragment[:a]+value+fragment[z:]
            fragment,unit_print_html=_present_flagged_prints(fragment,display,page['pno'],record['id'],
                record.get('source_regions') or [record['bbox']],package,images,page['asides'],
                evidence_words=optical or physical,heading=record.get('role')=='heading')
            page['body'][position]=fragment+unit_print_html
            record['marked']=sorted(set(record.get('marked',[]))|set(conflicts))
            record['source_image_words']=sorted(set(record.get('source_image_words',[]))|
                {i for i,d in conflicts.items()if d.get('source_image_only')})


def _audit_native_note_pixels(doc,book,page,cache,package,images):
    """Audit native note text separately from its label and generated controls."""
    from . import pixel_adjudication
    records=[];display=SourceDisplay(doc,page['pno']);additions=[]
    physical=[dict(text=w[4],bbox=list(w[:4]))
              for w in doc[page['pno']].get_text('words')]
    controller=unit_recovery.current()
    for ordinal,note in enumerate(n for n in book.notes if n.pno==page['pno']):
        ident='native-note-'+str(ordinal)
        if controller and ident in controller.blocked(page['pno']):continue
        if note.uses_source_image and not any(run[0]=='t' and run[1].strip() for run in note.glyph_runs):continue
        with unit_recovery.guard(page['pno'],'native_note_pixel_adjudication',block=ident):
            if note.uses_source_image:
                raise ValueError('Mixed native glyph and live-text note requires original source disclosure')
            literal=escape(note.text,quote=False)
            if not literal.strip():continue
            target='fn_p%04d_%s'%(page['pno'],note.num) if note.num is not None else None
            candidates=[]
            for index,fragment in enumerate(page['asides']):
                if target is not None and 'id="'+target+'"' not in fragment:continue
                tokens=_seam_text(fragment).split()
                ranges=pixel_adjudication.fragment_word_ranges(fragment,tokens)
                if ranges is None:continue
                for start in pixel_adjudication._matches(tokens,note.text.split()):
                    if note.num is not None and start==0:continue
                    a,z=ranges[start][0],ranges[start+len(note.text.split())-1][1]
                    if fragment[a:z]==literal:candidates.append((index,a,z))
            if len(candidates)!=1:
                raise ValueError('Native note has no unique emitted lexical fragment')
            source_box=extract.pymupdf.Rect(note.bbox)
            words=(_pixel_line_order(owned_words(physical,source_box))
                   if not source_box.is_empty else [])
            # Label order belongs to original source coordinates, before a PDF
            # display rotation can reverse its position beside the lexical copy.
            # A box already containing precisely the body must not lose its own
            # first word merely because that word equals the note number.
            local=vision_reading.normalized(' '.join(w['text']for w in words)).split()
            printed_label=None
            if (note.num is not None and words and local!=vision_reading.normalized(note.text).split()
                    and vision_reading.normalized(words[0]['text']).strip('.,:;()[]{}')==str(note.num)):
                printed_label=dict(text=words[0]['text'],bbox=list(display.reading_rect(words[0]['bbox'])),target=target)
                words=words[1:]
            territories=[list(display.reading_rect(vision_reading.union([w['bbox']for w in row])))
                         for row in _pixel_lines(words)]
            # A cached rectangle can retain only the first line of a continued
            # note. Complete unique original text proves its full footprint even
            # when that stale rectangle is nonempty. Ambiguous text still needs
            # the note's own bounded territory (including label separation).
            bound=_native_seam_blocks(doc,book,page['pno'],dict(body=['<p>'+literal+'</p>']))[0]
            if bound.get('native_geometry_binding')=='unique_complete_source_text' or not territories:
                territories=[list(display.reading_rect(box))for box in
                             (bound.get('source_rows')or bound.get('source_regions',[]))]
            if not territories:raise ValueError('Native note has no original word territory')
            fragment='<p><span data-source-page="%d">%s</span></p>'%(page['pno'],literal)
            proxy=dict(pno=page['pno'],body=[fragment],asides=[])
            record=dict(id=ident,role='body',native_source=True,source_regions=territories,
                        native_exact_territories=True)
            _native_published_provenance(proxy,[record])
            _audit_native_pixels(doc,proxy,[record],cache,package,images)
            # Presentation can append this note's generated print after its
            # lexical paragraph. Keep that evidence inside the footnote while
            # still rejecting any other change to the paragraph container.
            supplements=_defer_unit_prints([proxy])
            if any(owner!=(str(page['pno']),ident)for owner,_ in supplements):
                raise ValueError('Native note print changed its source owner')
            inner=re.fullmatch(r'<p>(.*)</p>',proxy['body'][0],re.S)
            if inner is None:raise ValueError('Native note audit changed its lexical container')
            index,a,z=candidates[0]
            original=page['asides'][index]
            replacement=original[:a]+inner[1]+original[z:]
            if supplements:
                if not replacement.endswith('</aside>'):
                    raise ValueError('Native note print lost its footnote container')
                replacement=replacement[:-len('</aside>')]+''.join(html for _,html in supplements)+'</aside>'
            page['asides'][index]=replacement
            record.update(role='footnote',lane='asides',source_aside_index=index,printed_label_occurrence=printed_label)
            records.append(record);additions.extend(proxy['asides'])
    page['asides'].extend(additions)
    return records


def _native_published_provenance(page,records):
    """Publish only the native fragment's already proved source identity.

    Text and markup stay intact. Source images and unbound fragments receive
    no invented live-text owner. The registered fragment signature includes
    these attributes, so a later recovery must still replace that exact unit.
    """
    for index,record in enumerate(records):
        regions=record.get('source_regions')
        if (record.get('role') not in ('body','heading','caption') or
                record.get('degraded_source_context') or not regions):continue
        fragment=page['body'][index]
        if record.get('native_source_list'):
            items=list(re.finditer(r'<li(?:\s[^>]*)?>(.*?)</li>',fragment,re.S))
            boxes=record.get('native_source_list_regions') or []
            if len(items)!=len(boxes) or any(not value for value in boxes):
                raise ValueError('Original native list item source territories differ')
            for item,territories in reversed(list(zip(items,boxes))):
                attrs=(f'<span data-source-page="{page["pno"]}" '
                       f'data-source-block="{escape(record["id"],quote=True)}" '
                       f'data-source-regions="{escape(json.dumps(territories),quote=True)}">')
                fragment=fragment[:item.start(1)]+attrs+item[1]+'</span>'+fragment[item.end(1):]
            page['body'][index]=fragment
            record['fragment_sha256']=hashlib.sha256(fragment.encode()).hexdigest()
            continue
        opening=f'<span data-source-page="{page["pno"]}">'
        if opening not in fragment:continue
        attrs=(f'<span data-source-page="{page["pno"]}" '
               f'data-source-block="{escape(record["id"],quote=True)}" '
               f'data-source-regions="{escape(json.dumps(regions),quote=True)}">')
        fragment=fragment.replace(opening,attrs,1)
        page['body'][index]=fragment
        record['fragment_sha256']=hashlib.sha256(fragment.encode()).hexdigest()


def _art_source_box(block,neighbours):
    """Locate a source-context control in flow; never change its raster extent."""
    printed=block.get('source_complete_caption_bbox')
    if block['role']=='caption' and printed:return printed
    if block['role'] in ('figure','table') and block.get('degraded_source_context'):
        position=next((i for i,b in enumerate(neighbours) if b['id']==block['id']),None)
        box=block['bbox']
        if position is not None:
            for caption in neighbours[position+1:]:
                if caption['role'] in ('running_head','folio'):continue
                if caption['role']!='caption':break
                proven=caption.get('source_complete_caption_bbox')
                if (proven and proven[0]<box[2] and box[0]<proven[2]
                        and box[1]<=proven[1] and proven[3]<=box[3]+3):
                    return [box[0],box[1],box[2],min(box[3],proven[3])]
    gap=block.get('source_art_gap')
    if block['role']=='caption' and gap:
        owner=next((b for b in neighbours if b['id']==gap['object'] and b['role'] in ('figure','table')),None)
        if owner and all(abs(a-b)<1e-6 for a,b in zip(owner['bbox'],gap['bbox'])):
            return _art_source_box(owner,neighbours)
    return block['bbox']


def _art_precedes_source_opening(block,neighbours,source_head,territory):
    box=_art_source_box(block,neighbours)
    return (box[2]<=territory[0]-3 or
            box[3]<=source_head[1]+3 and
            box[2]>=territory[0]-3 and box[0]<=territory[2]+3)


def _source_note_on_earlier_leaf(block,territory):
    regions=block.get('source_regions') or []
    return (block.get('role')=='footnote' and bool(regions) and
            all(box[2]<=territory[0]-3 for box in regions))


def _source_row_continuation(left,right,tail,head):
    tail=build_epub.block_text(tail);head=build_epub.block_text(head)
    lexical=lambda word:vision_reading.normalized(word).casefold()
    return (left.get('role')==right.get('role')=='body' and tail and head
            and _narrow_row_edge(left.get('printed_tail_box'),right.get('printed_head_box'),
                                 left.get('printed_tail_row'),right.get('printed_head_row'))
            and lexical(tail.split()[-1])==lexical(left.get('printed_tail') or '')
            and lexical(head.split()[0])==lexical(right.get('printed_head') or '')
            and assemble.continues(tail,head))


def _floating_art(blocks):
    """A captioned, original-ink-bound table can float like an illustration."""
    if not any(b['role'] in ('figure','table') for b in blocks):return False
    if any(b['role'] not in ('figure','table','caption') for b in blocks):return False
    for i,b in enumerate(blocks):
        if b['role']=='table' and not b.get('degraded_source_context') and (
                not b.get('source_bound_art') or i+1==len(blocks)
                or blocks[i+1]['role']!='caption'):return False
    return True


def _leading_art_continuation(previous,current,before,after,tail,blocks):
    """Move only an exact source-proven sentence opening ahead of floating art."""
    if not before or before[-1]['role']!='body' or not tail.startswith('<p>'):return None
    opening=next((i for i,b in enumerate(after) if b['role']=='body'),None)
    if not opening or opening>=len(current['body']):return None
    leading=after[:opening];right=after[opening];left=before[-1]
    # A disclosed or unbound body is a structural barrier, including when
    # another readable body follows it. Never reorder across that barrier.
    if any(b.get('degraded_source_context') or not b.get('source_regions') for b in (left,right)):return None
    if not _floating_art(leading):return None
    source_position=next(i for i,b in enumerate(blocks) if b['id']==right['id'])
    territory=right['source_regions'][0]
    if any(b['role'] not in ('running_head','folio','figure','table','caption')
           and not _source_note_on_earlier_leaf(b,territory)
           for b in blocks[:source_position]):return None
    head=current['body'][opening]
    if not head.startswith('<p>'):return None
    if any(not fragment.startswith(('<figure','<p class="caption">'))
           for fragment in current['body'][:opening]):return None
    division=_completed_division(left,right,tail,head)
    source_head=division['source_head']['bbox'] if division else right.get('printed_head_box')
    source_tail=division['source_tail']['bbox'] if division else left.get('printed_tail_box')
    if source_head is None or source_tail is None:return None
    tail_text=build_epub.block_text(tail);head_text=build_epub.block_text(head)
    lexical=lambda word:vision_reading.normalized(word).casefold()
    if not division:
        if not tail_text or not head_text or not left.get('printed_tail') or not right.get('printed_head'):return None
        if (lexical(tail_text.split()[-1])!=lexical(left['printed_tail']) or
            lexical(head_text.split()[0])!=lexical(right['printed_head'])):return None
    territory=right['source_regions'][0]
    if any(not _art_precedes_source_opening(b,leading,source_head,territory)
           for b in leading):return None
    # A footer after the prior source paragraph may stay on its printed page;
    # an in-flow note or other structural block remains a barrier.
    prior=previous['blocks'];position=next(i for i,b in enumerate(prior) if b['id']==left['id'])
    edge=left['source_regions'][-1]
    if any(b['role'] not in ('running_head','folio') and
           not (b['role']=='footnote' and b['bbox'][1]>=edge[3]-3 and
                b['bbox'][0]<edge[2] and b['bbox'][2]>edge[0])
           for b in prior[position+1:]):return None
    # Match the published opening itself: a sloped optical row can otherwise
    # identify a later word as its first line, despite an unindented opening.
    proven=dict(right,first_line_x=source_head[0])
    if not _source_continuation(left,proven,blocks,tail,head):return None
    current['body'].insert(0,current['body'].pop(opening))
    return proven


def _join_scanned_regions(page,blocks,words=(),compounds=()):
    """Join adjacent body fragments only across an observed column/leaf edge."""
    unit_prints=_defer_unit_prints([page])
    emitted=[b for b in blocks if b['role'] not in ('running_head','folio','footnote')]
    # In-flow notes are structural barriers; footer geometry distinguishes notes
    # that are outside a paragraph continuing onto the next scanned leaf.
    positions={b['id']:i for i,b in enumerate(blocks)}
    def outside_flow(item,left,right):
        if item['role'] in ('running_head','folio'):return True
        box=item['bbox']
        return (item['role']=='footnote' and box[1]>=left[3]-3
                and box[0]<left[2] and box[2]<=right[0]+3
                and all(box[1]>=_art_source_box(art,between)[3]-3
                        for art in between if art['role'] in ('figure','table','caption')
                        and _art_source_box(art,between)[2]<=right[0]-3
                        and _art_source_box(art,between)[1]>=left[3]-3))
    result=[];previous=None;last_body=None;last_body_index=None
    for fragment,block in zip(page['body'],emitted):
        if block.get('degraded_source_context') and block['role'] not in ('figure','table'):
            result.append(fragment)
            previous=last_body=None;last_body_index=None
            continue
        join=False
        if previous and previous['role']==block['role']=='body':
            left=previous['source_regions'][-1];right=block['source_regions'][0]
            edge=right[0]>left[2]+3 and right[1]<left[1]
            between=blocks[positions[previous['id']]+1:positions[block['id']]]
            adjacent=all(outside_flow(item,left,right) for item in between)
            recovered_tail=any(g.get('kind')=='omitted_pixel_line_recovery' for g in block.get('guard',[]))
            explicit=previous['continues_next'] and block['continues_previous']
            # Source geometry and the native lexical rule can recover a missed
            # model flag. A genuinely indented first line remains a new paragraph.
            inferred=_source_continuation(previous,block,blocks,result[last_body_index],fragment)
            row_continuation=_source_row_continuation(previous,block,result[last_body_index],fragment)
            same_column=right[0]<left[2] and left[0]<right[2] and right[1]>=left[3]-3
            join=adjacent and (row_continuation or ((edge or same_column) and inferred))
        if join:
            result[last_body_index],fragment=_repair_completed_division(previous,block,blocks,result[last_body_index],fragment)
            hyphen=_seam_hyphen(result[last_body_index],fragment,words,compounds)
            result[last_body_index]=build_epub._merge_layout_paragraphs(result[last_body_index],fragment,'',hyphen)
        else:
            floating=False
            if last_body and block['role']=='body':
                left=last_body['source_regions'][-1];right=block['source_regions'][0]
                between=blocks[positions[last_body['id']]+1:positions[block['id']]]
                tail=build_epub.block_text(result[last_body_index]);head=build_epub.block_text(fragment)
                # An unfinished sentence can surround a right-leaf figure.
                # Preserve its figure and caption as separate following blocks.
                # A damaged, unmatched opening stays uncertain; geometry proves
                # its placement without replacing the published word.
                source_edge=(tail and head and last_body.get('printed_tail')
                    and vision_reading.normalized(tail.split()[-1])==vision_reading.normalized(last_body['printed_tail'])
                    and (block.get('printed_head') is None or
                         vision_reading.normalized(head.split()[0])==vision_reading.normalized(block['printed_head'])))
                division=_completed_division(last_body,block,result[last_body_index],fragment)
                source_edge=source_edge or bool(division)
                cross_column=right[0]>left[2]+3 and right[1]<left[1]
                same_column=(right[0]<left[2] and left[0]<right[2] and right[1]>left[3])
                def caption_beside_tail(item):
                    if item['role']!='caption' or not item.get('printed_head') or not item.get('printed_tail'):return False
                    rows=last_body.get('source_rows') or [last_body.get('printed_tail_box')]
                    caption=item['bbox'];rows=[row for row in rows if row is not None]
                    overlapping=[row for row in rows if row[1]<caption[3]+3 and caption[1]<row[3]+3]
                    separate=bool(overlapping) and all(caption[0]>=row[2]+3 or caption[2]<=row[0]-3 for row in overlapping)
                    # These endpoints belong to the separate caption instances,
                    # after all restored BODY words have been reserved.
                    return separate
                # Rows in an inset new paragraph can all share the same
                # indent. Compare its opening also with independently printed
                # BODY territories in this leaf, not only its own later rows.
                leaf_margin=min((region[0] for peer in blocks if peer['role']=='body'
                    for region in peer.get('source_regions',[])
                    if region[0]>left[2]+3 and region[0]<right[2] and region[2]>right[0]),
                    default=right[0])
                earlier_leaf_opening=(block.get('first_line_x') is not None
                    and block['first_line_x']<=leaf_margin+3)
                art_above=_floating_art([b for b in between if not outside_flow(b,left,right)]) and all(outside_flow(b,left,right) or
                    (b['role'] in ('figure','table','caption') and
                     ((cross_column and earlier_leaf_opening and _art_source_box(b,between)[2]<=right[0]-3
                       and _art_source_box(b,between)[1]>=left[3]-3
                       and (b.get('source_bound_art') or b.get('degraded_source_context')
                            or b.get('source_complete_caption_bbox'))) or
                      (_art_source_box(b,between)[3]<=right[1]+3 and
                       ((cross_column and _art_source_box(b,between)[0]>left[2]+3) or
                        (same_column and (_art_source_box(b,between)[1]>=left[3]-3 or
                          b.get('degraded_source_context') and b.get('source_bound_art') or
                          caption_beside_tail(b))))))) for b in between)
                # A same-column float needs both exact printed endpoints. The
                # earlier damaged right-leaf opening remains explicitly uncertain.
                if same_column:source_edge=source_edge and (block.get('printed_head') is not None or division)
                if any(b['role']=='table' for b in between):
                    source_edge=source_edge and (block.get('printed_head') is not None or division)
                floating=(source_edge and art_above and (cross_column or same_column)
                          and _source_continuation(last_body,block,blocks,result[last_body_index],fragment))
            if floating:
                result[last_body_index],fragment=_repair_completed_division(last_body,block,blocks,result[last_body_index],fragment)
                hyphen=_seam_hyphen(result[last_body_index],fragment,words,compounds)
                result[last_body_index]=build_epub._merge_layout_paragraphs(result[last_body_index],fragment,'',hyphen)
            else:result.append(fragment)
        if block['role']=='body':
            last_body=block
            if not join and not floating:last_body_index=len(result)-1
        previous=block
    page['body']=result
    _restore_unit_prints([page],unit_prints)


def _proved_recovery_columns(words):
    """Partition only persistent empty gutters between multi-row ink columns.

    Word territories, rather than OCR sequence or a paragraph guess, prove the
    cut. Full-width ink closes a gutter, and a short isolated mark cannot create
    a column. Ambiguous geometry retains the original recovery territory.
    """
    import statistics
    if not words:return [words],False
    height=max(1,statistics.median(w['bbox'][3]-w['bbox'][1] for w in words))
    intervals=[]
    for left,right in sorted((w['bbox'][0],w['bbox'][2]) for w in words):
        if intervals and left<=intervals[-1][1]:
            intervals[-1][1]=max(intervals[-1][1],right)
        else:intervals.append([left,right])
    gaps=[b[0]-a[1] for a,b in zip(intervals,intervals[1:])]
    # A gutter must exceed ordinary persistent inter-word spacing as well as
    # the type height. Repeated word slots alone do not prove text columns.
    # The sole persistent gap can itself be the gutter. Including it in the
    # estimate of ordinary word spacing makes a two-column proof impossible.
    ordinary=[gap for gap in gaps if gap<1.5*height]
    minimum=max(6,1.5*height,2*statistics.median(ordinary)) if ordinary else max(6,1.5*height)
    widths=sorted(set(gap for gap in gaps if gap>=minimum))
    for width in widths:
        cuts=[(a[1]+b[0])/2 for a,b in zip(intervals,intervals[1:])
              if b[0]-a[1]>=width]
        groups=[[] for _ in range(len(cuts)+1)]
        for word in words:
            center=(word['bbox'][0]+word['bbox'][2])/2
            groups[sum(center>cut for cut in cuts)].append(word)
        rows=[_pixel_lines(group) for group in groups]
        if any(len(column)<3 or statistics.median(len(row) for row in column)<2
               for column in rows):continue
        boxes=[vision_reading.union([w['bbox'] for w in group]) for group in groups]
        if any(min(a[3],b[3])-max(a[1],b[1])<2*height
               for a,b in zip(boxes,boxes[1:])):continue
        return [_pixel_line_order(group) for group in groups],True
    return [words],False


def _source_paragraph_boundaries(answer,optical,display,regions,body_owners):
    """Partition a merged BODY reading only at independently bound ink starts.

    An inset row after a complete source sentence must belong to the same
    column's established body margin. Both sides need a unique, consecutive
    original-word sequence matching the response. Neither OCR nor geometry
    rewrites a word or its original whitespace-token confidence position.
    """
    import statistics
    answer=copy.deepcopy(answer);published=[];repairs=[]
    def reject(message):
        if unit_recovery.current() is None:
            raise ValueError(message)
        with unit_recovery.guard(display.pno,'source_word_coverage',block=block['id']):
            raise ValueError(message)
    identities={b['id'] for b in answer['blocks']}
    norm=lambda value:vision_reading.normalized(value).casefold()
    word_key=lambda value:re.sub(r'[^\w]','',norm(value))
    def one_character_edit(left,right):
        if abs(len(left)-len(right))>1:return False
        at=next((i for i,(a,b) in enumerate(zip(left,right)) if a!=b),min(len(left),len(right)))
        if at==min(len(left),len(right)):return abs(len(left)-len(right))==1
        if len(left)==len(right):return left[at+1:]==right[at+1:]
        if len(left)>len(right):return left[at+1:]==right[at:]
        return left[at:]==right[at+1:]
    def near_windows(words,window):
        positions=[]
        for i in range(3,len(words)-2):
            other=words[i-3:i+3]
            different=[(a,b)for a,b in zip(other,window)if a!=b]
            if not different or len(different)==1 and one_character_edit(*different[0]):positions.append(i)
        return positions
    for block in answer['blocks']:
        bid=block['id'];territories=regions.get(bid,[])
        if block['role']!='body' or len(territories)!=1:
            published.append(block);continue
        original=_owned_block_words(block,answer,optical,display,regions,body_owners,ordered=True)
        rows=_pixel_lines(original)
        if len(rows)<3:
            published.append(block);continue
        height=statistics.median(w['bbox'][3]-w['bbox'][1] for w in original)
        margin=min(row[0]['bbox'][0] for row in rows)
        normal_rows=[row for row in rows if len(row)>=2 and abs(row[0]['bbox'][0]-margin)<=height*.35]
        if len(normal_rows)<2:
            published.append(block);continue
        tokens=block['text'].split()
        visible=[(i,word_key(re.sub(r'<n:[^>]+>','',token))) for i,token in enumerate(tokens)]
        visible=[(i,key)for i,key in visible if key]
        values=[key for i,key in visible]
        source=[w for row in rows for w in row if word_key(w['text'])]
        keys=[word_key(w['text'])for w in source]
        offsets={id(w):i for i,w in enumerate(source)}
        # A delimited ordinal is a printed structural atom, not a quantity.
        # Both complete entries must have coherent hanging continuation rows;
        # their aligned heads and consecutive values are independent of OCR's
        # ability to observe each entry's final sentence punctuation.
        heads=[]
        for row_index,row in enumerate(rows):
            marker=re.fullmatch(r'([1-9][0-9]*)([.)])',norm(row[0]['text']))
            if marker and len(row)>=3:
                heads.append((row_index,int(marker.group(1)),marker.group(2)))
        ordinal_rows=set()
        if (len(heads)>=2 and heads[0][0]==0 and
                len({delimiter for _,_,delimiter in heads})==1 and
                all(right[1]==left[1]+1 for left,right in zip(heads,heads[1:])) and
                all(abs(rows[i][0]['bbox'][0]-margin)<=height*.35 for i,_,_ in heads)):
            proved=True;hanging_margins=[]
            for at,(first,_,_) in enumerate(heads):
                end=heads[at+1][0] if at+1<len(heads) else len(rows)
                hanging=rows[first+1:end]
                if len(hanging)<2 or any(len(row)<2 for row in hanging):
                    proved=False;break
                hanging_margin=statistics.median(row[0]['bbox'][0] for row in hanging)
                if (not max(3,height*.55)<hanging_margin-margin<=height*4 or
                        any(abs(row[0]['bbox'][0]-hanging_margin)>height*.35 for row in hanging)):
                    proved=False;break
                hanging_margins.append(hanging_margin)
                marker=norm(rows[first][0]['text'])
                if sum(norm(word['text'])==marker for word in source)!=1:
                    proved=False;break
            if proved and max(hanging_margins)-min(hanging_margins)<=height*.35:
                ordinal_rows={row_index for row_index,_,_ in heads[1:]}
        cuts=[]
        for row_index,row in enumerate(rows[1:],1):
            previous=rows[row_index-1]
            ordinal=row_index in ordinal_rows
            if not ordinal and not max(3,height*.55)<row[0]['bbox'][0]-margin<=height*4:continue
            lexical_previous=[w for w in previous if word_key(w['text'])]
            lexical_current=[w for w in row if word_key(w['text'])]
            if not lexical_previous or not lexical_current:continue
            terminal=re.sub(r'[)\]"”’\']+$','',norm(lexical_previous[-1]['text']).rstrip())
            if not ordinal and not re.search(r'[.!?…]$',terminal):continue
            # The row's own complete sentence is evidence; indentation alone
            # must not split an unfinished, wrapped line or a hanging run.
            position=offsets[id(lexical_current[0])]
            if position<3 or len(keys)-position<3:continue
            preceding=' '.join(w['text']for w in source[:position]).rstrip()
            preceding=re.sub(r'[)\]"”’\']+$','',preceding)
            opening=' '.join(w['text']for w in lexical_current).lstrip('(["“‘\'').lstrip()
            if not ordinal and _nonterminal_period(preceding,opening):continue
            sequence=keys[position-3:position+3]
            candidates=[i for i in range(3,len(values)-2) if values[i-3:i+3]==sequence]
            source_candidates=[i for i in range(3,len(keys)-2) if keys[i-3:i+3]==sequence]
            if not candidates:
                # A one-character optical defect cannot erase otherwise proved
                # prose. Five exact surrounding words and a
                # reciprocal unique window locate only the structural cut.
                # The differing word stays untouched for pixel adjudication.
                near=near_windows(values,sequence)
                if len(near)==1 and near_windows(keys,values[near[0]-3:near[0]+3])==[position]:
                    candidates=near
            if len(candidates)!=1 or source_candidates!=[position]:
                reject('Original paragraph start has ambiguous response ownership')
            boundary=visible[candidates[0]][0]
            left=next((re.sub(r'<n:[^>]+>','',token)for token in reversed(tokens[:boundary])
                       if re.sub(r'<n:[^>]+>','',token)), '')
            model_terminal=re.sub(r'[)\]"”’\']+$','',norm(left).rstrip())
            model_marker=norm(re.sub(r'<n:[^>]+>','',tokens[boundary]))
            if (ordinal and model_marker!=norm(lexical_current[0]['text']) or
                    not ordinal and not re.search(r'[.!?…]$',model_terminal)):
                reject('Original paragraph punctuation disagrees with response')
            cuts.append((boundary,row_index))
        if not cuts:
            published.append(block);continue
        if len({a for a,b in cuts})!=len(cuts) or cuts!=sorted(cuts):
            reject('Original paragraph starts have no ordered response partition')
        spans=[(0,0)]+cuts+[(len(tokens),len(rows))]
        parts=[]
        for index,((begin,first),(end,last))in enumerate(zip(spans,spans[1:])):
            ident=bid if index==0 else bid[:30]+'-p'+str(index+1)
            collision=0
            while index and ident in identities:
                collision+=1;suffix='-p'+str(index+1)+'-'+str(collision)
                ident=bid[:39-len(suffix)]+suffix
            identities.add(ident)
            ink=[w for row in rows[first:last]for w in row]
            box=vision_reading.union([w['bbox']for w in ink]);rect=display.rect
            part=dict(block,id=ident,text=' '.join(tokens[begin:end]),
                low_confidence=[i-begin for i in block['low_confidence']if begin<=i<end],
                bbox=[box[0]/rect.width*1000,box[1]/rect.height*1000,
                      box[2]/rect.width*1000,box[3]/rect.height*1000],
                continues_previous=block['continues_previous']if index==0 else False,
                continues_next=block['continues_next']if index==len(spans)-2 else False)
            parts.append(part);regions[ident]=[box];body_owners[ident]=box
            for word in ink:regions.setdefault('_instances',{})[_source_instance(word)]=ident
        published.extend(parts)
        repairs.append(dict(method=('original_delimited_hanging_paragraph_partition'
            if any(row_index in ordinal_rows for _,row_index in cuts)
            else 'original_indented_paragraph_partition'),block=bid,
            derived_blocks=[part['id']for part in parts],
            whitespace_token_cuts=[a for a,b in cuts],
            original_row_cuts=[b for a,b in cuts]))
    answer['blocks']=published
    vision_reading.validate(answer)
    return answer,repairs


def _omission_coverage(answer,display,regions,optical=None):
    """Record exactly the footprints used to consider each live unit covered."""
    coverage={b['id']:regions.get(b['id'],[block_rect(b,display)]) for b in answer['blocks']}
    controller=unit_recovery.current()
    blocked=controller.blocked(display.pno) if controller else {}
    for block in answer['blocks']:
        if (block['role']=='footnote' and block['id'] in blocked
                and not coverage[block['id']]):
            # An unplaced note retains the complete original page as context.
            # That context covers unclaimed ink without assigning it a note
            # location or reclassifying its text as an omitted body paragraph.
            # Independently owned body remains live. A previously admitted
            # narrower note footprint is preserved by register_regions.
            coverage[block['id']]=[list(display.rect)]
    if controller and optical:
        from .source_geometry import local_note_openings
        # Unreferenced rejected notes have no validation stub in `answer`.
        # Their retained source image must not also become a recovered BODY
        # row. Reserve only an actual unique opening and its continuous row,
        # never an unproved rectangle or a distant matching prose occurrence.
        for block in controller.source_answer(display.pno)['blocks']:
            bid=block['id'];bounds=block['bbox']
            if (bid not in blocked or bid in coverage or block['role']!='footnote'
                    or not all(0<=v<=1000 for v in bounds)
                    or not(bounds[0]<bounds[2] and bounds[1]<bounds[3])):continue
            hint=block_rect(block,display)
            available=[i for i,w in enumerate(optical)
                if (regions.get('_instances',{}).get(_source_instance(w),bid)==bid)]
            openings=local_note_openings(optical,available,block['marker'],hint)
            if len(openings)!=1:continue
            opening=optical[openings[0]];box=opening['bbox'];height=max(1,box[3]-box[1])
            row=sorted((optical[i]for i in available if i!=openings[0]
                and hint[0]-3<=optical[i]['bbox'][0] and optical[i]['bbox'][2]<=hint[2]+3
                and optical[i]['bbox'][0]>=box[2]
                and abs(sum(optical[i]['bbox'][1::2])-sum(box[1::2]))<=height*1.5),
                key=lambda w:w['bbox'][0])
            chain=[opening]
            for word in row:
                if word['bbox'][0]-chain[-1]['bbox'][2]>height*4:break
                chain.append(word)
            if len(chain)>=3:coverage[bid]=[vision_reading.union([w['bbox']for w in chain])]
    return coverage


def _recovery_visual_tail(words):
    """Keep a compact trailing diagram out of an omitted prose transcription.

    Several centered, sparse glyph rows below a colon are original visual
    evidence, not reliable prose merely because a recognizer assigned digits.
    Ordinary word rows, wide tables and ambiguous geometry retain their route.
    """
    import statistics
    rows=_pixel_lines(words)
    if len(rows)<6:return None
    tail=[]
    for row in reversed(rows):
        if not(1<=len(row)<=3)or any(len(w['text'])>3 for w in row):break
        tail.insert(0,row)
        if len(tail)>8:return None
    if len(tail)<3 or len(rows)-len(tail)<3:return None
    head=rows[:-len(tail)]
    if not head[-1][-1]['text'].endswith(':'):return None
    prose=[row for row in head if len(row)>=4]
    if len(prose)<3:return None
    from .text_quality import english_lexicon
    vocabulary=english_lexicon()
    if sum(''.join(c for c in w['text'].casefold()if c.isalpha())in vocabulary
           for row in tail for w in row if any(c.isalpha()for c in w['text']))>1:return None
    width=statistics.median(max(w['bbox'][2]for w in row)-min(w['bbox'][0]for w in row)for row in prose)
    height=statistics.median(w['bbox'][3]-w['bbox'][1]for row in prose for w in row)
    if width<=15*height:return None
    boxes=[vision_reading.union([w['bbox']for w in row])for row in tail]
    if any(b[2]-b[0]>.4*width for b in boxes):return None
    centers=[(b[0]+b[2])/2 for b in boxes]
    if max(centers)-min(centers)>1.5*height:return None
    if not 0<=boxes[0][1]-max(w['bbox'][3]for w in head[-1])<=3*height:return None
    return ([w for row in head for w in row],[w for row in tail for w in row])


def cover_missing_lines(answer,optical,source_words,display,regions=None,body_owners=None):
    """Recover line-sized printed text omitted entirely by the page reader.

    Independent OCR is evidence of source ink; it never makes recovered wording
    certain. Figure/table territories are already represented as source images.
    Short unmatched marks remain diagnostics instead of invented paragraph roles.
    """
    if regions is not None:_reserve_layer_instances(answer,optical,display,regions)
    answer=copy.deepcopy(answer);blocks=answer['blocks'];boxes=[box for b in blocks for box in (regions or {}).get(b['id'],[block_rect(b,display)])]
    # Reserved SOURCE territories survive removal of rejected authored blocks.
    # They are rendered by unit_source, so recovering their ink again would
    # duplicate the unit and reintroduce the very failure just contained.
    boxes.extend((regions or {}).get('_degraded_territories',[]))
    def covered(word):
        if _source_instance(word) in (regions or {}).get('_instances',{}):return True
        cx=(word['bbox'][0]+word['bbox'][2])/2;cy=(word['bbox'][1]+word['bbox'][3])/2
        return any(b[0]-3<=cx<=b[2]+3 and b[1]-3<=cy<=b[3]+3 for b in boxes)
    runs=[];pending=[]
    for word in optical:
        if covered(word):
            if pending:runs.append(pending);pending=[]
        else:pending.append(word)
    if pending:runs.append(pending)
    partitioned=[]
    for run in runs:
        columns,proved=_proved_recovery_columns(run)
        for column in columns:
            visual_tail=_recovery_visual_tail(column)
            if visual_tail:
                prose,artwork=visual_tail
                partitioned.extend(((prose,proved,False),(artwork,proved,True)))
            else:partitioned.append((column,proved,False))
    recovered=[];pixel_rows=_pixel_row_ids(optical)
    # A covered foreign word still occupies its place on the source row.
    # Exact consecutive physical instances prohibit jumping across that ink.
    row_pairs={(_source_instance(left),_source_instance(right))
               for row in _pixel_lines(optical) for left,right in zip(row,row[1:])}
    for index,(run,proved_column,is_visual) in enumerate(partitioned):
        bbox=vision_reading.union([w['bbox'] for w in run])
        if is_visual:
            ident='recovered-visual-'+hashlib.sha256(repr(tuple(round(v,3)for v in bbox)).encode()).hexdigest()[:16]
            while any(b['id']==ident for b in blocks):ident+='x'
            r=display.rect
            normalized=[bbox[0]/r.width*1000,bbox[1]/r.height*1000,bbox[2]/r.width*1000,bbox[3]/r.height*1000]
            block=dict(id=ident,role='figure',text='Original printed diagram',marker='',bbox=normalized,
                low_confidence=[],continues_previous=False,continues_next=False)
            neighbours=[(i,b)for i,b in enumerate(blocks)
                if b['bbox'][0]<normalized[2]and normalized[0]<b['bbox'][2]and b['bbox'][3]<=normalized[1]+5]
            at=max(neighbours,key=lambda p:p[1]['bbox'][3])[0]+1 if neighbours else 0
            blocks.insert(at,block);recovered.append(ident)
            if regions is not None:
                regions[ident]=[bbox]
                regions.setdefault('_instances',{}).update((_source_instance(w),ident)for w in run)
            continue
        local=[w for w in owned_words(source_words,bbox) if not covered(w)]
        if proved_column:local=_pixel_line_order(local)
        # A legible original layer is preferred; a sparse/garbage layer cannot
        # supply the omitted printed sentence. Optical fallback is disclosed.
        from difflib import SequenceMatcher
        optical_text=[vision_reading.normalized(w['text']).casefold() for w in run]
        source_text=[vision_reading.normalized(w['text']).casefold() for w in local]
        sm=SequenceMatcher(None,optical_text,source_text,autojunk=False)
        score=sum(size for _,_,size in sm.get_matching_blocks())/max(1,len(run))
        chosen=local if score>=.75 else run
        # A clipped ownership strip can omit a tail which the reader already
        # placed in the right paragraph. Exact consecutive source occurrences
        # settle its owner before a new recovery unit can steal that tail.
        # This grants geometry, never a new transcription or a lexical guess.
        ordered_run=_pixel_line_order(run)
        run_tokens=[vision_reading.normalized(w['text'])for w in ordered_run]
        tail_owners=[]
        if regions is not None and len(run_tokens)>=2 and run_tokens==[
                vision_reading.normalized(w['text'])for w in chosen]:
            for candidate in blocks:
                tokens=[vision_reading.normalized(t)for t in candidate['text'].split()]
                if (candidate['role']!='body' or len(tokens)<len(run_tokens)+2
                        or tokens[-len(run_tokens):]!=run_tokens):continue
                ink=_owned_block_words(candidate,answer,optical,display,regions,body_owners,ordered=True)
                if (len(ink)<2 or tokens[-len(run_tokens)-2:-len(run_tokens)]!=[
                        vision_reading.normalized(w['text'])for w in ink[-2:]]):continue
                if (_source_instance(ink[-1]),_source_instance(ordered_run[0]))not in row_pairs:continue
                if any(regions['_instances'].get(_source_instance(w),candidate['id'])!=candidate['id']
                       for w in run+chosen):continue
                tail_owners.append(candidate)
        if len(tail_owners)==1:
            owner=tail_owners[0]['id']
            regions.setdefault(owner,[]).append(bbox)
            regions['_instances'].update((_source_instance(w),owner)for w in run+chosen)
            continue
        # Geometry, rather than the current omission list's index, owns a
        # recovered ID. Removing a failed neighbour cannot rename another unit.
        ident='recovered-'+hashlib.sha256(repr(tuple(round(v, 3) for v in bbox)).encode()).hexdigest()[:16]
        while any(b['id']==ident for b in blocks):ident+='x'
        r=display.rect;normalized=[bbox[0]/r.width*1000,bbox[1]/r.height*1000,bbox[2]/r.width*1000,bbox[3]/r.height*1000]
        block=dict(id=ident,role='other',text=' '.join(w['text'] for w in chosen),marker='',bbox=normalized,
                   low_confidence=list(range(len(chosen))),continues_previous=False,continues_next=False)
        # Preserve existing model column order; place after the nearest preceding
        # source block in the same column. No model wording is inferred here.
        neighbours=[(i,b) for i,b in enumerate(blocks) if b['bbox'][0]<normalized[2] and normalized[0]<b['bbox'][2] and b['bbox'][3]<=normalized[1]+5]
        at=(max(neighbours,key=lambda p:p[1]['bbox'][3])[0]+1) if neighbours else 0
        # A cached block may stop partway through a printed row. Its exact
        # admitted source tail can precede the omitted word even though their
        # whole rectangles overlap vertically. This is not a model role guess.
        head=_pixel_line_order(run)[0]
        same_row=[]
        for position,candidate in enumerate(blocks):
            if candidate['role']!='body':continue
            ink=_owned_block_words(candidate,answer,optical,display,regions,body_owners,ordered=True)
            if (ink and candidate['text'].split() and
                    vision_reading.normalized(candidate['text'].split()[-1])==vision_reading.normalized(ink[-1]['text'])
                    and (_source_instance(ink[-1]),_source_instance(head)) in row_pairs
                    and ( _narrow_row_edge(ink[-1]['bbox'],head['bbox'],
                        pixel_rows.get(id(ink[-1])),pixel_rows.get(id(head))) or
                        (pixel_rows.get(id(ink[-1]))==pixel_rows.get(id(head))
                         and block_rect(candidate,display)[0]<bbox[2]
                         and bbox[0]<block_rect(candidate,display)[2]
                         and 0<=head['bbox'][0]-ink[-1]['bbox'][2]<=
                             2*max(ink[-1]['bbox'][3]-ink[-1]['bbox'][1],head['bbox'][3]-head['bbox'][1])))):
                same_row.append((position,candidate,ink[-1]['bbox'][2]))
        if same_row:at=max(same_row,key=lambda value:value[2])[0]+1
        if not neighbours and not same_row:
            # An omitted column opening has no predecessor in its own column.
            # It follows the preceding column's last body block, rather than
            # being prepended before all of the page's authored paragraphs.
            preceding_column=[(i,b) for i,b in enumerate(blocks)
                if (b['role']=='body' or proved_column and b['id'] in recovered)
                and b['bbox'][2]<normalized[0]-5
                and (proved_column or b['bbox'][1]>normalized[1])]
            if preceding_column:
                closest=max(preceding_column,key=lambda p:p[1]['bbox'][2])[1]['bbox']
                same_column=[p for p in preceding_column if p[1]['bbox'][0]<closest[2]
                             and closest[0]<p[1]['bbox'][2]]
                at=max(same_column,key=lambda p:p[1]['bbox'][3])[0]+1
        previous=next((b for b in reversed(blocks[:at]) if b['role'] not in ('running_head','folio')),None)
        float_tail=None
        if previous and previous['role'] in ('figure','caption'):
            tail=next((b for b in reversed(blocks[:at])
                       if b['role'] not in ('running_head','folio','figure','caption')),None)
            between=blocks[blocks.index(tail)+1:at] if tail else []
            if (tail and tail['role']=='body' and any(b['role']=='figure' for b in between)
                    and all(b['role'] in ('running_head','folio','figure','caption') for b in between)):
                previous=tail;float_tail=tail
        if previous and previous['role']=='footnote':
            flow_tail=next((b for b in reversed(blocks[:at])
                           if b['role'] not in ('running_head','folio','footnote')),None)
            if flow_tail and flow_tail['role']=='body':
                tail_box=(regions or {}).get(flow_tail['id'],[block_rect(flow_tail,display)])[-1]
                between=blocks[blocks.index(flow_tail)+1:at]
                if all(b['role'] in ('running_head','folio') or
                       (b['role']=='footnote' and block_rect(b,display)[1]>=tail_box[3]-3
                        and block_rect(b,display)[0]<tail_box[2]
                        and block_rect(b,display)[2]<=bbox[0]+3) for b in between):
                    previous=flow_tail
        following=next((b for b in blocks[at:] if b['role'] not in ('running_head','folio','footnote')),None)
        height=max(1,max(w['bbox'][3]-w['bbox'][1] for w in run))
        prev_box=block_rect(previous,display) if previous else None
        following_box=block_rect(following,display) if following else None
        next_edge=(following and following['role']=='body' and following['continues_previous']
                   and following_box[0]>bbox[2]+3 and following_box[1]<bbox[1])
        labelled=bool(re.match(r'^(?:\d+[.)]?|[*†‡])$',run[0]['text']))
        following_pixels=(_owned_block_words(following,answer,optical,display,regions,body_owners,ordered=True)
                          if following and following['role']=='body' else [])
        run_tail=_pixel_line_order(run)[-1]
        following_text=(vision_reading.align(following['text'],following_pixels,(),has_layer=True)['tokens']
                        if following_pixels else [])
        following_text=' '.join(t for t in following_text if not t.startswith('<n:'))
        forward=(not labelled and following_pixels and
                 _narrow_row_edge(run_tail['bbox'],following_pixels[0]['bbox'],
                                  pixel_rows.get(id(run_tail)),pixel_rows.get(id(following_pixels[0])))
                 and assemble.continues(block['text'],following_text))
        # A short omitted opening is recoverable only through independently
        # connected source ink. The global long-run fallback remains unchanged.
        rows=_pixel_lines(run)
        import statistics
        first_x=min(w['bbox'][0] for w in rows[0])
        column_x=(statistics.median(min(w['bbox'][0] for w in row) for row in rows[1:4])
                  if len(rows)>1 else following_box[0] if following_box else bbox[0])
        previous_text=previous['text'] if previous else ''
        if previous and previous['role']=='body':
            printed=_owned_block_words(previous,answer,optical,display,regions,body_owners,ordered=True)
            guarded=vision_reading.align(previous_text,printed,(),has_layer=True)
            previous_text=' '.join(t for t in guarded['tokens'] if not t.startswith('<n:'))
        # Recovery initially has no paragraph role. Exact printed endpoints,
        # source column origin and only floating artwork between the pieces
        # can establish that role; punctuation and indentation still bound it.
        column_origin=min([column_x]+[box[0] for b in blocks if b['role']=='body'
            for box in (regions or {}).get(b['id'],[block_rect(b,display)])
            if box[0]<bbox[2] and bbox[0]<box[2]])
        float_edge=(float_tail is not None and not labelled and printed and
            first_x<=column_x+3 and prev_box[0]<bbox[2] and bbox[0]<prev_box[2]
            and bbox[1]>prev_box[3] and previous_text.split() and
            vision_reading.normalized(previous_text.split()[-1])==vision_reading.normalized(printed[-1]['text'])
            and vision_reading.normalized(block['text'].split()[0])==vision_reading.normalized(_pixel_line_order(run)[0]['text'])
            and assemble.continues(previous_text,block['text']))
        # An omitted run can continue a known paragraph across a large gap.
        # Consecutive physical rows exclude intervening prose; the run's later
        # rows establish its column margin independently of a sloping tail.
        backward=(previous and previous['role']=='body' and not labelled and printed
            and first_x<=column_x+3 and bbox[0]<prev_box[2] and prev_box[0]<bbox[2]
            and (bbox[1]>=prev_box[3]-3 or any(previous is value[1] for value in same_row))
            and (pixel_rows.get(id(_pixel_line_order(run)[0]))==pixel_rows.get(id(printed[-1]),-2)+1
                 or any(previous is value[1] for value in same_row)
                 or ((_source_instance(printed[-1]),_source_instance(_pixel_line_order(run)[0])) in row_pairs
                     and _narrow_row_edge(printed[-1]['bbox'],_pixel_line_order(run)[0]['bbox'],
                         pixel_rows.get(id(printed[-1])),pixel_rows.get(id(_pixel_line_order(run)[0])))))
            and previous_text.split() and vision_reading.normalized(previous_text.split()[-1])==vision_reading.normalized(printed[-1]['text'])
            and assemble.continues(previous_text,block['text']))
        corroborated_short=(len(run)>=2 and score==1. and len(local)==len(run)
            and all(vision_reading.normalized(a['text'])==vision_reading.normalized(b['text'])
                    for a,b in zip(run,local))
            and any(sum(c.isalpha() for c in word['text'])>=3 for word in run))
        if len(run)<vision_reading.UNALIGNED_RUN and not (forward or float_edge or backward or corroborated_short):continue
        recovered_head=(previous and previous['role']=='body' and not labelled
            and bbox[0]>prev_box[2]+3 and bbox[1]<prev_box[1]
            and first_x<=column_x+3 and assemble.continues(previous_text,block['text']))
        if (previous and previous['role']=='body' and not labelled
                and (backward or float_edge or recovered_head or (-3<=bbox[1]-prev_box[3]<=3*height
                     and (previous['continues_next'] or next_edge)))):
            if next_edge:
                # A model can repeat the next leaf's head in its previous block.
                # Remove only an exact owned head that is absent from the local
                # source, in the presence of this independently observed tail.
                left_tokens=previous['text'].split();right_tokens=following['text'].split()
                overlap=0
                for count in range(5,min(len(left_tokens),len(right_tokens))+1):
                    if left_tokens[-count:]==right_tokens[:count]:overlap=count
                if overlap:
                    repeated=' '.join(left_tokens[-overlap:])
                    local_regions=[region for region in (regions or {}).get(previous['id'],[prev_box])
                        if region[0]<bbox[2] and bbox[0]<region[2]]
                    local_text=' '.join(w['text'] for region in local_regions for w in owned_words(optical,region))
                    right_text=' '.join(w['text'] for w in owned_words(optical,following_box))
                    if repeated not in local_text and repeated in right_text:
                        previous['text']=' '.join(left_tokens[:-overlap])
                        previous['low_confidence']=[i for i in previous['low_confidence'] if i<len(left_tokens)-overlap]
                        if local_regions:
                            if regions is not None:regions[previous['id']]=local_regions
                            rect=vision_reading.union(local_regions)
                            previous['bbox']=[rect[0]/r.width*1000,rect[1]/r.height*1000,
                                              rect[2]/r.width*1000,rect[3]/r.height*1000]
            previous['continues_next']=True
            block['role']='body';block['continues_previous']=True;block['continues_next']=True
            if regions is not None:regions[ident]=[bbox]
        elif forward:
            block['role']='body';block['continues_next']=True
            following['continues_previous']=True
            if regions is not None:regions[ident]=[bbox]
        # Every admitted recovery owns only its physical source instances.
        # A rectangle alone can reselect neighboring or already published ink.
        if regions is not None:
            regions[ident]=[bbox]
            for word in run+chosen:
                regions.setdefault('_instances',{})[_source_instance(word)]=ident
        blocks.insert(at,block);recovered.append(ident)
    return answer,set(recovered)


def _defer_unit_prints(pages):
    """Keep generated block prints outside lexical paragraph merging.

    A print following a source fragment is not a sentence boundary. Remove
    only the exact generated supplement while page/column seams are decided;
    after merging, its source attributes locate the completed paragraph.
    """
    saved=[]
    pattern=re.compile(r'(?:<div class="reflow-unit-print">(?:(?!</?div\b).)*</div>)+$',re.S)
    for page in pages:
        for i,fragment in enumerate(page['body']):
            match=pattern.search(fragment)
            if match is None:continue
            owners=re.findall(r'data-source-page="(\d+)" data-source-block="([^"]+)"',fragment[:match.start()])
            if not owners:continue
            saved.append((owners[-1],match[0]))
            page['body'][i]=fragment[:match.start()]
    return saved


def _restore_unit_prints(pages,saved):
    """A unit's shared print follows the paragraph containing its final words."""
    for (number,block),html in saved:
        owner=f'data-source-page="{number}" data-source-block="{block}"'
        locations=[(page,i)for page in pages for i,fragment in enumerate(page['body'])if owner in fragment]
        if not locations:raise ValueError('Published unit print lost its source paragraph')
        page,index=locations[-1]
        page['body'][index]+=html


def _join_control_carriers(pages):
    """Use the native joiner's generated-copy offset carrier for our controls.

    This intermediate wrapper is removed before publication. It makes a dagger
    transparent to source hyphen decisions while preserving its exact position,
    links and markup through the existing checked merge.
    """
    pattern=re.compile(r'<a id="(?:r-v4-d-|r-v4-unit-source-|v4-ref-)[^"]+" class="(?:source-disclosure-ref|noteref)" epub:type="noteref" href="[^"]+"><sup>[^<]*</sup></a>')
    carriers={}
    def carrier_for(anchor):
        identity=hashlib.sha256(anchor.encode()).hexdigest()
        carrier='<span class="source-reading-annotation" id="reading-return-reading-'+identity+'"></span>'
        carriers[carrier]=anchor
        return carrier
    def wrap(match):
        return carrier_for(match.group(0))
    # Adjacent print assists the flagged reading; it is not another source word.
    # Hide only this factory-generated attachment from the lexical seam copy,
    # preserving its exact bytes and location when the join restores controls.
    flagged=re.compile(r'(<span class="reflow-flagged-reading"><span class="reflow-plausible">.*?</span>)(<a class="source-word-image"(?: title="Inspect original print")? href="[^"]+"><img [^>]+/></a>)(</span>)')
    def assist(match):
        return match.group(1)+carrier_for(match.group(2))+match.group(3)
    compact=re.compile(r'<span class="reflow-print-control">.*?</span>',re.S)
    origins=re.compile(r'<a id="r-v4-d-[^"]+" class="reflow-reading-origin"></a>')
    for page in pages:
        with unit_recovery.guard(page['pno'],'seam_control_carriers'):
            page['body']=[pattern.sub(wrap,flagged.sub(assist,origins.sub(wrap,compact.sub(wrap,fragment)))) for fragment in page['body']]
    return carriers


def _recover_page_tail_roles(pages,records):
    """Bind an omitted unfinished paragraph using the next page's source ink.

    The recovery guard distinguishes originally omitted rows from authored
    OTHER blocks. Both printed endpoints must exactly match the publication;
    the normal punctuation, source geometry and structural checks still apply.
    Its uncertain reading and original evidence controls remain unchanged.
    """
    repairs=[]
    for previous,current in zip(pages,pages[1:]):
        with unit_recovery.guard([previous['pno'],current['pno']],'recover_page_tail_roles', units=unit_recovery.current().seam_units(previous,current) if unit_recovery.current() else None):
            a,b=previous['pno'],current['pno']
            if b!=a+1 or not previous['body'] or not current['body']:continue
            before=[x for x in records[a]['blocks'] if x['role'] not in ('running_head','folio','footnote')]
            after=[x for x in records[b]['blocks'] if x['role'] not in ('running_head','folio','footnote')]
            if not before or not after:continue
            left,right=before[-1],after[0]
            if left['role']!='other' or right['role']!='body':continue
            if not any(g.get('kind')=='omitted_pixel_line_recovery' for g in left.get('guard',[])):continue
            tail,head=previous['body'][-1],current['body'][0]
            if not tail.startswith('<p>') or not head.startswith('<p>'):continue
            t,h=_seam_text(tail).split(),_seam_text(head).split()
            lexical=lambda word:vision_reading.normalized(word).casefold()
            if (not t or not h or not left.get('printed_head') or not left.get('printed_tail')
                    or not right.get('printed_head') or not left.get('printed_tail_box') or not right.get('printed_head_box')):continue
            if (lexical(t[0])!=lexical(left['printed_head']) or
                    lexical(t[-1])!=lexical(left['printed_tail']) or
                    lexical(h[0])!=lexical(right['printed_head'])):continue
            opening=right.get('source_regions')
            if not opening or any(x['role']=='footnote' and x['bbox'][1]<opening[0][1]
                                  for x in records[b]['blocks']):continue
            proposed=dict(left,role='body')
            if not _source_continuation(proposed,right,records[b]['blocks'],tail,head):continue
            left['role']='body'
            left['seam_role_proof']='recovered_source_tail_and_exact_next_page_body'
            repairs.append(dict(page=a,block=left['id'],continuation_page=b,
                                method=left['seam_role_proof']))
    return repairs


def _defer_page_art(pages,records,scanned):
    """Carry a paragraph's floating art until its last continuation page.

    Moving before the shared page-turn join lets its carrier preserve a chain
    spanning any number of adjacent pages. A terminal sentence, an indented
    opening, a heading or an unproven table remains a boundary.
    """
    openings={}
    for previous,current in zip(pages,pages[1:]):
        with unit_recovery.guard([previous['pno'],current['pno']],'defer_page_art', units=unit_recovery.current().seam_units(previous,current) if unit_recovery.current() else None):
            a,b=previous['pno'],current['pno']
            if b!=a+1 or a not in scanned or b not in scanned:continue
            body=previous['body'];at=len(body)
            while at and body[at-1].startswith(('<figure','<p class="caption">')):at-=1
            if at==len(body) or not at or not body[at-1].startswith('<p>'):continue
            if not any(fragment.startswith('<figure') for fragment in body[at:]):continue
            before=[x for x in records[a]['blocks'] if x['role']=='body']
            emitted=[x for x in records[a]['blocks'] if x['role'] not in ('running_head','folio','footnote')]
            if any(x['role']=='table' for x in emitted) and not _floating_art(
                    [x for x in emitted if x['role'] in ('figure','table','caption')]):continue
            after=[x for x in records[b]['blocks'] if x['role'] not in ('running_head','folio','footnote')]
            if not before or not after or not current['body']:continue
            opening=next((i for i,x in enumerate(after) if x['role']=='body'),None)
            if opening is None or opening>=len(current['body']):continue
            leading=after[:opening]
            if leading and (not _floating_art(leading) or any(
                    not fragment.startswith(('<figure','<p class="caption">'))
                    for fragment in current['body'][:opening])):continue
            head=current['body'][opening]
            if not head.startswith('<p>'):continue
            left,right=before[-1],after[opening];tail=body[at-1]
            # Source furniture and genuine in-flow notes remain barriers. Only
            # the trailing source objects represented by these carriers may
            # move across this exact unfinished paragraph endpoint.
            prior=records[a]['blocks'];position=next(i for i,x in enumerate(prior) if x['id']==left['id'])
            edge=left['source_regions'][-1]
            trailing=prior[position+1:]
            # A trailing carrier may be below the paragraph, on the next
            # leaf, or wrapped inside this same source-bound paragraph. The
            # latter needs actual owned rows above and below the object in its
            # horizontal territory; a union box alone proves neither relation.
            # This decides placement only, never clean artwork separation.
            def follows_or_is_wrapped(item):
                box=_art_source_box(item,trailing)
                overlap=lambda row: box[0]<row[2] and box[2]>row[0]
                if box[0]>=edge[2]+3:return True
                if overlap(edge) and box[1]>=edge[3]-3:return True
                rows=[row for row in left.get('source_rows',[]) if overlap(row)]
                return (any(row[3]<=box[1]+3 for row in rows) and
                        any(row[1]>=box[3]-3 for row in rows))
            if any(not follows_or_is_wrapped(x) for x in trailing
                   if x['role'] in ('figure','table','caption')):continue
            if any(x['role'] not in ('running_head','folio','figure','table','caption')
                   and not (x['role']=='footnote' and x['bbox'][1]>=edge[3]-3
                            and x['bbox'][0]<edge[2] and x['bbox'][2]>edge[0])
                   for x in prior[position+1:]):continue
            source_position=next(i for i,x in enumerate(records[b]['blocks']) if x['id']==right['id'])
            territory=right['source_regions'][0]
            if any(x['role'] not in ('running_head','folio','figure','table','caption')
                   and not _source_note_on_earlier_leaf(x,territory)
                   for x in records[b]['blocks'][:source_position]):continue
            division=_completed_division(left,right,tail,head)
            lexical=lambda t:vision_reading.normalized(t).casefold()
            if not division:
                if not left.get('printed_tail') or not right.get('printed_head'):continue
                if lexical(build_epub.block_text(tail).split()[-1])!=lexical(left['printed_tail']):continue
                if lexical(build_epub.block_text(head).split()[0])!=lexical(right['printed_head']):continue
            proven=right
            if leading:
                source_head=division['source_head']['bbox'] if division else right.get('printed_head_box')
                source_tail=division['source_tail']['bbox'] if division else left.get('printed_tail_box')
                if source_head is None or source_tail is None:continue
                territory=right['source_regions'][0]
                if any(not _art_precedes_source_opening(x,leading,source_head,territory)
                       for x in leading):continue
                proven=dict(right,first_line_x=source_head[0])
            if not _source_continuation(left,proven,records[b]['blocks'],tail,head):continue
            art=body[at:];del body[at:]
            leading_fragments=current['body'][:opening]
            del current['body'][:opening]
            current['body'][1:1]=art+leading_fragments
            if opening:openings[b]=proven
    return openings


def _native_removal_offsets(runs,start,end,remaining):
    """Project native navigation through this exact physical character removal.

    Keep source-instance identity even when identical title words occur later
    in the body. Text sequence matching would choose an arbitrary occurrence.
    Navigation uses the renderer's whitespace-normalized character stream.
    """
    from bisect import bisect_right
    values=[run[1] if run[0] in ('t','raised','glyph') else '[%s]'%run[1] for run in runs]
    raw=''.join(values);offset=0;render_offset=0;boundaries={}
    for run,value in zip(runs,values):
        for boundary in (start,end):
            if boundary not in boundaries and offset<=boundary<=offset+len(run[1]):
                boundaries[boundary]=(render_offset+len(value) if boundary==offset+len(run[1])
                    else render_offset+boundary-offset)
        offset+=len(run[1]);render_offset+=len(value)
    a,z=boundaries[start],boundaries[end]
    def characters(text):
        result=[(' ' if len(m.group())>1 else m.group(),m.start(),m.end())
                for m in re.finditer(r'[^\S\n]{2,}|[\s\S]',text)]
        while result and result[0][0].isspace():result.pop(0)
        while result and result[-1][0].isspace():result.pop()
        return result
    old=characters(raw);new=characters(raw[:a]+raw[z:])
    assert ''.join(x[0] for x in old)==assemble.plain_text(runs)
    assert ''.join(x[0] for x in new)==assemble.plain_text(remaining)
    ends=[x[2] for x in new];mapping=[]
    for i in range(len(old)+1):
        position=old[i][1] if i<len(old) else old[-1][2] if old else 0
        projected=position if position<a else a if position<z else position-(z-a)
        mapping.append(bisect_right(ends,projected))
    return mapping


def _native_margin_runs(doc,book,numbers):
    """Remove source-proven marginal rows from stale native prepared runs.

    A cached element can carry a header's box but concatenate its following
    paragraph into the same run. Only the uniquely mapped header characters
    leave the run; the element and its paragraph survive. Ambiguity preserves
    the reading rather than authorizing a substring deletion.
    """
    import statistics
    rows={};repeated={};families={};token_pattern=re.compile(r'[^\W\d_]+|\d+',re.UNICODE)
    controller=unit_recovery.current()
    evidence=getattr(controller,'native_margin_source_evidence',None)
    if evidence is not None and not set(numbers)<=set(evidence['pages']):
        evidence=None
    tokens=lambda text:[m.group().casefold() for m in token_pattern.finditer(text)]
    def authored_region(n,b):
        regions=[f['bbox'] for f in book.figures+book.artwork if f['pno']==n]
        regions.extend(e.bbox for e in book.pages.get(n,[]) if e.kind in ('h','fig','table'))
        regions.extend(note.bbox for note in book.notes if note.pno==n)
        return any(box[0]<b[2] and box[2]>b[0] and box[1]<b[3] and box[3]>b[1]
                   for box in regions)
    for n in numbers if evidence is None else ():
        with unit_recovery.guard(n,'native_margin_ownership'):
            page=doc[n]
            if page.rotation:continue  # Cached/native frames must agree geometrically.
            r=page.rect;lines=[]
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',[]):
                    spans=[s for s in line['spans'] if s.get('text','').strip()]
                    if spans:lines.append(dict(text=''.join(s['text'] for s in spans),bbox=list(line['bbox']),
                        size=statistics.median(s['size'] for s in spans),
                        fonts=tuple(sorted({s['font'] for s in spans}))))
            ordinary=[x['size'] for x in lines if r.height*.1<x['bbox'][1]<r.height*.88
                      and len(tokens(x['text']))>=4]
            size=statistics.median(ordinary) if ordinary else None;margins=[]
            for line in sorted(lines,key=lambda x:(x['bbox'][1],x['bbox'][0])):
                b=line['bbox']
                if not (b[3]<r.height*.13 or b[1]>r.height*.92):continue
                if (size is not None and line['size']>size) or len(tokens(line['text']))>12:continue
                if authored_region(n,b):continue
                if any(note.pno==n and note.bbox[0]<b[2] and note.bbox[2]>b[0]
                       and note.bbox[1]<b[3] and note.bbox[3]>b[1] for note in book.notes):continue
                # A detached folio and title can be separate PDF spans on one row.
                if margins and abs(margins[-1]['bbox'][1]-b[1])<line['size']*.25:
                    prior=margins[-1]
                    numeric=lambda text:bool(re.fullmatch(r'\s*\d+\s*',text))
                    prior['detached_folio']=prior['detached_folio'] or numeric(line['text'])!=numeric(prior['text'])
                    prior['text']+=' '+line['text'];prior['bbox']=vision_reading.union([prior['bbox'],b])
                    prior['fonts']=tuple(sorted(set(prior['fonts'])|set(line['fonts'])))
                else:
                    margins.append(dict(copy.deepcopy(line),detached_folio=False,
                        small=size is not None and line['size']<=size*.97,
                        local_body_size=size,strict_margin=b[3]<r.height*.1 or b[1]>r.height*.92))
            margins=[row for row in margins if not authored_region(n,row['bbox'])]
            rows[n]=margins
            source_words=page.get_text('words') if margins else []
            for row in margins:
                # PDF line/span grouping is not physical ownership. A folio may
                # share a text span with a distant running title, or the cache may
                # already have omitted its separate numeric carrier.
                b=row['bbox']
                words=sorted([w for w in source_words if b[0]<=((w[0]+w[2])/2)<=b[2]
                              and b[1]<=((w[1]+w[3])/2)<=b[3]],key=lambda w:w[0])
                if len(words)>1:
                    for edge,neighbor,title_words,left in ((words[0],words[1],words[1:],True),
                                                           (words[-1],words[-2],words[:-1],False)):
                        gap=neighbor[0]-edge[2] if left else edge[0]-neighbor[2]
                        if re.fullmatch(r'\d+',edge[4]) and gap>=row['size']*3 and any(
                                re.search(r'[^\W\d_]',w[4]) for w in title_words):
                            row['detached_folio']=True
                            row['title_text']=' '.join(w[4] for w in title_words)
                            row['title_bbox']=vision_reading.union([list(w[:4]) for w in title_words])
                            row['folio_bbox']=list(edge[:4])
                            row['family']=(row['fonts'],'left' if left else 'right',
                                'top' if b[3]<r.height*.13 else 'bottom',int(edge[4])-n)
                            if size is not None:
                                families.setdefault(row['family'],{})[n]=row['size']
                            break
                key=re.sub(r'[\W\d_]+','',row['text']).casefold()
                row['key']=(key,row['fonts'],round(row['size'],2)) if key else None
                if key:repeated.setdefault(row['key'],set()).add(n)
    if evidence is None:
        # Capture only source-derived ownership evidence, before touching runs.
        # A later page fallback changes publication membership, not the source
        # corroboration that proved furniture on its healthy neighbours.
        evidence=dict(pages=list(numbers),rows=copy.deepcopy(rows),
                      repeated=copy.deepcopy(repeated),families=copy.deepcopy(families))
        if controller is not None and numbers:
            controller.native_margin_source_evidence=evidence
    rows={n:copy.deepcopy(evidence['rows'][n]) for n in numbers if n in evidence['rows']}
    repeated=copy.deepcopy(evidence['repeated']);families=copy.deepcopy(evidence['families'])
    repairs=[]
    for n,source_rows in rows.items():
        with unit_recovery.guard(n,'native_margin_repair'):
            elements=book.pages.get(n,[])
            for row in source_rows:
                family=families.get(row.get('family'),{})
                # Running titles can change at every chapter. Their physical font,
                # folio side and printed-page offset are stable source evidence.
                # A header-only leaf inherits this bounded family, never an assumed
                # book-wide body font. A modest smaller member must share all the
                # other physical evidence and cannot enlarge the anchor typography.
                family_size=statistics.median(family.values()) if len(family)>=3 else None
                family_proven=(family_size is not None and family_size*.85<=row['size']<=family_size)
                repeated_title=row['key'] and len(repeated.get(row['key'],()))>=3
                if row['key'] and not (repeated_title or family_proven):continue
                if not row['key'] and not re.fullmatch(r'\s*\d+\s*',row['text']):continue
                # A shifted or equal-size header needs a repeated source style and
                # a physically detached folio on its row. Local table typography
                # alone cannot establish ownership of an authored section title.
                if not (row['small'] and row['strict_margin']) and not (
                        row['key'] and row['detached_folio'] and (repeated_title or family_proven)):continue
                options=[(row['text'],row['bbox'],'unique_native_marginal_row_and_carrier')]
                if row.get('title_text'):
                    options.append((row['title_text'],row['title_bbox'],
                                    'unique_native_marginal_title_with_detached_folio'))
                for source_text,b,method in options:
                    wanted=tokens(source_text);matches=[]
                    for element in elements:
                        if element.kind!='p':continue
                        box=element.bbox
                        if not (box[0]<b[2] and box[2]>b[0] and box[1]<b[3] and box[3]>b[1]):continue
                        raw=''.join(run[1] for run in element.runs)
                        ts=list(token_pattern.finditer(raw));values=[m.group().casefold() for m in ts]
                        for i in range(len(values)-len(wanted)+1):
                            if values[i:i+len(wanted)]==wanted:
                                matches.append((element,ts[i].start(),ts[i+len(wanted)-1].end(),raw))
                    # Prefer the full physical row. An ambiguous compound match
                    # cannot authorize a narrower deletion. Never fall back to a
                    # bare folio, which might occur later in authored body prose.
                    if matches:break
                if len(matches)!=1:continue
                element,start,end,raw=matches[0];offset=0;safe=True
                for run in element.runs:
                    if offset<end and offset+len(run[1])>start and run[0]!='t':safe=False
                    offset+=len(run[1])
                if not safe:continue
                old=copy.deepcopy(element.runs);remaining=copy.deepcopy(old);offset=0
                for run in remaining:
                    length=len(run[1]);a=max(0,start-offset);z=min(length,end-offset)
                    if a<z:run[1]=run[1][:a]+run[1][z:]
                    offset+=length
                remaining=assemble.tidy(remaining)
                mapping=_native_removal_offsets(old,start,end,remaining)
                old_text=assemble.plain_text(old);new_text=assemble.plain_text(remaining)
                element_index=next(i for i,e in enumerate(elements) if e is element)
                source_links=[link for link in book.source_navigation if link['status']=='resolved'
                    and link['pno']==n and link['source_element']==element_index
                    and link.get('source_item') is None]
                # Authored link labels cannot be consumed as disposable furniture.
                # Their original source range must survive exactly, in order.
                if any(old_text[link['source_offset']:link['source_offset']+link['source_extent']]!=
                       new_text[mapping[link['source_offset']]:mapping[link['source_offset']+link['source_extent']]]
                       for link in source_links):continue
                navigation=[]
                for link in source_links:
                    a=link['source_offset'];z=a+link['source_extent']
                    link['source_offset']=mapping[a];link['source_extent']=mapping[z]-mapping[a]
                    navigation.append(dict(id=link['id'],source_old=[a,z],source_new=[mapping[a],mapping[z]]))
                for link in book.source_navigation:
                    if (link['status']=='resolved' and link['dest_page']==n and link['dest_element']==element_index
                            and link.get('dest_item') is None and link.get('dest_note') is None):
                        a=link['dest_offset'];link['dest_offset']=mapping[a]
                        navigation.append(dict(id=link['id'],destination_old=a,destination_new=mapping[a]))
                element.runs=remaining
                repairs.append(dict(page=n,bbox=b,source_text=source_text,source_tokens=wanted,
                    character_range=[start,end],method=method,
                    navigation=navigation,
                    previous_runs_sha256=hashlib.sha256(json.dumps(old,ensure_ascii=False).encode()).hexdigest(),
                    published_runs_sha256=hashlib.sha256(json.dumps(element.runs,ensure_ascii=False).encode()).hexdigest()))
        # Empty carriers retain their original indices and arrival anchors.
        # page_fragment already omits empty unreferenced visible paragraphs.
    return repairs


def _insert_source_units(page,report,original,blocked,display,package,images):
    """Replace named failed units without changing their healthy neighbours."""
    from .unit_source import source_unit
    alignment=report['blocks'];report.setdefault('degraded_units',[])
    original_blocks=original['blocks']
    for index,block in enumerate(original_blocks):
        bid=block['id']
        if bid not in blocked:continue
        case=blocked[bid];role=block['role']
        if role in ('figure','table'):
            rendered=next((c for c in report.get('source_figure_contexts',[]) if c.get('block')==bid),None)
            if rendered:
                unit=dict(rendered,page=page['pno'],block=bid,unit_kind=role,
                    stage=case['stage'],reason=case['reason'],status='DEGRADED_SOURCE_CONTEXT')
                case.update(unit);report['degraded_units'].append(unit)
                continue
        # Unproved geometry is never used to clip the source fallback.
        fragment,asides,unit=source_unit(display,page['pno'],bid,role,package,images,case['reason'])
        unit['stage']=case['stage'];case.update(unit)
        following={b['id'] for b in original_blocks[index+1:]}
        at=next((i for i,b in enumerate(alignment) if b['id'] in following),len(alignment))
        body_at=sum(b['role'] not in ('footnote','running_head','folio') for b in alignment[:at])
        if fragment:page['body'].insert(body_at,fragment)
        page['asides'].extend(asides)
        alignment[at:at]=unit['blocks']
        report['degraded_units'].append(unit)


def _complete_note_alias_body_marker(display, optical, glyph, label, territory):
    """Veto a partial original marker before admitting an ID-only repair."""
    lexical=[w for w in optical if re.search(r'[A-Za-z]',w['text'])]
    before=[]
    for word in lexical:
        a=word['bbox'];height=max(1,a[3]-a[1])
        if (a[0]<glyph[0] and a[3]>glyph[3]+.5
                and min(a[3],glyph[3])-max(a[1],glyph[1])>-height*.6
                and -height*1.5<=glyph[0]-a[2]<=height*1.5
                and a[2]-glyph[2]<=height*.2):before.append(word)
    if not before:return False
    nearest=max(w['bbox'][0]for w in before)
    before=[w for w in before if abs(w['bbox'][0]-nearest)<.1]
    if len(before)!=1:return False
    a=before[0]['bbox'];height=max(1,a[3]-a[1])
    after=[w for w in lexical if w['bbox'][0]>=glyph[2]-.4
        and min(w['bbox'][3],a[3])-max(w['bbox'][1],a[1])>height*.3
        and w['bbox'][0]-glyph[2]<=height*4]
    if after:
        first=min(w['bbox'][0]for w in after);after=[w for w in after if abs(w['bbox'][0]-first)<.1]
        if len(after)!=1:return False
        b=after[0]['bbox'];right=b[0]
    else:b=None;right=min(territory[2],glyph[2]+height*1.5)
    group=_original_marker_slot_components(display,a,b,glyph,right)
    return bool(group and group['horizontal_components']==(len(label)if label.isdigit()else 1)
        and all(glyph[j]-.4<=group['bbox'][j]for j in(0,1))
        and all(group['bbox'][j]<=glyph[j]+.4 for j in(2,3)))


def _complete_note_opening_characters(display, word, label):
    """Original component cardinality vetoes an incomplete optical prefix.

    Parentheses or stops remain part of their original word. No component is
    named here and no marker value is manufactured from a note catalogue.
    """
    from . import vision_pixels
    text=word['text'].strip();box=word['bbox']
    if not re.fullmatch(r'[().\[\]0-9*†‡]+',text):return False
    pix=display.pixmap(box,scale=12,max_pixels=200_000)
    picture,components=vision_pixels._word_ink_components(pix)
    groups=[]
    for c in sorted(components,key=lambda c:c[0]):
        if groups and c[0]<=groups[-1][1]+1:groups[-1][1]=max(groups[-1][1],c[2])
        else:groups.append([c[0],c[2]])
    return len(groups)==len(label)+len(text)-len(text.strip('().[]'))


def _source_note_id_repair(answer,display,optical,cache):
    """Prove an alias repair from local original glyphs and note openings."""
    from . import vision_pixels
    from PIL import Image,ImageOps
    import io
    notes=[b for b in answer.get('blocks',[]) if b.get('role')=='footnote']
    refs=[(b,m) for b in answer.get('blocks',[]) if b.get('role') in ('body','caption','other')
          for m in re.finditer(r'<n:([^>]+)>',b.get('text',''))]
    known={b['id'] for b in answer.get('blocks',[])}
    if not refs or all(m[1] in known for _,m in refs):return None
    if len(refs)!=len(notes) or len({m[1] for _,m in refs})!=len(refs):return None
    labels=[b['marker'].strip().strip('().[] ') for b in notes]
    if len(set(labels))!=len(labels) or not all(re.fullmatch(r'\d{1,4}|[*†‡]',x) for x in labels):return None
    mapping={m[1]:note['id'] for (_,m),note in zip(refs,notes)}
    temporary=copy.deepcopy(answer)
    for b in temporary['blocks']:
        b['text']=re.sub(r'<n:([^>]+)>',lambda m:'<n:'+mapping.get(m[1],m[1])+'>',b['text'])
    vision_reading.validate(temporary,defer_geometry=True,defer_notes=True)
    regions={};body_owners={}
    bound,_=bind_geometry(temporary,optical,display,regions,body_owners)
    by_id={b['id']:b for b in bound['blocks']}
    evidence=dict(source_sha256=hashlib.sha256(display.roi_jpeg(display.rect,scale=2,quality=90)).hexdigest(),references=[],notes=[])
    local_labels={label:[(display.pno,b['id'])] for label,b in zip(labels,notes)}
    grouped={}
    for (b,m),note,label in zip(refs,notes,labels):grouped.setdefault(b['id'],[]).append((m,note,label))
    physical={}
    for bid,items in grouped.items():
        b=by_id[bid];proposals={m[1]:(note['id'],label) for m,note,label in items}
        probe=re.sub(r'\s*<n:([^>]+)>',lambda m:proposals[m[1]][1]+' <n:'+proposals[m[1]][0]+'>',answer['blocks'][next(i for i,x in enumerate(answer['blocks']) if x['id']==bid)]['text'])
        tokens=re.findall(r'<n:[^>]+>|\S+',re.sub(r'(<n:[^>]+>)',r' \1 ',probe))
        owned=_owned_block_words(b,bound,optical,display,regions,ordered=True)
        box=vision_reading.union([w['bbox'] for w in owned]) if owned else block_rect(b,display)
        context={'source_words':optical,'marker_reader':None,'marker_receipts':{}}
        actual=_literal_note_refs(tokens,_adjacent_source_note_glyphs(owned,optical),display,box,cache,local_labels,display.pno,context)
        for m,note,label in items:
            instances=[list(rect) for key,rects in getattr(actual,'source_instances',{}).items()
                       if key[3]==(display.pno,note['id']) and key[4]==label for rect in rects]
            unique={tuple(rect) for rect in instances}
            if len(unique)!=1:return None
            physical[note['id']]=list(unique.pop())
            if not _complete_note_alias_body_marker(display,owned,physical[note['id']],label,box):return None
            evidence['references'].append(dict(block=bid,offset=m.start(),label=label,
                bbox=physical[note['id']],source_proved=True,method='local_original_marker_between_response_lexical_anchors'))
    def silhouette(box):
        pix=display.pixmap(box,scale=12,max_pixels=200_000)
        image=Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
        ink=image.point(lambda v:255 if v<180 else 0);rect=ink.getbbox()
        return ink.crop(rect) if rect else None
    for note,label in zip(notes,labels):
        b=by_id[note['id']];owned=_owned_block_words(b,bound,optical,display,regions,ordered=True)
        if not owned:return None
        first=owned[0];box=first['bbox'];height=box[3]-box[1]
        prefixes=[w for w in optical if
            abs((w['bbox'][1]+w['bbox'][3]-box[1]-box[3])/2)<height*.65 and
            0<=box[0]-w['bbox'][2]<3*height and
            len(w['text'])<=4 and not re.search(r'[A-Za-z]',w['text'])]
        if first['text'].strip().strip('().[] ')==label:
            prefixes.append(first)
        proved=[]
        body_ink=silhouette(physical[note['id']])
        for word in prefixes:
            if not _complete_note_opening_characters(display,word,label):continue
            candidate=vision_pixels.ink_bounds(display,word['bbox'])
            if not candidate:continue
            observed=word['text'].strip().strip('().[] ')
            if observed==label and word.get('confidence',0)>=80:
                proved.append((candidate,'coherent_note_opening_original_optical_label'));continue
            note_ink=silhouette(candidate)
            if body_ink is None or note_ink is None:continue
            ratio=(body_ink.width/body_ink.height)/(note_ink.width/note_ink.height)
            if not .8<=ratio<=1.25:continue
            a=body_ink.resize((64,64),Image.Resampling.NEAREST);z=note_ink.resize((64,64),Image.Resampling.NEAREST)
            aa=list(a.getdata());zz=list(z.getdata());union=sum(bool(x or y) for x,y in zip(aa,zz))
            overlap=sum(bool(x and y) for x,y in zip(aa,zz))
            if union and overlap/union>=.9:proved.append((candidate,'same_original_glyph_silhouette_at_coherent_note_opening'))
        if len(proved)!=1:return None
        evidence['notes'].append(dict(id=note['id'],label=label,bbox=list(proved[0][0]),
                                     source_proved=True,method=proved[0][1]))
    # Match the actual response order, including multiple body blocks.
    evidence['references'].sort(key=lambda v:next(i for i,b in enumerate(answer['blocks']) if b['id']==v['block'])*1_000_000+v['offset'])
    return vision_reading.repair_note_ids(answer,evidence)


def _compiled_text_quality(pages,records,numbers,stage='compiled_publication_text_quality'):
    """Sweep all reader-facing channels and retain only implicated owners."""
    sweep=text_quality.sweep_publication_pages(pages,records,numbers)
    controller=unit_recovery.current()
    failures=[]
    for page in pages:
        n=page['pno'];quality=sweep['pages'][n]
        records[n]['compiled_text_quality']=quality
        if quality['passed']:continue
        fragments=page['body']+page['asides']
        parts=[];owners={}
        for fragment in fragments:
            for part in text_quality.publication_parts(dict(body=[fragment],asides=[])):
                # Preserve every whitespace word, including one joined across
                # source spans. Distinct neighbouring words keep their own
                # contributing owners rather than inheriting the paragraph's
                # union and sacrificing a clean source page beside bad text.
                groups=[]
                for token in re.finditer(r'\S+',part['text']):
                    explicit=[unit for span in part.get('spans',[])
                              if span['start']<token.end() and token.start()<span['end']
                              for unit in span['owners']]
                    owned=list({(u['page'],u.get('block')):u for u in explicit}.values())
                    if not owned:
                        owned=(controller.fragment_owners(fragment) if controller else [dict(page=n,block=None)])
                    if controller:
                        controller.check_units(owned)
                        for unit in owned:
                            if unit.get('block') is not None and unit['block'] not in {
                                    b['id'] for b in (records[unit['page']].get('blocks',[])+
                                                     records[unit['page']].get('native_note_adjudication',[]))}:
                                raise unit_recovery.SourceIntegrityError('Publication text has an unregistered source unit')
                    key=tuple(sorted((u['page'],u.get('block') or '') for u in owned))
                    if groups and groups[-1][0]==key:
                        groups[-1][2]=token.end()
                    else:
                        groups.append([key,token.start(),token.end(),owned])
                for _,start,end,owned in groups:
                    index=len(parts)
                    parts.append((index,part['text'][start:end],part['kind']))
                    owners[index]=owned
        local=text_quality.inspect_partitioned(parts)
        for index in local['rejected_units']:
            failures.extend(owners[index])
        if not local['rejected_units']:
            failures.append(dict(page=n,block=None))
    if failures:
        unique=list({(unit['page'],unit.get('block')):unit for unit in failures}.values())
        # A failed joined fragment may own the complete reading of its source
        # pages. Only those pages with no independently safe unit left need a
        # whole-page fallback; partial pages keep their unaffected fragments.
        if stage=='compiled_publication_text_quality':
            whole=set()
            for n in {unit['page'] for unit in unique}:
                ids={b['id'] for b in (records[n].get('blocks',[])+records[n].get('native_note_adjudication',[]))
                     if b['role'] not in ('running_head','folio') and not b.get('degraded_source_context')}
                bad={unit.get('block') for unit in unique if unit['page']==n}
                if None in bad or ids and ids<=bad:whole.add(n)
            unique=[unit for unit in unique if unit['page'] not in whole]+[dict(page=n,block=None) for n in sorted(whole)]
        if controller:
            controller.check_units(unique)
            raise unit_recovery.UnitFailure(unique,stage,ValueError('Joined reading has unresolved text quality'))
        raise ValueError('Joined reading has unresolved text quality')
    return sweep


def _prepare_scanned_page(doc,n,reader,controller,provenance,original_frames):
    """Checkpoint source geometry before any mutable book-level assembly."""
    boundary_receipts={}
    if controller:controller.verify_source(doc,n)
    saved=controller.load_prepared(n) if controller else None
    if saved is not None:
        prov=(provenance or {}).get(n,{})
        display=SourceDisplay(doc,n,dict(prov,layer='ocr' if prov.get('space')=='reading' else 'native',
            original_frame=(original_frames or {}).get(n)))
        display=local_reading.reading_display(display,saved['reading'])
        optical=saved['optical']
        controller.source_repairs[n]=lambda value:_source_note_id_repair(value,display,optical,reader.cache.parent/'pixel-guard-cache')
        controller.register_regions(n,saved['regions'],
            _omission_coverage(saved['answer'],display,saved['regions'],saved['optical']))
        receipts=saved.pop('boundary_receipts')
        return dict(display=display,**saved),receipts
    with unit_recovery.guard(n,'primary_source_render'):
        prov=(provenance or {}).get(n,{})
        display=SourceDisplay(doc,n,dict(prov,layer='ocr' if prov.get('space')=='reading' else 'native',
            original_frame=(original_frames or {}).get(n)))
        if controller and n in controller.images:
            image=Path(controller.images[n]['path']).read_bytes()
        else:
            pix=display.pixmap(scale=2,max_pixels=2_500_000)
            image=pix.tobytes('jpg',jpg_quality=90)
        if controller:controller.register_image(n,image)
    from . import vision_pixels
    with unit_recovery.guard(n,'optical_evidence'):
        optical=vision_pixels.words(display,reader.cache.parent/'pixel-guard-cache')
    with unit_recovery.guard(n,'reading'):
        reading=(controller.read(n,image,lambda value:_source_note_id_repair(value,display,optical,reader.cache.parent/'pixel-guard-cache'),
                    local_read=lambda reason:local_reading.read_oriented(display,controller.staging/'local-ocr-cache',reason))
                 if controller else reader.read(image))
        upright=local_reading.reading_display(display,reading)
        if upright is not display:
            display=upright
            optical=vision_pixels.words(display,reader.cache.parent/'pixel-guard-cache')
            controller.source_repairs[n]=lambda value:_source_note_id_repair(value,display,optical,reader.cache.parent/'pixel-guard-cache')
    regions={};body_owners={}
    selected=controller.filter_answer(n,reading['answer']) if controller else reading['answer']
    with unit_recovery.guard(n,'geometry'):
        answer,repairs=bind_geometry(selected,optical,display,regions,body_owners)
    with unit_recovery.guard(n,'note_boundaries'):
        answer,boundary_repairs=_source_note_boundaries(answer,optical,display,regions,body_owners,
            getattr(reader,'read_marker',None),boundary_receipts,n)
        repairs.extend(boundary_repairs)
        for opening in _unpartitioned_note_openings(answer,optical,display,regions,body_owners):
            with unit_recovery.guard(n,'note_boundaries',block=opening['block']):
                raise ValueError('Interior printed note opening requires a verified source partition')
    if controller:
        controller.register_regions(n,regions,_omission_coverage(answer,display,regions,optical))
        controller.admit_answer(n,answer)
    with unit_recovery.guard(n,'reading_text_quality'):
        units=[(b['id'],b['text']) for b in answer['blocks'] if b['role'] not in ('figure','table')]
        quality=text_quality.inspect_partitioned(units)
        failed=list(quality['rejected_units']);missing_scripts=set();weak_local=[]
        for unit in reading.get('local_ocr_units',[]):
            # Very weak recognition is retained as source context for just its
            # paragraph. A high engine score never removes uncertainty marks.
            if (unit['id'] in {b['id'] for b in answer['blocks']}
                    and unit['mean_confidence'] < 75 and unit['id'] not in failed):
                failed.append(unit['id'])
                weak_local.append(unit['id'])
        for block in answer['blocks']:
            if block['role'] in ('figure','table') or not visible_scripts.source_text(display):continue
            boxes=regions.get(block['id'])
            if boxes is None:boxes=[block_rect(block,display)]
            missing=text_quality.script_mismatches(visible_scripts.source_text(display,boxes),block['text'])
            missing_scripts.update(missing)
            if missing and block['id'] not in failed:failed.append(block['id'])
        if failed:
            reason='Pixel reading has unresolved text quality: '+', '.join(quality['reasons']+
                ['local_ocr_mean_confidence_below_75' for _ in weak_local[:1]]+
                ['missing_source_script_'+v for v in sorted(missing_scripts)])
            if controller:
                owned=([dict(page=n,block=None)] if set(failed)=={b['id'] for b in answer['blocks']}
                       else [dict(page=n,block=bid) for bid in failed])
                raise unit_recovery.UnitFailure(owned,
                    'reading_text_quality',ValueError(reason))
            raise ValueError(reason)
    prepared=dict(reading=reading,optical=optical,regions=regions,
        body_owners=body_owners,answer=answer,geometry_repairs=repairs,
        source_words=[])
    with unit_recovery.guard(n,'source_words'):
        prepared['source_words']=layer_words(doc,n,display)
    prepared['boundary_receipts']=boundary_receipts
    if controller:controller.store_prepared(n,prepared)
    prepared.pop('boundary_receipts')
    return dict(display=display,**prepared),boundary_receipts


def _convert_attempt(doc,out_path,reader,*,page_numbers=None,provenance=None,progress=None,native_prepared=None,original_frames=None,metadata=None):
    """Build an EPUB with explicit source-word disclosures and source-linked notes."""
    numbers=sorted(page_numbers if page_numbers is not None else range(len(doc)))
    # Scanned pages need their original rendering, not a native rawdict image
    # extraction. Some PDF image decoders change subsequent rendering after
    # extracting image metadata, which also invalidates the reading cache.
    # clean_native reads native spans only after ruling out scanned pages.
    controller=unit_recovery.current()
    if controller:
        for n in numbers:controller.verify_source(doc,n)
    source_pages=controller.source_pages() if controller else set()
    native=[]
    for n in numbers:
        if n in source_pages:continue
        with unit_recovery.guard(n,'page_classification'):
            if controller and n in controller.native_classification:
                is_native=controller.native_classification[n]
            else:
                is_native=clean_native(doc[n])
                if controller:controller.native_classification[n]=is_native
            if is_native:native.append(n)
    scanned=[n for n in numbers if n not in native and n not in source_pages]
    prepared=None
    if native:
        source_prepared=native_prepared
        if source_prepared is None:
            source_prepared=getattr(controller,'native_prepared',None)
            if source_prepared is None:
                source_prepared=pipeline.run(doc,page_numbers=native,recovery_opts={'mode':'off'})
                if controller:controller.native_prepared=source_prepared
        prepared=copy.deepcopy(source_prepared)
    book=prepared.book if prepared else assemble.Book()
    # Filter cached native preparation to the actual selected native pages.
    book.pages={n:els for n,els in book.pages.items() if n in native}
    book.notes=[note for note in book.notes if note.pno in native]
    book.figures=[f for f in book.figures if f['pno'] in native]
    book.artwork=[f for f in book.artwork if f['pno'] in native]
    native_publication_repairs,native_vector_contexts=_native_publication_ownership(
        doc,book,native,raw_pages=getattr(prepared,'raw_pages',None))
    native_margin_repairs=_native_margin_runs(doc,book,native)
    native_caption_repairs=_native_caption_ownership(doc,book,native)
    from . import image_budget
    package=unit_recovery.package(out_path,source_bytes=image_budget.source_size(doc));pages=[];records={};images={};html_native={};native_unit_reports={}
    try:
        for n in native:
            with unit_recovery.guard(n,'native_page_markup'):
                if controller:
                    html_native[n],native_unit_reports[n]=build_epub.recovery_page_fragment(
                        book,n,SourceDisplay(doc,n),package,images)
                else:html_native[n]=build_epub.page_fragment(book,n)
        from .native_text import package_glyphs,image_name,note_descriptor
        package_glyphs(book,html_native,doc,package)
        native_evidence=build_epub._original_evidence(book,html_native,doc,package)
        for pno,record in native_evidence.items():
            images[record['full']]=dict(page=pno,bbox=record['reading_rect'],kind='native_source_reference')
            for detail in record['details']:
                images[detail['src']]=dict(page=pno,bbox=detail.get('reading_bbox',record['reading_rect']),kind='native_source_reference')
        native_fragments=[build_epub.Chapter(index=0,title='',blocks=list(html_native.values()))]
        native_figures={}
        missing,blanks=build_epub._figure_images(native_fragments,doc,book,package,
            owned_images=set(package.images)|set(package.aliases),figure_presentation=native_figures)
        native_cases,native_asides=_recover_native_figures(missing+blanks,html_native,book,
            doc,package,images,native_evidence)
        for src,figure in native_figures.items():
            if src in missing or src in blanks:continue
            images[src]=dict(page=figure['pno'],bbox=figure['bbox'],kind=figure.get('found','figure'))
        glyphs={}
        for pno,elements in book.pages.items():
            for index,element in enumerate(elements):
                bid='native-element-'+str(index)
                if controller and bid in controller.blocked(pno):continue
                with unit_recovery.guard(pno,'native_glyph_metadata',block=bid):
                    for run in element.runs:
                        if run[0]=='glyph':
                            src=image_name(run[2])
                            if src in package.images:glyphs[src]=(run[2],pno,bid)
        note_indices={}
        for note in book.notes:
            index=note_indices.get(note.pno,0);note_indices[note.pno]=index+1
            bid='native-note-'+str(index)
            if controller and bid in controller.blocked(note.pno):continue
            with unit_recovery.guard(note.pno,'native_note_glyph_metadata',block=bid):
                if note.pno in html_native and note.uses_source_image:
                    runs=getattr(note,'glyph_runs',None)
                    values=[r[2] for r in runs if r[0]=='glyph'] if runs else [note_descriptor(note)]
                    for record in values:
                        src=image_name(record)
                        if src in package.images:glyphs[src]=(record,note.pno,bid)
        for src,(record,pno,bid) in glyphs.items():
            with unit_recovery.guard(pno,'native_glyph_metadata',block=bid):
                display=SourceDisplay(doc,record['page'])
                images[src]=dict(page=record['page'],bbox=list(display.reading_rect(record['bbox'])),kind='glyph')
        native_pages={p['pno']:p for p in build_epub._page_blocks(html_native)}
        for pno,asides in native_asides.items():native_pages[pno]['asides'].extend(asides)
        for case in native_vector_contexts:
            pno=case['page']
            with unit_recovery.guard(pno,'native_vector_context',block=case['block']):
                from .unit_source import source_unit
                fragment,asides,unit=source_unit(SourceDisplay(doc,pno),pno,case['block'],'other',
                    package,images,case['reason'],bbox=case['seed_bbox'])
                control=re.search(r'<a\b.*?</a>',fragment,re.S)
                if control is None:raise ValueError('Missing original vector context control')
                native_pages[pno]['body'].append('<div class="source-disclosure source-evidence-notice">'+control[0]+'</div>')
                native_pages[pno]['asides'].extend(asides)
                case.update(unit,unit_kind='figure');native_cases.append(case)
        scans={};boundary_receipts={}
        for n in scanned:
            scans[n],receipts=_prepare_scanned_page(doc,n,reader,controller,provenance,original_frames)
            scans[n]['pixel_cache']=reader.cache.parent/'pixel-guard-cache'
            boundary_receipts.update(receipts)
        # Resolve book-level roles before omitted-line recovery so an oversized
        # model illustration cannot swallow either live prose or a note tail.
        _note_ownership(scans)
        # Collect every proved source-owned separation failure before recovery.
        # A chart-heavy book needs one shared reread round, not a complete
        # geometry pass for each individual failed object.
        unproved_art_cases=[]
        for n,s in scans.items():
            with unit_recovery.guard(n,'figures_and_tables'):
                blocked_art={b['id'] for b in s['answer']['blocks'] if controller and b['id'] in controller.blocked(n) and b['role'] in ('figure','table')}
                figure_answer=dict(s['answer'],blocks=[b for b in s['answer']['blocks'] if b['id'] not in blocked_art])
                s['figure_repairs']=_bind_figures(figure_answer,s['optical'],s['display'],s['regions'])
                for bid in blocked_art:
                    block=next(b for b in s['answer']['blocks'] if b['id']==bid)
                    case=controller.blocked(n)[bid]
                    s['figure_repairs'].append(dict(block=bid,degraded=True,
                        seed_bbox=list(block_rect(block,s['display'])),reason=case['reason'],method='unit_recovery_source_context'))
                if controller:
                    source_failure_reason='; '.join(c.get('reason','Figure separation is unproved')
                        for c in s['figure_repairs'] if c.get('degraded'))
                    cases=[dict(page=n,block=c['block'],source_failure_reason=source_failure_reason)
                        for c in s['figure_repairs'] if c.get('degraded') and c['block'] not in blocked_art]
                    if cases:
                        unproved_art_cases.extend(cases)
                        continue
            with unit_recovery.guard(n,'source_word_coverage'):
                # Keep admitted recovery ownership in the same map compilation
                # consumes; coverage_regions includes reserved degraded ink.
                if controller:s['regions']=controller.coverage_regions(n,s['regions'])
                s['answer'],s['recovered']=cover_missing_lines(s['answer'],s['optical'],s['source_words'],
                    s['display'],s['regions'],s['body_owners'])
                if controller:
                    # Newly recovered units already have source footprints.
                    # Keep those identities before the stricter paragraph
                    # partition can reject one, without publishing its text.
                    controller.register_regions(n,s['regions'],
                        _omission_coverage(s['answer'],s['display'],s['regions'],s['optical']))
                    controller.admit_answer(n,s['answer'])
                    s['answer']=controller.filter_answer(n,s['answer'])
                s['answer'],s['paragraph_repairs']=_source_paragraph_boundaries(
                    s['answer'],s['optical'],s['display'],s['regions'],s['body_owners'])
                if controller:
                    controller.register_regions(n,s['regions'],
                        _omission_coverage(s['answer'],s['display'],s['regions'],s['optical']))
                    controller.admit_answer(n,s['answer'])
                    s['answer']=controller.filter_answer(n,s['answer'])
        if unproved_art_cases:
            raise unit_recovery.UnitFailure(unproved_art_cases,'figure_separation',
                ValueError('; '.join(dict.fromkeys(c['source_failure_reason'] for c in unproved_art_cases))))
        owners,catalogue,labels=_note_ownership(scans)
        note_context={'owners':owners,'catalogue':catalogue,'labels':labels,'refs':{},
                      'cache':reader.cache.parent/'pixel-guard-cache',
                      'marker_reader':getattr(reader,'read_marker',None),'marker_receipts':boundary_receipts}
        compilation_failures=[]
        for n in numbers:
            if n in source_pages:
                from .unit_source import source_unit
                prov=(provenance or {}).get(n,{})
                display=SourceDisplay(doc,n,dict(prov,layer='ocr' if prov.get('space')=='reading' else 'native',original_frame=(original_frames or {}).get(n)))
                case=controller.degraded[(n,None)]
                fragment,asides,unit=source_unit(display,n,'page','page',package,images,case['reason'])
                unit.update(block=None,stage=case['stage'])
                case.update(unit)
                page={'pno':n,'anchor':f'<span id="pg_{n:04d}"></span>','body':[fragment],'asides':asides}
                pages.append(page);records[n]=dict(source='original_source',blocks=unit['blocks'],new_cost_usd=0.,cached=False,
                    words=0,marks=0,degraded_figures=[],degraded_figure_count=0,degraded_units=[unit])
                continue
            if n in native:
                for lane in ('body','asides'):
                    native_pages[n][lane]=[re.sub(r'(<(?:p|h[1-6])\b[^>]*>)(.*?)(</(?:p|h[1-6])>)',
                        lambda m:m[1]+f'<span data-source-page="{n}">'+m[2]+'</span>'+m[3],
                        fragment,flags=re.S) for fragment in native_pages[n][lane]]
                cases=[case for case in native_cases if case['page']==n]
                native_report={'source':'native','new_cost_usd':0.,
                    'cached':False,'degraded_figures':cases,'degraded_figure_count':len(cases),
                    'degraded_units':native_unit_reports.get(n,[]),
                    'blocks':[]}
                with unit_recovery.guard(n,'native_seam_binding'):
                    native_report['blocks']=_native_seam_blocks(doc,book,n,native_pages[n])
                    _native_published_provenance(native_pages[n],native_report['blocks'])
                    _audit_native_pixels(doc,native_pages[n],native_report['blocks'],
                        reader.cache.parent/'pixel-guard-cache',package,images)
                    native_report['native_note_adjudication']=_audit_native_note_pixels(doc,book,native_pages[n],
                        reader.cache.parent/'pixel-guard-cache',package,images)
                    native_report['degraded_units'].extend(b['unplaced_source_context']
                        for b in native_report['blocks'] if b.get('unplaced_source_context'))
                    audited=native_report['blocks']+native_report['native_note_adjudication']
                    native_report['marks']=sum(len(b.get('marked',[])) for b in audited)
                    native_report['words']=sum(len(b.get('published_tokens',[])) for b in audited)
                    if controller:
                        native_pages[n],native_report=build_epub.degrade_native_units(native_pages[n],native_report,
                            controller.blocked(n),SourceDisplay(doc,n),package,images)
                pages.append(native_pages[n]);records[n]=native_report;continue
            s=scans[n];reading=s['reading'];recovered=s['recovered']
            try:
                with unit_recovery.guard(n,'page_compilation'):
                    local_units=(local_reading.partition_units(reading['local_ocr_units'],
                        s.get('paragraph_repairs',[])) if reading.get('local_ocr') else None)
                    page,report=compile_page(s['answer'],s['source_words'],s['display'],n,package,images,
                        s['optical'],recovered,s['regions'],s['body_owners'],note_context,
                        {case['block']:case for case in s['figure_repairs'] if case.get('degraded')},
                        local_ocr=reading.get('local_ocr',False),
                        **({'local_ocr_units':local_units} if local_units is not None else {}))
            except _CompilationQualityFailure as failure:
                compilation_failures.extend(failure.units)
                continue
            if controller and controller.blocked(n):
                _insert_source_units(page,report,controller.source_answer(n),controller.blocked(n),s['display'],package,images)
            report['recovered_omitted_blocks']=list(recovered)
            report['source_paragraph_repairs']=s.get('paragraph_repairs',[])
            report['source_note_marker_verifications']=list(s.get('source_note_marker_verifications',{}).values())
            report['pixel_bound_geometry_repairs']=s['geometry_repairs']
            report['source_bound_figure_repairs']=s['figure_repairs']
            report['degraded_figures']=[case for case in s['figure_repairs'] if case.get('degraded')]
            report['degraded_figure_count']=len(report['degraded_figures'])
            pages.append(page);records[n]=dict(source='vision',**report,
                **{k:v for k,v in reading.items() if k not in ('answer','local_ocr_units')})
            if local_units is not None:
                records[n]['local_ocr_original_units']=copy.deepcopy(reading['local_ocr_units'])
                records[n]['local_ocr_units']=report['local_ocr_units']
            records[n]['marker_readings']=note_context['marker_receipts'].get(n,[])
            records[n]['new_cost_usd']+=sum(r['new_cost_usd'] for r in records[n]['marker_readings'])
            records[n]['cached']=records[n]['cached'] and all(
                r['cached'] for r in records[n]['marker_readings']
                if r.get('request_dispatched') is not False)
            if progress:progress(n,records[n])
        if compilation_failures:
            # No page sweep, note finishing or publication may consume this
            # failed attempt's partial fragments or reference dictionaries.
            raise _CompilationQualityFailure(compilation_failures)
        # All physical pages, including short notes and headings, are checked.
        # Failure uses the same source-owned reread/disclosure recovery as any
        # other rejected reading; this sweep itself never changes lexical text.
        if controller:
            controller.records=records
            for page in pages:controller.register_page(page,records[page['pno']])
        quality_sweep=_compiled_text_quality(pages,records,numbers,'publication_text_quality')
        for n,quality in quality_sweep['pages'].items():records[n]['text_quality']=quality
        _finish_notes(pages,owners,catalogue,note_context['refs'])
        unit_prints=_defer_unit_prints(pages)
        carriers=_join_control_carriers(pages)
        scan_words,scan_compounds=_scan_wrap_vocabulary(scans)
        for page in pages:
            if page['pno'] in scanned:
                with unit_recovery.guard(page['pno'],'same_page_seams'):
                    _join_scanned_regions(page,records[page['pno']]['blocks'],scan_words,scan_compounds)
        seam_role_repairs=_recover_page_tail_roles(pages,records)
        deferred_openings=_defer_page_art(pages,records,set(scanned))
        boundaries={};native_boundaries={};seam_decisions=[]
        vision_set=set(scanned)
        for prev,current in zip(pages,pages[1:]):
            with unit_recovery.guard([prev['pno'],current['pno']],'seam_decision',units=controller.seam_units(prev,current) if controller else None):
                pno=current['pno'];previous=prev['pno']
                if pno!=previous+1:continue
                before=[b for b in records[previous].get('blocks',[]) if b['role'] not in ('running_head','folio','footnote')]
                after=[b for b in records[pno].get('blocks',[]) if b['role'] not in ('running_head','folio','footnote')]
                # Floating art was deferred; the source paragraph remains the
                # physical tail even though the original report ends with art.
                if before and before[-1]['role'] in ('figure','table','caption'):
                    tail_body=next((b for b in reversed(before) if b['role']=='body'),None)
                    if tail_body and prev['body'] and prev['body'][-1].startswith('<p>'):before=[tail_body]
                if pno in deferred_openings:
                    # Both source-side object groups moved together. Their
                    # original report order remains intact; this opening is
                    # the exact source-proved published first paragraph.
                    after=[deferred_openings[pno]]
                # Native-side continuation uses its deterministic lexical condition;
                # a vision-side continuation requires the page reader's explicit flag.
                left=before[-1].get('continues_next') if before else previous in native
                right=after[0].get('continues_previous') if after else pno in native
                tail_index=build_epub._layout_flow_index(prev['body'],True)
                head_index=build_epub._layout_flow_index(current['body'],False)
                tail=prev['body'][tail_index] if tail_index is not None else ''
                head=current['body'][head_index] if head_index is not None else ''
                if previous in native and tail_index is not None:
                    before=[records[previous]['blocks'][tail_index]]
                if pno in native and head_index is not None:
                    after=[records[pno]['blocks'][head_index]]
                if previous in vision_set and pno in vision_set:
                    proven=_leading_art_continuation(records[previous],current,before,after,tail,
                                                     records[pno]['blocks'])
                    if proven is not None:
                        after=[proven];head=current['body'][0]
                        right=proven.get('continues_previous')
                actual_left=((build_epub._is_paragraph(tail) or before and before[-1].get('native_source_list') and after and after[0].get('native_list_continuation')) if previous in native else tail.startswith('<p>'))
                actual_right=(build_epub._is_paragraph(head) if pno in native else head.startswith('<p>'))
                decision=(_continuation_decision(before[-1],after[0],records[pno]['blocks'],tail,head)
                          if before and after and actual_left and actual_right else
                          {'status':'split','reason':'structural_page_endpoint'})
                if after and after[0].get('source_regions'):
                    opening=after[0]['source_regions'][0]
                    if any(b['role']=='footnote' and b.get('bbox') and b['bbox'][1]<opening[1]
                           for b in records[pno]['blocks']):
                        decision={'status':'split','reason':'inflow_note_before_opening'}
                seam_decisions.append(dict(previous_page=previous,page=pno,**decision))
                if decision['status']=='joined':
                    if before and after and _completed_division(before[-1],after[0],tail,head):
                        tail,head=_repair_completed_division(before[-1],after[0],records[pno]['blocks'],tail,head)
                        # Leading artwork may have moved the actual opening since
                        # the earlier flow-index lookup.
                        tail_index=build_epub._layout_flow_index(prev['body'],True)
                        head_index=build_epub._layout_flow_index(current['body'],False)
                        prev['body'][tail_index]=tail;current['body'][head_index]=head
                    if pno in vision_set or previous in vision_set:
                        boundaries[pno]={'role':'paragraph','hyphen':_seam_hyphen(tail,head,scan_words,scan_compounds),'pixel_boundary':False}
                    else:native_boundaries[pno]=decision
        native_words,native_compounds=_native_wrap_vocabulary(doc,native)
        joins=build_epub._join_page_turns(pages,layout_pages=vision_set,layout_boundaries=boundaries,
            words=native_words,compounds=native_compounds,native_boundaries=native_boundaries)
        for page in pages:
            for carrier,anchor in carriers.items():
                page['body']=[fragment.replace(carrier,anchor) for fragment in page['body']]
        _restore_unit_prints(pages,unit_prints)
        # Removing a printed line-end hyphen can form a newly damaged word.
        # Validate the actual joined text, not only its earlier page fragments.
        quality_sweep=_compiled_text_quality(pages,records,numbers)
        chapters=unit_recovery.chapters(pages)
        reading_flow.finish(chapters,book)
        note_link_disclosures=[]
        def disclose_note_link(units, missing_target, bid):
            # Association failure is local metadata uncertainty. Keep both live
            # text and already admitted page fallbacks; expose original pixels
            # without spending a new read or retrying unrelated transcription.
            pno=units[0]['page']
            from .unit_source import source_unit
            fragment,asides,case=source_unit(SourceDisplay(doc,pno),pno,bid,'other',
                package,images,'The printed note association could not be proved.')
            case.update(stage='note_link_disclosure',missing_target=missing_target)
            note_link_disclosures.append(case)
            return case['source_reference'],[fragment]+asides
        unit_recovery.bind_links(chapters,source_context=disclose_note_link)
        for chapter in chapters:
            chapter.blocks=[build_epub._image_aliases(block,package.aliases) for block in chapter.blocks]
        native_evidence=build_epub._resource_aliases(native_evidence,package.aliases)
        images={package.aliases.get(src,src):record for src,record in images.items()}
        for record in records.values():
            for case in record['degraded_figures']:
                case['source_image']=package.aliases.get(case['source_image'],case['source_image'])
        homes=unit_recovery.page_homes(chapters)
        documents={c.href:unit_recovery.document(c.title,'\n'.join(c.blocks),'en') for c in chapters}
        about='vision-about.xhtml'
        documents[about]=unit_recovery.document('About this conversion','<h1>About this conversion</h1><p>Clean native pages retain deterministic text. Other pages were transcribed from their printed pixels. Proved words remain ordinary text. A plausible but unproved reading is underlined and accompanied by its original print; compare that print when the reading matters. When no complete reading is available, only the original print is shown. A dagger opens the full source crop. Independently supported corrections keep their correction notice. Running heads and folios are excluded from reading flow. Printed notes and transcription disclosures appear in the notes section with Return links.</p>','en')
        entries=[(about,'About this conversion')]+[(c.href,c.title) for c in chapters]
        identifier='urn:uuid:'+str(uuid.uuid4());meta={'title':'PDF vision conversion','language':'en'}
        manifest=[{'id':'nav','href':'nav.xhtml','type':'application/xhtml+xml','properties':'nav'},
                  {'id':'ncx','href':'toc.ncx','type':'application/x-dtbncx+xml'},
                  {'id':'css','href':'style.css','type':'text/css'},
                  {'id':'about','href':about,'type':'application/xhtml+xml'}]
        spine=['about']
        for c in chapters:
            manifest.append({'id':c.item_id,'href':c.href,'type':'application/xhtml+xml'});spine.append(c.item_id)
        if native:
            support=unit_recovery.source_support(native_evidence,homes,chapters)
            for index,(href,html) in enumerate(support.items()):
                ident='native-support-'+str(index);documents[href]=html
                manifest.append({'id':ident,'href':href,'type':'application/xhtml+xml'});spine.append(ident)
            entries.extend([(build_epub.SOURCE_CHECKS_HREF,'Source checks'),(build_epub.SOURCE_INDEX_HREF,'Source PDF pages')])
        from .occurrence_coverage import final_publication
        source_displays={n:scans[n]['display']if n in scans else SourceDisplay(doc,n)for n in numbers}
        source_frames={n:scans[n]['optical']if n in scans else
            [dict(text=w[4],bbox=list(source_displays[n].reading_rect(w[:4])))
             for w in doc[n].get_text('words')]for n in numbers}
        publication_coverage=final_publication(documents,records,source_frames,images,package,source_displays,homes,
            source_inventory=getattr(book,'source_inventory',{}))
        for index,src in enumerate(package.images):
            manifest.append({'id':f'im{index}','href':src,'type':package.media_types[src]})
        result={'converter':'vision-page-pilot','version':vision_reading.VERSION,'model':vision_reading.MODEL,
                'pages':records,'text_quality':quality_sweep,'page_homes':homes,'page_joins':joins,'native_pages':native,'vision_pages':scanned,
                'page_seam_decisions':seam_decisions,'page_seam_role_repairs':seam_role_repairs,
                'source_document_sha256':extract.document_fingerprint(doc),'images':images,
                'publication_coverage':publication_coverage,
                'native_margin_repairs':native_margin_repairs,
                'native_caption_repairs':native_caption_repairs,
                'native_publication_repairs':native_publication_repairs,
                'live_table_cells':[case for record in records.values() for case in record.get('live_table_cells',[])],
                'note_marker_disclosures':[case for record in records.values() for case in record.get('note_marker_disclosures',[])],
                'note_link_disclosures':note_link_disclosures,
                'note_marker_repairs':[case for record in records.values() for case in record.get('note_marker_repairs',[])],
                'degraded_figures':[case for record in records.values() for case in record['degraded_figures']],
                'degraded_figure_count':sum(record['degraded_figure_count'] for record in records.values()),
                'source_evidence':list(native_evidence.values()),
                'source_enrichment':{n:{'provenance':dict((provenance or {}).get(n,{}),layer='ocr' if (provenance or {}).get(n,{}).get('space')=='reading' else 'native')} for n in numbers},
                'new_cost_usd':sum(r.get('new_cost_usd',0.) for r in records.values()),
                'disclosure_count':sum(r.get('marks',0) for r in records.values()),
                'words':sum(r.get('words',0) for r in records.values())}
        if controller:
            result['degraded_units']=[unit for record in records.values() for unit in record.get('degraded_units',[])]
            controller.decorate_report(result)
        # Shared image resources retain each disclosure's own identity.
        result=build_epub._resource_aliases(result,package.aliases)
        text_quality.validate_sweep(quality_sweep,numbers)
        package.finish({'sidecar':result,'documents':documents,
            'opf':unit_recovery.generated_xml('opf',build_epub._opf,dict(meta,**(metadata or {})),manifest,spine,identifier,datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                nonlinear={item for item in spine if item=='about' or item.startswith('native-support-')}),
            'nav':unit_recovery.generated_xml('nav',build_epub._nav,entries,'en',homes),
            'ncx':unit_recovery.generated_xml('ncx',build_epub._ncx,entries,identifier,meta['title'])})
        return result
    except BaseException:
        package.abandon();raise


def _convert_with_recovery(doc,out_path,reader,*,page_numbers=None,provenance=None,progress=None,native_prepared=None,original_frames=None,metadata=None,scratch_dir=None,job_id=None):
    """Complete source-owned units despite rejected readings or assembly units.

    Each failed publication attempt is abandoned. Its source-bound unit either
    receives the one shared targeted reread or is replaced visibly by original
    evidence in a fresh attempt. Healthy primary readings are never dispatched
    again, and partly mutated notes, paragraphs and packages are never reused.
    """
    import tempfile
    from pathlib import Path
    numbers=sorted(page_numbers if page_numbers is not None else range(len(doc)))
    if not numbers or len(set(numbers))!=len(numbers) or any(type(n) is not int or not 0<=n<len(doc) for n in numbers):
        raise unit_recovery.SourceIntegrityError('Invalid or empty authoritative source scope')
    # Authority checks are outside all quality-recovery guards. A changed
    # original/prepared source can never be relabelled as a bad model answer.
    for n in numbers:
        prov=(provenance or {}).get(n,{})
        display=SourceDisplay(doc,n,dict(prov,layer='ocr' if prov.get('space')=='reading' else 'native',
                      original_frame=(original_frames or {}).get(n)))
        if display.provenance.get('original_frame') is not None:
            # The existing lineage verifier checks both PDF identities and the
            # complete prepared/original pixels before any reading dispatch.
            # The requested result is tiny; its pixels are not published.
            try:
                display.roi_pixmap(scale=.01,max_pixels=16)
            except unit_recovery.SourceIntegrityError as error:
                error.source_recovery_event=dict(page=n,stage='source_admission',
                    status='source_integrity_failure',cause_type=type(error).__name__,reason=str(error))
                raise
    parent=Path(out_path).parent;parent.mkdir(parents=True,exist_ok=True)
    with unit_recovery.preparation_scratch(scratch_dir=scratch_dir,job_id=job_id) as staging:
        controller=unit_recovery.Controller(reader,numbers,staging)
        from .image_budget import EvidenceRaster
        source_raster=EvidenceRaster()
        def original_image(n):
            prov=(provenance or {}).get(n,{})
            display=SourceDisplay(doc,n,dict(prov,layer='ocr' if prov.get('space')=='reading' else 'native',
                original_frame=(original_frames or {}).get(n)))
            return source_raster.jpeg(display,display.rect)
        controller.source_image_provider=original_image
        with unit_recovery.activated(controller):
            while True:
                try:
                    return _convert_attempt(doc,out_path,reader,page_numbers=numbers,
                        provenance=provenance,progress=progress,native_prepared=native_prepared,
                        original_frames=original_frames,metadata=metadata)
                except unit_recovery.UnitFailure as failure:
                    controller.recover(failure)


def convert(doc,out_path,reader,*,page_numbers=None,provenance=None,progress=None,native_prepared=None,original_frames=None,metadata=None,scratch_dir=None,job_id=None):
    """Publish source-owned units; persist bounded SOURCE-fatal diagnostics.

    Optional source-record metadata supplies OPF title, authors and language.
    Reading documents and navigation retain their original conversion values.
    """
    try:
        return _convert_with_recovery(doc,out_path,reader,page_numbers=page_numbers,
            provenance=provenance,progress=progress,native_prepared=native_prepared,
            original_frames=original_frames,metadata=metadata,scratch_dir=scratch_dir,job_id=job_id)
    except unit_recovery.SourceIntegrityError as error:
        import os,tempfile
        event=getattr(error,'source_recovery_event',{})
        diagnostic=dict(kind='original-source-failure',page=event.get('page'),
            stage=event.get('stage','source_publication'),
            status=event.get('status','source_integrity_failure'),
            cause_type=event.get('cause_type',type(error).__name__),
            reason=str(error)[:1000])
        # Only bounded error metadata is persisted, never an answer or SOURCE text.
        diagnostic['event']={k:(v[:1000] if isinstance(v,str) else v) for k,v in event.items()
            if k in {'page','block','stage','status','cause_type','reason','transition',
                     'repeated_cause_type','repeated_reason','repair_allowance_used'}}
        try:
            diagnostic['source_document_sha256']=extract.document_fingerprint(doc)
        except Exception:
            # An unreadable source still gets its explicit fatal diagnostic.
            diagnostic['source_document_sha256']=None
        frame=(original_frames or {}).get(diagnostic['page'])
        if isinstance(frame,dict):
            diagnostic['original_source_sha256']=frame.get('original_pdf_sha256')
        destination=Path(str(out_path)+'.source-failure.json')
        destination.parent.mkdir(parents=True,exist_ok=True)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=destination.parent,
                    prefix='.'+destination.name+'-',delete=False) as handle:
                temporary=Path(handle.name)
                json.dump(diagnostic,handle,sort_keys=True);handle.write('\n')
                handle.flush();os.fsync(handle.fileno())
            os.replace(temporary,destination);temporary=None
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
        raise
