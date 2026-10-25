"""Account for source occurrences independently of the chosen transcription.

A chosen word must bind to this occurrence's own complete bounds. Unplaced
occurrences need published original ink; a different copy of the same spelling
and an image's bounding envelope do not establish that coverage.
"""
import unicodedata
import re
from collections import defaultdict
from difflib import SequenceMatcher
from xml.etree import ElementTree as ET


def _key(text):
    text=re.sub(r'<n:[^>]+>','',text)
    return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold()if c.isalnum())


def _contains(outer,inner):
    width=max(.01,inner[2]-inner[0]);height=max(.01,inner[3]-inner[1])
    return (min(outer[2],inner[2])-max(outer[0],inner[0])>=.9*width
            and min(outer[3],inner[3])-max(outer[1],inner[1])>=.9*height)


def _print_contains(outer,inner):
    """Print retention needs the complete occurrence, allowing only rounding."""
    epsilon=.01
    return (outer[0]<=inner[0]+epsilon and outer[1]<=inner[1]+epsilon
            and outer[2]>=inner[2]-epsilon and outer[3]>=inner[3]-epsilon)


def classify(words,tokens,boxes,notes,images,pno):
    """Return one explicit live/print/missing disposition per lexical occurrence."""
    rows=[];conserved={}
    # One proved published word can own several source glyph fragments or an
    # end-of-line division. Exact letters and one unique physical envelope
    # account for those occurrences without inventing another uncertainty mark.
    for j,token in enumerate(tokens):
        if j not in boxes or token.startswith('<n:'):continue
        literal=_key(token);groups=[]
        for first in range(len(words)):
            joined=''
            for last in range(first,min(first+8,len(words))):
                joined+=_key(words[last]['text'])
                if len(joined)>len(literal):break
                if joined==literal and all(_contains(boxes[j],w['bbox'])for w in words[first:last+1]):
                    groups.append((first,last+1))
        if len(groups)==1:
            for index in range(*groups[0]):conserved.setdefault(index,set()).add(j)
    for index,word in enumerate(words):
        if not _key(word['text']):continue
        box=word['bbox'];placements=list(conserved.get(index,()))
        for j,token in enumerate(tokens):
            if token.startswith('<n:')or j not in boxes:continue
            slot=boxes[j]
            # A shared paragraph envelope cannot prove an individual word.
            # An explicit adjudication source inventory may bind a multiword edit.
            note=notes.get(j,{})
            named=any(_key(t)==_key(word['text'])for t in note.get('source',[]))
            same=(_key(token)==_key(word['text']))
            overlaps=(min(slot[2],box[2])-max(slot[0],box[0])>=.3*min(slot[2]-slot[0],box[2]-box[0])
                and min(slot[3],box[3])-max(slot[1],box[1])>=.5*min(slot[3]-slot[1],box[3]-box[1]))
            if not (_contains(slot,box)or same and overlaps):continue
            if (same or named)and (
                    slot[2]-slot[0]<=3*max(.01,box[2]-box[0])or named):
                if j not in placements:placements.append(j)
        live=[j for j in placements if not notes.get(j,{}).get('source_image_only')]
        row=dict(source_index=index,source_text=word['text'],source_bbox=list(box),
                 disposition='live'if len(live)==1 else 'missing',published_indices=live)
        if len(live)!=1:
            assets=[]
            for name,image in images.items():
                if image.get('page')!=pno or not image.get('bbox') or image.get('coverage_eligible') is False:continue
                parts=image.get('source_parts')or [image['bbox']]
                if any(_print_contains(part['bbox']if isinstance(part,dict)else part,box)for part in parts):assets.append((name,image))
            if assets:
                name,_=min(assets,key=lambda item:(item[1]['bbox'][2]-item[1]['bbox'][0])*
                    (item[1]['bbox'][3]-item[1]['bbox'][1]))
                row.update(disposition='print',image=name,published_indices=placements)
        rows.append(row)
    return rows


def summary(rows):
    rows=[row for row in rows if row['disposition']!='excluded']
    return dict(inventory=len(rows),live=sum(r['disposition']=='live'for r in rows),
                print=sum(r['disposition']=='print'for r in rows),
                missing=sum(r['disposition']=='missing'for r in rows))


def _surviving_indices(before,after):
    """Bind unchanged characters through final layout joins, never bag counts."""
    a=[_key(t)for t in before];b=[_key(t)for t in after];found=set()
    for kind,i,z,j,y in SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
        if kind=='equal':found.update(range(i,z))
        elif kind=='replace' and ''.join(a[i:z])==''.join(b[j:y]):
            found.update(range(i,z))
    return found


def _text(node):
    tag=node.tag.rsplit('}',1)[-1];classes=set(node.get('class','').split())
    if 'reflow-flagged-reading'in classes:
        live=next((c for c in node if 'reflow-plausible'in c.get('class','').split()),None)
        return ''.join(live.itertext())if live is not None else ''
    if tag=='img' or classes.intersection({'source-disclosure-ref','source-reading-annotation',
            'source-word-image','vision-disclosure'}):return ' '
    return (node.text or '')+''.join(_text(c)+(c.tail or '')for c in node)


def _published_resource(name,package):
    """Resolve the writer's declared aliases to an actual encoded resource."""
    aliases=getattr(package,'aliases',{});seen=set()
    while name in aliases:
        if name in seen:raise ValueError('Cyclic image resource alias')
        seen.add(name);name=aliases[name]
    return name if name in package.images else None


def _furniture_exclusions(inventory,pno,frame,display):
    """Carry exact assembled furniture ownership into the final reading frame.

    Native capture uses unrotated PDF coordinates. Recovered capture explicitly
    names its already normalized reading frame; it must match this display.
    A whole source line, its unique owner and every current word must agree.
    """
    if not inventory:return []
    from . import source_inventory
    try:
        source_inventory.validate(inventory)
        if inventory['page']!=pno:return []
        geometry=inventory['source_geometry']
        if geometry:
            if display is None or geometry!=dict(space='reading',orientation=display.angle,
                    source_rotation=display.page.rotation,page_rect=list(display.page.rect)):
                return []
            transform=lambda box:list(box)
        else:
            transform=(lambda box:list(display.reading_rect(box)))if display is not None else lambda box:list(box)
        lines={row['id']:row['source']for row in inventory['lines']}
        owners={row['line_id']:row for row in inventory['ownership']}
        result=[]
        for region in inventory['regions']:
            # Other furniture classifications do not grant a final exclusion.
            if (region['suggested_kind']!='furniture' or region['geometry_status']!='valid'
                    or region['reason'] not in ('folio','sequence_folio')):continue
            for identifier in region['line_ids']:
                owner=owners.get(identifier,{})
                if owner.get('representation')!='text' or owner.get('owner_id')!=region['id']:continue
                line=lines[identifier]
                if not _print_contains(region['bbox'],line.bbox):continue
                if any(not _print_contains(line.bbox,span.bbox)for span in line.spans):continue
                box=transform(line.bbox)
                words=[word for word in frame if _contains(box,word['bbox'])]
                if not words or any(not _print_contains(box,word['bbox'])for word in words):continue
                if not _key(line.text) or _key(' '.join(word['text']for word in words))!=_key(line.text):continue
                result.append((box,'source_inventory_furniture'))
        return result
    except (ValueError,KeyError,TypeError,AttributeError):
        # An invalid or stale inventory cannot grant an exclusion. Ordinary
        # publication coverage will retain any unmatched occurrence as print.
        return []


def final_publication(documents,records,frames,images,package,displays,homes,*,source_inventory=None):
    """Audit the attributed source frame against the actual final documents.

    The frame is native PDF occurrences or the independently recognized original
    raster, augmented by admitted local occurrences. It is not a claim that OCR
    can enumerate every glyph on a scan. Original page contexts retain ink that
    neither recognition pass identified. Furniture is excluded only at an
    explicitly attributed running-head or folio territory.
    """
    actual=defaultdict(list);used=set();identifiers=set();identified={}
    for html in documents.values():
        root=ET.fromstring(html)
        for node in root.iter():
            if node.get('id'):
                identifiers.add(node.get('id'));identified[node.get('id')]=node
            if node.tag.rsplit('}',1)[-1]=='img':used.add(node.get('src',''))
            if node.get('data-source-page')is not None and node.get('data-source-block'):
                actual[(int(node.get('data-source-page')),node.get('data-source-block'))].extend(_text(node).split())
    published={name:item for name,item in images.items()if name in used and _published_resource(name,package)is not None
        and item.get('coverage_eligible') is not False}
    pages={}
    for pno,frame in frames.items():
        blocks=records[pno].get('blocks',[])+records[pno].get('native_note_adjudication',[]);claims=[];extras=[];exclusions=[]
        source_page=(source_inventory or {}).get(pno,(source_inventory or {}).get(str(pno)))
        for block in blocks:
            exclusions.extend((box,'measured_contents_dot_leader')for box in block.get('decorative_source_regions',[]))
            if block.get('role')in('running_head','folio'):
                exclusions.extend((box,block['role'])for box in block.get('source_regions',[block.get('bbox')])if box)
                continue
            label=block.get('printed_label_occurrence')
            if label and label['target']in identified:
                visible=_text(identified[label['target']]).split()
                if visible and _key(visible[0])==_key(label['text']):
                    claims.append(dict(source_text=label['text'],source_bbox=label['bbox'],
                        disposition='live',typed_reference=label['target'],published_indices=[],block=block['id']))
            expected=block.get('published_tokens',[])
            surviving=_surviving_indices(expected,actual.get((pno,block['id']),[]))
            rows=block.get('occurrence_coverage',[])
            if not rows and block.get('native_source_occurrences'):
                source=block['native_source_occurrences'];placements={}
                source_keys=[_key(w['text'])for w in source];target=[_key(t)for t in expected]
                for kind,a,z,b,y in SequenceMatcher(None,source_keys,target,autojunk=False).get_opcodes():
                    if kind=='equal':
                        for k in range(z-a):placements[b+k]=source[a+k]['bbox']
                    elif kind=='replace'and y-b==1 and ''.join(source_keys[a:z])==target[b]:
                        placements[b]=[min(w['bbox'][0]for w in source[a:z]),min(w['bbox'][1]for w in source[a:z]),
                            max(w['bbox'][2]for w in source[a:z]),max(w['bbox'][3]for w in source[a:z])]
                notes={i:dict(source_image_only=True,source=[expected[i]])for i in block.get('source_image_words',[])if i<len(expected)}
                rows=classify(source,expected,placements,notes,published,pno)
            for row in rows:
                value=dict(row,block=block['id'])
                if (row['disposition']=='live'and row.get('typed_reference')not in identifiers
                        and not any(i in surviving for i in row['published_indices'])):
                    value.update(disposition='missing',method='removed_after_compilation')
                claims.append(value)
                if not any(_contains(w['bbox'],row['source_bbox'])for w in frame):
                    extras.append(dict(text=row['source_text'],bbox=row['source_bbox']))
        exclusions.extend(_furniture_exclusions(source_page,pno,frame,displays.get(pno)))
        inventory=list(frame)
        for word in extras:
            if not any(_contains(w['bbox'],word['bbox'])for w in inventory):inventory.append(word)
        rows=[]
        for index,word in enumerate(inventory):
            if not _key(word['text']):continue
            box=word['bbox'];excluded=next((role for region,role in exclusions if _contains(region,box)),None)
            if excluded and excluded!='source_inventory_furniture':
                rows.append(dict(source_index=index,source_text=word['text'],source_bbox=box,
                    disposition='excluded',rule=excluded));continue
            matching=[r for r in claims if _key(r['source_text'])==_key(word['text'])
                and (_contains(box,r['source_bbox'])or _contains(r['source_bbox'],box))]
            if excluded and not matching:
                rows.append(dict(source_index=index,source_text=word['text'],source_bbox=box,
                    disposition='excluded',rule=excluded));continue
            live=[r for r in matching if r['disposition']=='live']
            row=dict(source_index=index,source_text=word['text'],source_bbox=box,disposition='missing')
            if live:row.update(disposition='live',blocks=sorted({r['block']for r in live}))
            else:
                for name,image in published.items():
                    if image.get('page')!=pno:continue
                    parts=image.get('source_parts')or [image.get('bbox')]
                    if any(part and _print_contains(part.get('bbox')if isinstance(part,dict)else part,box)for part in parts):
                        row.update(disposition='print',image=name,kind=image.get('kind'));break
            rows.append(row)
        missing=[r for r in rows if r['disposition']=='missing']
        if missing:
            from .unit_source import source_unit
            fragment,asides,case=source_unit(displays[pno],pno,'publication-coverage','other',
                package,images,'Source occurrences without final live-word coverage are retained as original print')
            href=homes[pno]if pno in homes else homes[str(pno)]
            # This is an inspection channel, not another prose paragraph. Keep
            # its control in the notes lane with the retained original page.
            context='<aside class="footnote vision-disclosure">'+fragment+'</aside>'+''.join(asides)
            documents[href]=documents[href].replace('</body>',context+'</body>')
            if _published_resource(case['image'],package)is None:raise ValueError('Occurrence coverage source image was not published')
            for row in missing:row.update(disposition='print',image=case['image'],
                kind='publication_coverage_source_context',source_reference=case['source_reference'])
        pages[pno]=dict(rows=rows,**summary([r for r in rows if r['disposition']!='excluded']),
            excluded=sum(r['disposition']=='excluded'for r in rows))
    return dict(pages=pages,totals={key:sum(p[key]for p in pages.values())for key in ('inventory','live','print','missing','excluded')},
        frame='native_pdf_occurrences_or_independent_original_raster_ocr_plus_admitted_local_occurrences',
        limitation='OCR cannot enumerate all printed ink; retained original-page contexts remain available for unrecognized ink')
