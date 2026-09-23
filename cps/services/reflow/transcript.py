# SPDX-License-Identifier: GPL-3.0-or-later
"""Qualify an invisible transcript against independent source recognition.

Recognition supplies disagreement/confidence evidence only. Its text never
replaces the embedded transcript. Uncertain whole source words/regions retain
printed pixels; corroborated text keeps its original spelling and geometry.
"""
import difflib
import re
from dataclasses import replace

VERSION = 'native-transcript-agreement-1'
_LIGATURES = {'ﬁ':'fi','ﬂ':'fl','ﬀ':'ff','ﬃ':'ffi','ﬄ':'ffl'}


def _canonical(text):
    chars, offsets = [], []
    for index, char in enumerate(text):
        if char.isspace() or char == '\u00ad':
            continue
        value = _LIGATURES.get(char, char)
        chars.extend(value); offsets.extend([index]*len(value))
    return ''.join(chars), offsets


def corroborate(raw, recognition, confidence_floor=85):
    from .native_text import _whole_source_words
    lines=[line for block in raw.text_blocks for line in block.lines]
    assigned={id(line):[] for line in lines}
    unmatched = []
    for word in recognition.words:
        box=word.pdf_bbox
        x,y=(box[0]+box[2])/2,(box[1]+box[3])/2
        matches=[line for line in lines if line.bbox[0]-1<=x<=line.bbox[2]+1
                 and line.bbox[1]-1<=y<=line.bbox[3]+1]
        if matches:
            chosen=min(matches,key=lambda line:abs(y-(line.bbox[1]+line.bbox[3])/2))
            assigned[id(chosen)].append(word)
        elif any(char.isalnum() for char in word.text):
            unmatched.append(word)
    replacements={};word_count=uncertain_count=region_count=0
    for line in lines:
        words=sorted(assigned[id(line)],key=lambda word:word.pdf_bbox[0])
        native=line.text; seen=' '.join(word.text for word in words)
        left,offsets=_canonical(native);right,_=_canonical(seen)
        disputed=set();punctuation=set();region=False
        if max(len(left),len(right))>4096 or not words:
            region=bool(native.strip())
        else:
            for tag,a,b,c,d in difflib.SequenceMatcher(None,left,right,autojunk=False).get_opcodes():
                if tag=='equal':continue
                disputed.update(index for index in offsets[a:b] if native[index].isalnum())
                punctuation.update(index for index in offsets[a:b] if not native[index].isalnum())
                # Extra recognized ink has no trustworthy native character box.
                # Keep the source region rather than inventing an inserted word.
                if a==b and any(ch.isalnum() for ch in right[c:d]):region=True
        spans=[];cursor=0
        for span in line.spans:
            for token in re.finditer(r'\S+|\s+',span.text):
                chars=[c for c in span.char_boxes if c[0]>=token.start() and c[1]<=token.end()]
                has_text=bool(token.group().strip())
                if has_text:word_count+=1
                uncertain=has_text and any(cursor+index in disputed for index in range(token.start(),token.end()))
                if chars:
                    box=(min(c[2] for c in chars),min(c[3] for c in chars),max(c[4] for c in chars),max(c[5] for c in chars))
                    uncertain=uncertain or (has_text and any(word.confidence<confidence_floor and any(ch.isalnum() for ch in word.text)
                        and box[0]-1<=(word.pdf_bbox[0]+word.pdf_bbox[2])/2<=box[2]+1
                        and box[1]-1<=(word.pdf_bbox[1]+word.pdf_bbox[3])/2<=box[3]+1 for word in words))
                else:
                    box=span.bbox
                    if has_text:region=True
                if uncertain:uncertain_count+=1
                spans.append(replace(span,text=token.group(),bbox=box,
                    transcription_uncertain=uncertain,
                    punctuation_uncertain=span.punctuation_uncertain or any(cursor+index in punctuation for index in range(token.start(),token.end())),
                    char_boxes=tuple((c[0]-token.start(),c[1]-token.start(),*c[2:]) for c in chars)))
            cursor+=len(span.text)
        if region:region_count+=1
        replacements[id(line)]=replace(line,spans=_whole_source_words(spans),transcription_uncertain=region)
    blocks=[replace(block,lines=[replacements[id(line)] for line in block.lines]) for block in raw.blocks]
    for block in blocks:
        for line in block.lines:
            for span in line.spans:span.char_boxes=()
    return replace(raw,blocks=blocks,transcript_unverified=bool(unmatched)),dict(version=VERSION,words=word_count,
        unmatched_words=len(unmatched),
        uncertain_words=uncertain_count,uncertain_lines=region_count,lines=len(lines))
