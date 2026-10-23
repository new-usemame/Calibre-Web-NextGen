"""Publication tiers keep an unproved reading adjacent to its original print.

These choices supply reading assistance, never an identity certificate. Existing
resolved corrections retain their correction notice; undecodable glyphs and
incomplete surviving fragments remain print-only.
"""
import re
import unicodedata
from html import escape

PRINT_ONLY = frozenset({
    'unproved_native_character_map', 'unproved_doubtful_leading_word_ink',
    'incomplete_vertical_word_ink', 'incomplete_numeric_word_ink',
    'unproved_complete_divided_word', 'unproved_complete_word_scope',
    'local_numeric_suffix_not_identified', 'unsettled_additional_pixel_ink',
})


def typographic_key(value):
    # NFKC expands presentation ligatures but does not strip accents/macrons.
    value = unicodedata.normalize('NFKC', value).replace('\u00ad', '')
    return value.translate(str.maketrans({'’': "'", '‘': "'", 'ʼ': "'",
                                         '“': '"', '”': '"'}))


def equivalent(left, right):
    return typographic_key(left) == typographic_key(right)


def choose(token, decision, *, rank_lexical=True):
    """Choose assistance from an existing reader, with an explicit provenance.

    Numeric identifiers prefer a source reading when the model dropped a real
    digit. Ordinary disagreements retain the chosen complete first reading;
    a dictionary cannot distinguish two valid words or authorize added letters.
    """
    if (decision.get('status') == 'RESOLVED'
            and decision.get('method') == 'complete_vision_reading_without_optical_counterevidence'):
        # Lack of a complete competing read does not independently prove the
        # retained first reading. It remains useful, explicitly flagged text.
        return dict(tier='flagged', reading=token, method='complete_reader_without_independent_proof')
    if decision.get('status') == 'RESOLVED':
        return dict(tier='proved', reading=token, method='existing_resolution')
    method = decision.get('method', '')
    if method in PRINT_ONLY or decision.get('unplaced_prefix'):
        return dict(tier='print', reading=None, method='no_complete_reading')
    source, model = decision.get('source', []), decision.get('model', [])
    if method=='optical_map_confusable_nonword' and rank_lexical:
        # The optical-map check supplies one stroke-confusable dictionary
        # hypothesis, preserving case and punctuation. Rank that assistance
        # without turning a lexical preference into a pixel certificate.
        candidates=decision.get('alternative',[])
        if len(candidates)==1:
            return dict(tier='flagged',reading=candidates[0],method='lexical_confusable_hypothesis')
    if method == 'unproved_mathematical_symbol_identity':
        # A Unicode symbol is a plausible reading. Alphabetic guesses made by
        # both general readers are not evidence of a symbolic font's identity.
        symbolic = next((s for s in source + model
                         if any(unicodedata.category(c).startswith('S') for c in s)), None)
        if symbolic is None:
            return dict(tier='print', reading=None, method='unidentified_symbol')
        return dict(tier='flagged', reading=symbolic, method='reader_unicode_symbol')
    if (method=='unproved_source_transcription_change' and rank_lexical
            and len(source)==1 and 2<=len(model)<=4):
        # Rank an already observed complete reading over a fused nonword, not
        # a newly invented segmentation. The aggregate retains its one source
        # box and unresolved receipt, so the reader can inspect the boundary.
        from .text_quality import english_lexicon
        from difflib import SequenceMatcher
        lexical=lambda s: re.sub(r'^[^A-Za-z]+|[^A-Za-z]+$', '',s).casefold()
        original=lexical(source[0]);parts=[lexical(t) for t in model]
        vocabulary=english_lexicon()
        edits=sum(max(b-a,d-c) for op,a,b,c,d in
            SequenceMatcher(None,original,''.join(parts),autojunk=False).get_opcodes() if op!='equal')
        if (original.isascii() and original.isalpha() and original not in vocabulary
                and all(p and p.isascii() and p.isalpha() and p in vocabulary for p in parts)
                and len(original)>=5 and edits<=1):
            return dict(tier='flagged',reading=' '.join(model),method='complete_reader_over_fused_nonword')
    if len(source) == len(model) == 1:
        from .text_quality import english_lexicon
        a, b = source[0], model[0]
        if (re.fullmatch(r'\d{1,3}(?:st|nd|rd|th)[.,;:)]?', b, re.I)
                and not re.fullmatch(r'\d{1,3}(?:st|nd|rd|th)[.,;:)]?', a, re.I)
                and re.fullmatch(r'[Iil1|0OogBSs]+(?:st|nd|rd|t?h)?[.,;:)]?', a, re.I)
                and (a[:1] in "Iil1|" or a.casefold() not in english_lexicon())):
            return dict(tier='flagged',reading=b,method='complete_reader_typed_ordinal')
        if (any(c.isdigit() for c in a) and any(c.isalpha() for c in a)
                and sum(c.isdigit() for c in a) > sum(c.isdigit() for c in b)
                and len(a) == len(b) + 1
                and any(a[:i] + a[i+1:] == b for i,c in enumerate(a) if c.isdigit())):
            return dict(tier='flagged', reading=a, method='source_complete_numeric_identifier')
    # Private-use, replacement and control glyphs cannot be helpful text. A
    # source-only punctuation fragment is not a plausible whole word either.
    if (not token or not any(c.isalnum() for c in token)
            or any(unicodedata.category(c) in ('Co', 'Cc', 'Cs') or c == '\ufffd' for c in token)
            or token.startswith('<n:')):
        return dict(tier='print', reading=None, method='no_lexical_reading')
    if (rank_lexical and len(source)==len(model)==1
            and token.islower() and token.isalpha() and token.isascii()):
        # English word-list absence says nothing about an accent, possessive
        # or compound. Rank only plain alphabetic readings.
        # A dictionary may rank already observed complete candidates; it never
        # certifies their ink. Names, symbols and two valid words keep the first
        # reader. The attached print retains the unresolved physical decision.
        from .text_quality import english_lexicon
        vocabulary=english_lexicon()
        lexical=lambda s: bool(s) and s.islower() and s.isalpha() and s in vocabulary
        if not lexical(token):
            candidates={s for s in source+decision.get('alternative',[])if lexical(s)}
            if len(candidates)==1:
                return dict(tier='flagged',reading=candidates.pop(),method='lexical_reader_candidate')
    return dict(tier='flagged', reading=token, method='existing_reader_hypothesis')


def prepare(result):
    """Attach publication decisions without erasing the original adjudication."""
    flags = {}
    for index in sorted(result['marked']):
        if result['tokens'][index].startswith('<n:'):
            continue
        old = result.get('word_notes', {}).get(index)
        decision = dict(old or dict(source=[], model=[result['tokens'][index]],
                                    chosen=[result['tokens'][index]], status='UNRESOLVED'))
        choice = choose(result['tokens'][index], decision)
        decision['publication'] = choice
        if choice['tier'] == 'flagged':
            flags[index] = choice
            result['tokens'][index] = choice['reading']
            decision['source_image_only_before_publication'] = decision.get('source_image_only', False)
            decision['source_image_only'] = False
        result.setdefault('word_notes', {})[index] = decision
    result['flagged_readings'] = flags
    return flags


def flagged_html(reading, image, target, style='', *, reading_html=None):
    """Print evidence is decorative to speech; the live reading is spoken once."""
    metric = ' style="' + escape(style, quote=True) + '"' if style else ''
    return ('<span class="reflow-flagged-reading"><span class="reflow-plausible">'
            + (escape(reading) if reading_html is None else reading_html) + '</span>'
            + '<a class="source-word-image" title="Inspect original print" href="#' + target + '"><img src="'
            + image + '" alt=""' + metric + '/></a></span>')


def stylesheet():
    return '''\n.reflow-plausible { border-bottom:1px solid currentColor; }
.reflow-flagged-reading { font-style:inherit; }
.reflow-plausible a { color:inherit; text-decoration:none; }
.reflow-print-control { white-space:nowrap; margin-left:.15em; }
.reflow-unit-print { text-indent:0; margin:.4em 0; white-space:nowrap; }
.reflow-unit-print img { max-width:95%; width:auto; height:auto; vertical-align:baseline; }
.reflow-unit-print a.source-word-image img { max-width:95%; width:auto; height:auto; vertical-align:baseline; }
.reflow-flagged-reading > a.source-word-image { margin-left:.15em; }
a.source-word-image, a.source-disclosure-ref { text-decoration:none; color:inherit; }
a.source-word-image img { max-width:none; width:auto; }
a.source-disclosure-ref sup { font-size:.55em; line-height:0; }
'''


def join_letter_spaced_readings(result, witness):
    """One whole pixel word may own a marked run of typographic letter atoms.

    The letters themselves are conserved. A complete high-confidence reading at
    this precise row supplies the word boundary; spelling elsewhere does not.
    Existing opaque-code refusals survive as a single print-only word.
    """
    from .pixel_adjudication import _complete_word_occurrence
    tokens=result['tokens']; groups=[];i=0
    while i<len(tokens):
        candidates=[]
        for end in range(i+2,min(i+16,len(tokens))+1):
            indices=list(range(i,end))
            if not all(len(tokens[j])==1 and tokens[j].isalpha()
                       and j in result['marked'] and j in result['boxes'] for j in indices):break
            boxes=[result.get('word_notes',{}).get(j,{}).get('source_bbox',result['boxes'][j])for j in indices]
            if any(not(-.5<=b[0]-a[2]<=.8*max(a[3]-a[1],b[3]-b[1])
                       and min(a[3],b[3])-max(a[1],b[1])>=.5*min(a[3]-a[1],b[3]-b[1]))
                   for a,b in zip(boxes,boxes[1:])):continue
            literal=''.join(tokens[i:end]);bounds=[min(b[0]for b in boxes),min(b[1]for b in boxes),
                max(b[2]for b in boxes),max(b[3]for b in boxes)]
            proof=[w for w in witness if equivalent(w['text'].casefold(),literal.casefold())
                   and w.get('confidence',0)>=85 and _complete_word_occurrence(dict(bbox=bounds),w)]
            if len(proof)==1:candidates.append((end,literal,bounds))
        if candidates:
            end,literal,bounds=max(candidates)
            old=[result.get('word_notes',{}).get(j,{})for j in range(i,end)]
            opaque=any(d.get('method')=='unproved_native_character_map' for d in old)
            note=dict(source=[s for d in old for s in d.get('source',[])],model=[literal],chosen=[literal],
                status='UNRESOLVED',method='unproved_native_character_map' if opaque else 'letter_spaced_printed_word',
                source_bbox=bounds,source_image_only=opaque,letter_spaced_atoms=list(range(i,end)))
            groups.append((i,end,literal,bounds,note));i=end
        else:i+=1
    if not groups:return
    starts={a:(z,text,box,note)for a,z,text,box,note in groups}
    output=[];boxes={};notes={};marked=set();verified=[];mapping={};i=0
    while i<len(tokens):
        new=len(output)
        if i in starts:
            end,text,box,note=starts[i];output.append(text);boxes[new]=box;notes[new]=note;marked.add(new)
            mapping.update({old:new for old in range(i,end)});i=end;continue
        output.append(tokens[i]);mapping[i]=new
        if i in result['boxes']:boxes[new]=result['boxes'][i]
        if i in result.get('word_notes',{}):notes[new]=result['word_notes'][i]
        if i in result['marked']:marked.add(new)
        if i in result.get('verified_unchanged',[]):verified.append(new)
        i+=1
    result.update(tokens=output,boxes=boxes,word_notes=notes,marked=marked,verified_unchanged=verified)
    result.setdefault('guard',[]).append(dict(kind='letter_spaced_printed_words',groups=[dict(
        original_indices=list(range(a,z)),reading=text,source_bbox=box)for a,z,text,box,note in groups]))


def join_fragmented_readings(result, witness):
    """A measured continuous word gets one plausible reading and one tier.

    OCR can split italic strokes into several purported words. The first
    reader's complete word is useful assistance only when original ink proves
    there is no internal word space. This groups the hypothesis; it does not
    certify its letters or discard the source alternatives.
    """
    boundary=getattr(witness,'fragment_boundary',None)
    if boundary is None:return
    from difflib import SequenceMatcher
    tokens=result['tokens'];groups=[]
    for decision in result.get('adjudication',[]):
        source=decision.get('source',[]);model=decision.get('model',[])
        if (decision.get('status')!='UNRESOLVED'
                or decision.get('method')!='unproved_source_transcription_change'
                or not 2<=len(source)<=6 or len(model)!=1):continue
        letters=(all(t.isalpha()for t in source+model)
            and SequenceMatcher(None,''.join(source).casefold(),model[0].casefold()).ratio()>=.65)
        # A complete first-reader ordinal is another whole-word hypothesis.
        # Optical pieces may call an old-style numeral I/l or omit its tiny
        # raised stroke. Rank that existing typed reading only after the same
        # geometric and measured-ink boundary checks below; never certify it.
        ordinal=False
        if (re.fullmatch(r'\d{1,3}(?:st|nd|rd|th)',model[0],re.I)
                and all(t.isascii() and t.isalnum()for t in source)):
            optical=''.join(source).translate(str.maketrans({'I':'1','i':'1','l':'1','O':'0','o':'0'})).casefold()
            edits=sum(max(b-a,d-c)for op,a,b,c,d in
                SequenceMatcher(None,optical,model[0].casefold(),autojunk=False).get_opcodes()if op!='equal')
            ordinal=edits<=1
        if not(letters or ordinal):continue
        positions=[i for i in range(len(tokens)-len(source)+1)
            if tokens[i:i+len(source)]==source and all(
                j in result['marked'] and j in result['boxes']
                and result.get('word_notes',{}).get(j,{}).get('source_range')
                and decision['source_range'][0]<=result['word_notes'][j]['source_range'][0]
                and result['word_notes'][j]['source_range'][1]<=decision['source_range'][1]
                and result['word_notes'][j].get('model_range')==decision.get('model_range')
                for j in range(i,i+len(source)))]
        if len(positions)!=1:continue
        start=positions[0];end=start+len(source);parts=[result['boxes'][i]for i in range(start,end)]
        if any(not(-.5<=b[0]-a[2]<=.15*min(a[3]-a[1],b[3]-b[1])
                and min(a[3],b[3])-max(a[1],b[1])>=.6*min(a[3]-a[1],b[3]-b[1]))
                for a,b in zip(parts,parts[1:])):continue
        box=[min(b[0]for b in parts),min(b[1]for b in parts),max(b[2]for b in parts),max(b[3]for b in parts)]
        context=dict(bbox=box)
        if ordinal:context['ordinal_candidate']=model[0]
        if boundary(context)is not True:continue
        if any(start<z and end>a for a,z,_,_,_ in groups):continue
        note=dict(decision,chosen=model,method='plausible_complete_word_over_optical_fragments',
            source_bbox=box,optical_fragment_boxes=[list(b)for b in parts],source_image_only=False)
        groups.append((start,end,model[0],box,note))
    if not groups:return
    starts={a:(z,text,box,note)for a,z,text,box,note in groups}
    output=[];boxes={};notes={};marked=set();verified=[];i=0
    while i<len(tokens):
        new=len(output)
        if i in starts:
            end,text,box,note=starts[i];output.append(text);boxes[new]=box;notes[new]=note;marked.add(new);i=end;continue
        output.append(tokens[i])
        if i in result['boxes']:boxes[new]=result['boxes'][i]
        if i in result.get('word_notes',{}):notes[new]=result['word_notes'][i]
        if i in result['marked']:marked.add(new)
        if i in result.get('verified_unchanged',[]):verified.append(new)
        i+=1
    result.update(tokens=output,boxes=boxes,word_notes=notes,marked=marked,verified_unchanged=verified)
    result.setdefault('guard',[]).append(dict(kind='whole_word_publication_tiers',groups=[dict(
        original_indices=list(range(a,z)),reading=text,source_bbox=box)for a,z,text,box,note in groups]))


def discard_dot_leaders(result, display, neighbors):
    """Even solid dots between a title and folio are layout, not OCR words.

    Measure the original components, their repeated spacing and the two row
    anchors. Small real letters, uneven marks and unanchored punctuation keep
    their normal reading. Store exact excluded regions for occurrence accounting.
    """
    import statistics
    from . import vision_pixels
    heights=[w['bbox'][3]-w['bbox'][1]for w in neighbors
        if sum(c.isalpha()for c in w['text'])>=3]
    if not heights:return
    normal_height=statistics.median(heights)
    regions=[];removed=set()
    for index,token in enumerate(result['tokens']):
        box=result.get('boxes',{}).get(index)
        if not box or len(token)>10 or not any(c.isalnum()for c in token):continue
        width,height=box[2]-box[0],box[3]-box[1]
        if not(0<height<min(20,normal_height*.4) and 0<width<=180):continue
        middle=(box[1]+box[3])/2
        left=[w for w in neighbors if sum(c.isalpha()for c in w['text'])>=2
            and w['bbox'][2]<=box[0]+.1 and w['bbox'][1]<=middle<=w['bbox'][3]]
        right=[w for w in neighbors if re.fullmatch(r'\d{1,4}',w['text'])
            and w['bbox'][0]>=box[2]-.1 and w['bbox'][1]<=middle<=w['bbox'][3]]
        if not left or not right:continue
        a=max(left,key=lambda w:w['bbox'][2])['bbox'];b=min(right,key=lambda w:w['bbox'][0])['bbox']
        normal=min(a[3]-a[1],b[3]-b[1])
        if normal<=0 or height>=normal*.4 or box[0]-a[2]>normal*12 or b[0]-box[2]>normal*12:continue
        picture,components=vision_pixels._word_ink_components(display.pixmap(box,scale=8,gray=True,max_pixels=200_000))
        if not 3<=len(components)<=100:continue
        sx=width/picture.width;sy=height/picture.height;pixels=picture.load();centers=[];diameters=[]
        for x0,y0,x1,y1 in components:
            cw,ch=(x1-x0)*sx,(y1-y0)*sy
            if not(.4<=cw/max(.01,ch)<=2.5 and max(cw,ch)<normal*.35):break
            dark=sum(pixels[x,y]<150 for y in range(y0,y1)for x in range(x0,x1))
            if dark<.65*(x1-x0)*(y1-y0):break
            centers.append(((x0+x1)*sx/2,(y0+y1)*sy/2));diameters.append(max(cw,ch))
        else:
            centers.sort();diameter=statistics.median(diameters)
            gaps=[b[0]-a[0]for a,b in zip(centers,centers[1:])];pitch=statistics.median(gaps)
            if (max(y for x,y in centers)-min(y for x,y in centers)<=diameter*.6
                    and 1.5*diameter<=pitch<=8*diameter
                    and all(abs(g-pitch)<=pitch*.3 for g in gaps)):
                removed.add(index);regions.append(list(box))
    if not removed:return
    kept=[i for i in range(len(result['tokens']))if i not in removed];mapping={old:new for new,old in enumerate(kept)}
    result['tokens']=[result['tokens'][i]for i in kept]
    for key in ('boxes','word_notes'):
        result[key]={mapping[i]:v for i,v in result.get(key,{}).items()if i in mapping}
    result['marked']={mapping[i]for i in result['marked']if i in mapping}
    result['verified_unchanged']=[mapping[i]for i in result.get('verified_unchanged',[])if i in mapping]
    result['decorative_source_regions']=regions
    result.setdefault('guard',[]).append(dict(kind='measured_contents_dot_leaders',source_regions=regions))


def conserve_punctuation_boundaries(result, source, model):
    """A source delimiter moved across whitespace is still one occurrence.

    A local correction may keep a source's glued leading comma while the first
    reader already placed that same comma on the preceding token. Account for
    both source tokens and both model tokens before consuming the duplicate.
    Two genuinely printed delimiters and decimal points are unchanged.
    """
    for index, note in list(result.get('word_notes',{}).items()):
        chosen=result['tokens'][index]
        if (index==0 or not chosen or chosen[0] not in ',.;:'
                or not chosen[1:].isalpha() or note.get('status')!='RESOLVED'):
            continue
        a,z=note.get('source_range',[-1,-1]);b,y=note.get('model_range',[-1,-1])
        if not(0<a<z<=len(source) and z-a==1 and 0<b<y<=len(model) and y-b==1):continue
        glyph=chosen[0];left,right=source[a-1],source[a]
        if (left['text'].endswith(tuple(',.;:')) or right['text'][:1]not in ',.;:'
                or model[b]!=chosen[1:] or not model[b-1].endswith(glyph)
                or not result['tokens'][index-1].endswith(glyph)):
            continue
        u,v=left['bbox'],right['bbox']
        if not(min(u[3],v[3])>max(u[1],v[1]) and -.5<=v[0]-u[2]<=2*max(u[3]-u[1],v[3]-v[1])):continue
        result['tokens'][index]=chosen[1:]
        result['marked'].discard(index);result['word_notes'].pop(index,None)
        result.setdefault('guard',[]).append(dict(kind='source_punctuation_boundary_conserved',
            index=index,original_chosen=chosen,reading=chosen[1:],source_range=[a-1,z],model_range=[b-1,y]))


_SHAPE_PROOFS = frozenset({'independent_upright_crop', 'two_own_word_language_readings',
    'two_own_pixel_readings', 'intact_pixel_corroboration_of_model',
    'corroborated_resampled_pixel_readings', 'two_alternate_alphabet_pixel_readings'})
_SHAPE_DOUBTS = frozenset({'unproved_shared_stroke_identity', 'unsettled_alternate_pixel_readings',
                         'pixel_read_inconclusive'})


def settle_typographic_equivalents(result):
    """Representation alone cannot cast doubt on a conserved printed word.

    Accents and symbol identities are not representation choices. Keep the
    existing correction notice rather than inventing an invisible repair.
    """
    for index,old in list(result.get('word_notes',{}).items()):
        if old.get('status')!='UNRESOLVED' or old.get('method') not in (
                'unproved_quote_identity','pixel_read_inconclusive','unsettled_alternate_pixel_readings'):
            continue
        source=old.get('source',[]);model=old.get('model',[])
        if len(source)!=1 or len(model)!=1 or source==model or not equivalent(source[0],model[0]):continue
        chosen=result['tokens'][index]
        if not equivalent(chosen,source[0]):continue
        decision=dict(old,status='RESOLVED',method='typographic_equivalence',source_image_only=False)
        result['word_notes'][index]=decision
        result.setdefault('guard',[]).append(dict(kind='typographic_equivalence',index=index))


def word_shape(display, box):
    """A bounded gray ink signature, refusing touching or incomplete crops."""
    from PIL import Image, ImageOps
    from .extract import pymupdf
    rect=(pymupdf.Rect(box)+(-2,-2,2,2)) & display.rect
    if rect.get_area()<=0 or rect.get_area()>6000:return None
    pix=display.pixmap(rect,scale=6,gray=True,max_pixels=220_000)
    image=Image.frombytes('L',(pix.width,pix.height),pix.samples)
    ink=ImageOps.invert(image)
    bound=ink.point(lambda v:255 if v>95 else 0).getbbox()
    if not bound or min(bound[:2])<=1 or bound[2]>=image.width-1 or bound[3]>=image.height-1:return None
    w,h=bound[2]-bound[0],bound[3]-bound[1]
    if w/h>20 or w<3 or h<3:return None
    ink=ink.crop(bound).resize((max(1,round(w/h*32)),32),Image.Resampling.LANCZOS)
    return dict(width=w,height=h,signature=ink,source_bbox=list(box))


def same_word_shape(left,right):
    """Whole ink agreement, with mismatch normalized to ink rather than paper."""
    from PIL import Image
    a,b=left['signature'],right['signature']
    if abs(a.width-b.width)>max(1,.02*a.width):return False
    if abs(left['height']-right['height'])>max(1,.04*left['height']):return False
    b=b.resize(a.size,Image.Resampling.LANCZOS)
    x,y=a.tobytes(),b.tobytes()
    return sum(abs(i-j)for i,j in zip(x,y))/max(1,sum(x)+sum(y))<=.02


def settle_repeated_word_shapes(result,display):
    """Reuse only a completed independent proof of this same whole ink shape.

    A different occurrence cannot supply a spelling by vocabulary frequency.
    Letters, dimensions and gray ink must all agree with an earlier proved
    occurrence. Opaque codes, diacritics and symbolic identities stay outside
    this optional lexical-stroke proof.
    """
    evidence=getattr(display,'_reflow_proved_word_shapes',None)
    if evidence is None:
        evidence=[];display._reflow_proved_word_shapes=evidence
    notes=result.get('word_notes',{})
    # Within a block, a later clear occurrence may settle an earlier one too.
    for i,note in notes.items():
        if (note.get('status')!='RESOLVED' or note.get('method')not in _SHAPE_PROOFS
                or i not in result['boxes']):continue
        token=result['tokens'][i]
        if not token.strip('.,;:!?').isalpha():continue
        shape=word_shape(display,result['boxes'][i])
        if shape is not None and len(evidence)<256:
            evidence.append(dict(reading=token,shape=shape,method=note['method']))
    for i,note in list(notes.items()):
        if (note.get('status')!='UNRESOLVED' or note.get('method')not in _SHAPE_DOUBTS
                or i not in result['boxes']):continue
        token=result['tokens'][i]
        proofs=[p for p in evidence if equivalent(p['reading'],token)]
        if not proofs:continue
        shape=word_shape(display,note.get('source_bbox',result['boxes'][i]))
        if shape is None:continue
        matches=[p for p in proofs if same_word_shape(shape,p['shape'])]
        if not matches:continue
        proof=matches[0]
        resolved=dict(note,status='RESOLVED',method='same_page_complete_word_shape',
            source_image_only=False,shape_proof=dict(source_bbox=proof['shape']['source_bbox'],
                reading=proof['reading'],method=proof['method']))
        notes[i]=resolved
        result.setdefault('adjudication',[]).append(resolved)


def bound_print_runs(groups, flagged, boxes):
    """Each appended print run follows one row at a readable inline width.

    A block-level OCR warning can cover several lines. It is not one large
    glyph: preserve all of its reading and split only its presentation.
    Missing geometric evidence prevents joining, rather than borrowing a row.
    """
    output=[]
    for group in groups:
        if not all(i in flagged for i in group):
            output.append(group);continue
        current=[]
        for index in group:
            candidate=current+[index]
            own=[boxes[i] for i in candidate if i in boxes]
            join=len(own)==len(candidate) and len(candidate)<=8
            if join and len(own)>1:
                heights=[b[3]-b[1] for b in own]
                join=min(heights)>0 and max(b[2] for b in own)-min(b[0] for b in own)<=10*min(heights)
                for a,b in zip(own,own[1:]):
                    join=join and (min(a[3],b[3])-max(a[1],b[1])>=.5*min(a[3]-a[1],b[3]-b[1])
                        and -.5<=b[0]-a[2]<=1.5*max(a[3]-a[1],b[3]-b[1]))
            if current and not join:
                output.append(current);current=[]
            current.append(index)
        if current:output.append(current)
    return output
