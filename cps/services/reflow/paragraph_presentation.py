"""Measured source paragraph insets, independent of semantic wrapper choices.

A current canonical paragraph and its complete retained lines supply geometry.
No quotation role, punctuation boundary, words, assets or source tree is changed.
"""
import hashlib
import json
import math
import os
import statistics
from xml.dom import Node
from xml.parsers.expat import ExpatError
from . import extract,heading_evidence as geometry,quote_evidence,source_inventory,skeleton
from .source_display import SourceDisplay
from .source_reading_display import _tree,_fragment,_source_shape

VERSION='source-paragraph-presentation-1'


def stylesheet(evidence):
    rules={}
    for page in evidence:
        for entry in page.get('paragraph_presentation',{}).get('entries',[]):
            if entry['displayed']:
                key=entry['id']
                rules[key]=('p[data-source-inset="%s"] { margin-left:%.8fem; text-indent:%.8fem; }\n' %
                    (key,entry['left_em'],entry['first_line_em']))
    return ''.join(rules[k] for k in sorted(rules))


def _shape(node):
    retained=node.cloneNode(True)
    # These annotations are issued after the markup boundary. Strip only that
    # generated child to compare all original attributes, resources and words.
    for child in list(retained.getElementsByTagName('*')):
        if 'source-reading-annotation' in child.getAttribute('class').split():
            child.parentNode.removeChild(child)
    return _source_shape(retained)


def _size(lines):
    sizes=[s['size'] for s in geometry._spans(lines) if isinstance(s.get('size'),(int,float)) and
           math.isfinite(s['size']) and s['size']>0]
    return statistics.median(sizes) if sizes else 0


def render(book,doc,source,raw,fragment,excluded=()):
    source.validate(book)
    audit=dict(version=VERSION,source_identity=source.identity,entries=[])
    inventory=book.source_inventory.get(source.page)
    if raw is None or inventory is None:return fragment,audit
    source_inventory.validate(inventory,raw)
    name=getattr(doc,'name',None)
    if not (name and os.path.isfile(name) and not getattr(doc,'is_dirty',True) and
            book.source_fingerprint and extract.document_fingerprint(doc)==book.source_fingerprint):
        audit['reason']='immutable_source_pdf_binding_unproved';return fragment,audit
    provenance=json.loads(source.provenance_json);layer=provenance.get('layer')
    if layer not in ('native','ocr') or (layer=='native' and doc[source.page].rotation):
        audit['reason']='unsupported_source_coordinate_frame';return fragment,audit
    display=SourceDisplay(doc,source.page,provenance)
    if abs(raw.width-display.rect.width)>.02 or abs(raw.height-display.rect.height)>.02:
        audit['reason']='source_frame_mismatch';return fragment,audit
    try:_tree(source.html);current=_tree(fragment)
    except ExpatError:
        audit['reason']='source_or_publication_markup_unparseable';return fragment,audit
    raw_blocks=raw.to_dict()['blocks']
    lines=[line for block in raw_blocks if block.get('kind','text')=='text'
           for line in block.get('lines',[]) if geometry._valid(line.get('bbox')) and line.get('spans')]
    elements=book.pages[source.page];mapped={}
    for index,element in enumerate(elements):
        if element.kind!='p' or element.table_row or index in excluded:continue
        if any(r[0]=='glyph' and (not isinstance(r[2],dict) or r[2].get('reason')!='transcript') for r in element.runs):continue
        chosen,_,error=quote_evidence._source_lines(element,lines)
        if error or not chosen:continue
        containing=[block for block in raw_blocks if any(line in chosen for line in block.get('lines',[]))]
        if any(any(line not in chosen for line in block.get('lines',[])) for block in containing):continue
        mapped[index]=chosen
    # Canonical mapping includes other top-level elements and empty separators;
    # use the existing block splitter rather than inferring element positions.
    from .build_epub import split_blocks
    blocks=split_blocks(source.html);mapping=json.loads(source.blocks_json)
    published=[node for node in current.documentElement.childNodes if node.nodeType==Node.ELEMENT_NODE and node.tagName=='p']
    for index,chosen in mapped.items():
        element=elements[index];em=_size(chosen)
        if len(chosen)<2 or not em or element.bbox[1]>=raw.height*skeleton.FOOTER_BAND:continue
        centers=[(line['bbox'][0]+line['bbox'][2])/2 for line in chosen]
        if (max(line['bbox'][0] for line in chosen)-min(line['bbox'][0] for line in chosen)>em*.5 and
                max(centers)-min(centers)<=em*.5):continue
        left=min(line['bbox'][0] for line in chosen)
        # Every continuation line shares the source inset. A first-line offset
        # is measured separately; centered/ragged left displays stay untouched.
        if any(abs(line['bbox'][0]-left)>em*.5 for line in chosen[1:]):continue
        first=(chosen[0]['bbox'][0]-left)/em
        if not 0<=first<=2.5:continue
        refs=[]
        for other_index,other_lines in mapped.items():
            other=elements[other_index]
            if other_index==index or other.column!=element.column or len(other_lines)<2:continue
            size=_size(other_lines)
            if not size or abs(size/em-1)>.15:continue
            box=other.bbox
            if box[2]-box[0]<raw.width*.4 or not (box[3]<=element.bbox[1] or box[1]>=element.bbox[3]):continue
            gap=min(abs(element.bbox[1]-box[3]),abs(box[1]-element.bbox[3]))
            if gap<em*.5:continue
            body_left=min(line['bbox'][0] for line in other_lines)
            if any(abs(line['bbox'][0]-body_left)>em*.5 for line in other_lines[1:]):continue
            inset=(left-body_left)/em
            if .5<=inset<=4:refs.append((gap,other_index,inset))
        if not refs:continue
        _,reference,inset=min(refs)
        canonical_block=_tree(blocks[mapping[str(index)]]).documentElement
        nodes=[n for n in canonical_block.childNodes if n.nodeType==Node.ELEMENT_NODE]
        if len(nodes)!=1 or nodes[0].tagName!='p':continue
        matches=[node for node in published if _shape(node)==_source_shape(nodes[0])]
        entry=dict(element=index,displayed=False,source_line_boxes=[line['bbox'] for line in chosen],
                   reference_element=reference,left_em=inset,first_line_em=first)
        audit['entries'].append(entry)
        if len(matches)!=1:entry['reason']='unique_complete_canonical_paragraph_unproved';continue
        entry['id']=hashlib.sha256(json.dumps([VERSION,source.identity,index,inset,first],sort_keys=True).encode()).hexdigest()
        matches[0].setAttribute('data-source-inset',entry['id']);entry['displayed']=True
    return (_fragment(current.documentElement) if any(e['displayed'] for e in audit['entries']) else fragment),audit
