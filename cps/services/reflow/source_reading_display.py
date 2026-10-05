"""Publication-only evidence at a proved retained occurrence; source stays sealed.

Inventory geometry establishes ownership. Whole owned-region codepoint equality
establishes offsets (whitespace is a coordinate separator, never a word search).
Compiler atom IDs establish the new location after regrouping. Any unsupported
mapping retains the qualified detail with an explicit placement refusal.
"""
import json
from xml.dom import minidom, Node
from . import source_readings as readings, source_inventory, enriched_source
from .structural_ops import ContractError

VERSION = 'source-reading-display-1'
GLYPH_HEIGHT_EM = 1.40
ANNOTATION_CLASS = 'source-reading-annotation'


def glyph_stylesheet():
    # Crop/font metadata and pixels remain intrinsic source evidence. Only the
    # inline display changes. Auto width retains aspect ratio at both font sizes.
    return '.source-glyph img[style*="vertical-align:baseline"] { height:%.2fem !important; width:auto; max-width:none; }' % GLYPH_HEIGHT_EM


def _tree(fragment):
    return minidom.parseString('<root xmlns:epub="http://www.idpf.org/2007/ops">'+fragment+'</root>')


def _fragment(root):
    return ''.join(n.toxml() for n in root.childNodes)


def _need(condition, reason):
    if not condition:
        raise _Unplaced(reason)


class _Unplaced(ValueError):
    pass


def _glyphs(book, page):
    from .native_text import image_name, descriptor, note_descriptor
    records = {}
    runs = [r for e in book.pages[page] for r in e.runs]
    runs += [r for n in book.notes if n.pno == page for r in getattr(n, 'glyph_runs', ())]
    for run in runs:
        if run[0] == 'glyph':
            name = image_name(run[2])
            _need(name not in records or records[name] == str(run[1]), 'ambiguous_glyph_codepoints')
            records[name] = str(run[1])
    for note in book.notes:
        if note.pno == page and note.uses_source_image and not note.glyph_runs:
            name = image_name(note_descriptor(note))
            _need(name not in records or records[name] == note.text, 'ambiguous_glyph_codepoints')
            records[name] = note.text
    return records


def _projection(node, glyphs):
    """Non-whitespace coordinates, plus actual source DOM endpoints.

    Only factory-generated evidence/navigation copy is omitted. Protected image
    pixels count as their immutable original run codepoints, never an OCR guess.
    """
    chars, locations = [], []
    def walk(n):
        if n.nodeType == Node.TEXT_NODE:
            value = n.data
            parent = n.parentNode
            if (parent.nodeType == Node.ELEMENT_NODE and
                    parent.getAttribute('class') == 'reflow-uncertain' and
                    parent.getAttribute('title') in ('number read from a damaged text layer',
                        'number or association read from a damaged text layer') and value.endswith(' (?)')):
                value = value[:-4]  # exact factory decoration, not source digits
            for i, char in enumerate(value):
                if not char.isspace():
                    chars.append(char); locations.append((n, i+1))
        elif n.nodeType == Node.ELEMENT_NODE:
            classes = set(n.getAttribute('class').split())
            if classes.intersection({'source-evidence-notice', 'source-reading-annotation', 'pdf-return', 'layout-note-backlink'}):
                return
            if 'source-glyph' in classes:
                images = n.getElementsByTagName('img')
                _need(len(images) == 1 and images[0].getAttribute('src') in glyphs, 'unmapped_source_glyph')
                for char in glyphs[images[0].getAttribute('src')]:
                    if not char.isspace():
                        chars.append(char); locations.append((n, None))
                return
            for child in n.childNodes:
                walk(child)
    walk(node)
    return ''.join(chars), locations


def _source_shape(node):
    """Verify complete source trees as well as their coordinate projection.

    Ignore only post-admission original-evidence notices and its exact figure
    inspection wrapper. No source resource, attribute or inline text may change.
    """
    if node.nodeType == Node.TEXT_NODE:
        return ('text',node.data)
    if node.nodeType != Node.ELEMENT_NODE:
        return None
    classes = set(node.getAttribute('class').split())
    if classes.intersection({'source-evidence-notice','source-evidence'}):
        return None
    if (node.tagName == 'a' and
        node.getAttribute('aria-label') in ('Inspect original figure and page details',
                                         'Inspect original page and enlarged details') and
        len(node.childNodes) == 1 and node.firstChild.nodeType == Node.ELEMENT_NODE and node.firstChild.tagName == 'img'):
        return _source_shape(node.firstChild)
    attributes = tuple(sorted((name,node.getAttribute(name)) for name in node.attributes.keys() if not name.startswith('xmlns')))
    children = []
    for child in node.childNodes:
        if node.tagName == 'root' and child.nodeType == Node.TEXT_NODE and not child.data.strip():
            continue
        shape = _source_shape(child)
        if shape is not None:
            children.append(shape)
    return node.tagName,attributes,tuple(children)


def _contained(box, outer):
    return (outer[0]-.0001 <= box[0] and outer[1]-.0001 <= box[1] and
            box[2] <= outer[2]+.0001 and box[3] <= outer[3]+.0001)


def _regions_for_elements(book, page, inventory):
    """Use retained line membership or exact retained glyph geometry as anchors.

    Joined legacy elements sometimes kept only the first region's line_boxes.
    A glyph still names its literal source crop, so it can anchor that later
    region. Full region/element equality below must verify the entire join.
    """
    rows = {r['id']:r for r in inventory['lines']}
    result = {}
    for region in inventory['regions']:
        if region['suggested_kind'] not in ('body','heading','caption'):
            continue
        lines = [rows[k]['source'] for k in region['line_ids']]
        candidates = []
        for i, element in enumerate(book.pages[page]):
            if element.kind not in ('p','h','caption'):
                continue
            line_anchor = any(readings._same_box(box, line.bbox)
                for box in element.line_boxes for line in lines)
            glyph_anchor = any(run[0] == 'glyph' and run[2]['page'] == page and
                any(readings._same_box(run[2]['bbox'], span.bbox) and str(run[1]) == span.text
                    for line in lines for span in line.spans) for run in element.runs)
            if line_anchor or glyph_anchor:
                candidates.append(i)
        if len(candidates) == 1:
            result[region['id']] = candidates[0]
        elif candidates:
            result[region['id']] = None
    return result


def _owned_codepoints(lines, page):
    """Replay the existing assembly's earned soft-hyphen seam, with offsets.

    Never normalize a selected token. Only an unprotected line-final soft hyphen
    which current stitch_runs removes under this source's printed vocabulary may
    be a consumed coordinate. Every other codepoint must agree in the full block.
    """
    from . import assemble
    text = ''.join(line.text for line in lines)
    vocabulary = {w.lower() for w in assemble._WORD.findall(text)}
    consumed = set();cursor = 0
    for left,right in zip(lines,lines[1:]):
        tail = left.text.rstrip()
        if tail.endswith('\xad'):
            a = assemble._line_runs(left,page,set(),set(),[],[],preserve_style=True)
            b = assemble._line_runs(right,page,set(),set(),[],[],preserve_style=True)
            joined = assemble.stitch_runs(a,b,heal=True,vocab=vocabulary)
            flat = lambda runs: ''.join(c for r in runs for c in str(r[1]) if not c.isspace())
            before = flat(a)+flat(b)
            expected = flat(a)[:-1]+flat(b)
            if flat(a).endswith('\xad') and flat(joined)==expected and flat(joined)!=before:
                consumed.add(cursor+len(tail)-1)
        cursor += len(left.text)
    kept = [(i,c) for i,c in enumerate(text) if not c.isspace() and i not in consumed]
    return text,kept,consumed


def _canonical_offset(book, source, raw, occurrence, glyphs):
    """Return a canonical nonspace offset, only after complete ownership proof."""
    inventory = book.source_inventory[source.page]
    segments = readings._segments(readings._selector(occurrence))
    bi, li = segments[0]['block'], segments[0]['line']
    rows = {r['id']:r for r in inventory['lines']}
    selected = [r for r in rows.values() if (r['block_index'],r['line_index']) == (bi,li)]
    _need(len(selected) == 1, 'ambiguous_inventory_line')
    selected = selected[0]
    owner = [o for o in inventory['ownership'] if o['line_id'] == selected['id']]
    _need(len(owner) == 1 and owner[0]['representation'] == 'text', 'occurrence_not_text_owned')
    region = next(r for r in inventory['regions'] if r['id'] == owner[0]['owner_id'])
    _need(region['geometry_status'] == 'valid' and not region['unmapped_lines'], 'unsupported_region_membership')
    dom = _tree(source.html).documentElement
    blocks = [n for n in dom.childNodes if n.nodeType == Node.ELEMENT_NODE]
    if region['suggested_kind'] == 'note':
        notes = [n for n in book.notes if n.pno == source.page]
        matches = [i for i,n in enumerate(notes) if readings._same_box(n.bbox,region['bbox'])]
        _need(len(matches) == 1, 'ambiguous_note_geometry')
        asides = [n for n in blocks if n.tagName == 'aside' and n.getAttribute('class') == 'footnote']
        _need(len(asides) == len(notes), 'note_block_mapping_differs')
        block = asides[matches[0]]
        line_ids = region['line_ids']
    else:
        assignments = _regions_for_elements(book,source.page,inventory)
        _need(region['id'] in assignments and assignments[region['id']] is not None, 'ambiguous_or_missing_element_owner')
        index = assignments[region['id']]
        related = [r for r in inventory['regions'] if assignments.get(r['id'], -1) == index]
        _need(all(not r['unmapped_lines'] and r['geometry_status']=='valid' for r in related), 'unsupported_joined_membership')
        line_ids = [k for r in related for k in r['line_ids']]
        mapping = json.loads(source.blocks_json)
        _need(str(index) in mapping, 'element_block_mapping_missing')
        block = blocks[mapping[str(index)]]
    _need(len(line_ids) == len(set(line_ids)) and line_ids.count(selected['id']) == 1, 'ambiguous_owned_line_order')
    raw_text, kept, consumed = _owned_codepoints([rows[k]['source'] for k in line_ids],source.page)
    canonical, locations = _projection(block,glyphs)
    _need(''.join(c for i,c in kept) == canonical, 'owned_region_codepoints_differ')
    prefix = ''.join(rows[k]['source'].text for k in line_ids[:line_ids.index(selected['id'])])
    last = segments[-1]
    line = raw.blocks[bi].lines[li]
    prefix += ''.join(s.text for s in line.spans[:last['span']])+line.spans[last['span']].text[:last['end']]
    end = sum(i < len(prefix) for i,c in kept)
    _need(not any(len(prefix)-len(occurrence['original']) <= i < len(prefix) for i in consumed), 'occurrence_crosses_consumed_source_seam')
    _need(0 < end <= len(canonical), 'occurrence_endpoint_missing')
    _need(canonical[end-len(occurrence['original']):end] == occurrence['original'], 'occurrence_endpoint_differs')
    before = 0
    for candidate in blocks:
        if candidate is block:
            return before+end
        before += len(_projection(candidate,glyphs)[0])
    raise _Unplaced('canonical_block_missing')


def _layout_offset(source, endpoint, glyphs, plan, compiled):
    """Translate canonical occurrence through current source atom identities."""
    from . import _layout_atoms as atoms
    baseline = atoms.prepare(source.html,source.identity,source.page)
    cursor = 0;target = None
    for atom in baseline['atoms']:
        value = _projection(_tree(atom['html']).documentElement,glyphs)[0]
        if cursor < endpoint <= cursor+len(value):
            target = (atom,endpoint-cursor);break
        cursor += len(value)
    _need(target is not None, 'canonical_atom_endpoint_missing')
    original, local = target
    contract = json.loads(plan.prepared.contract_json)
    _need(contract['identity'] == source.identity, 'layout_identity_differs')
    current = {a['id']:a for a in contract['atoms']}
    _need(original['id'] in current and current[original['id']]['html'] == original['html'], 'layout_atom_identity_differs')
    groups = json.loads(compiled.groups_json)
    ordered = [g for g in groups if g['role'] not in ('furniture','source_furniture')]
    ordered += [g for g in groups if g['role'] in ('furniture','source_furniture')]
    joins = {j['left']:j for j in json.loads(compiled.answer_json)['joins']}
    expected = '';new_endpoint = None
    for group in ordered:
        for identifier in group['ids']:
            value = _projection(_tree(current[identifier]['html']).documentElement,glyphs)[0]
            if identifier in joins and joins[identifier]['hyphen'] == 'drop':
                _need(value.endswith('-'), 'layout_join_projection_differs')
                value = value[:-1]
            if identifier == original['id']:
                _need(new_endpoint is None and local <= len(value), 'changed_or_duplicate_layout_endpoint')
                new_endpoint = len(expected)+local
            expected += value
    _need(new_endpoint is not None, 'layout_occurrence_missing')
    return new_endpoint, expected


def _insert(root, location, annotation):
    node, offset = location
    if offset is None:
        # A source glyph is protected: only its complete end is insertable.
        lift = node
        while lift.parentNode is not root and lift.parentNode.nodeType == Node.ELEMENT_NODE and lift.parentNode.tagName in ('a','sup'):
            _need(lift.nextSibling is None, 'endpoint_inside_protected_inline')
            lift = lift.parentNode
        lift.parentNode.insertBefore(annotation,lift.nextSibling)
        return
    # Lift an endpoint outside any existing source link/sup/uncertainty atom.
    current = node
    boundary = offset == len(node.data)
    lift = node
    while current.parentNode is not root:
        parent = current.parentNode
        if parent.nodeType != Node.ELEMENT_NODE:
            break
        boundary = boundary and current.nextSibling is None
        classes = set(parent.getAttribute('class').split())
        if parent.tagName in ('a','sup') or 'reflow-uncertain' in classes:
            _need(boundary, 'endpoint_inside_protected_inline')
            lift = parent
        current = parent
    if lift is not node:
        lift.parentNode.insertBefore(annotation,lift.nextSibling)
    else:
        tail = node.splitText(offset)
        node.parentNode.insertBefore(annotation,tail)


def render(book, doc, source, raw, fragment, *, plan=None, previous_source=None, previous_raw=None):
    """Reissue evidence and derive placement under current factory authority.

    Serialized reports, offsets and historical plans cannot authorize display.
    Returned audit is publication evidence; it never modifies the SourcePage.
    """
    source.validate(book)
    record = source.report().get('source_readings')
    if not record:
        return fragment, dict(version=VERSION,source_identity=source.identity,entries=[])
    base = enriched_source.prepare_source_page(book,source.page,json.loads(source.provenance_json),json.loads(source.records_json))
    readings.replay(book,doc,base,raw,source)
    source_inventory.validate(book.source_inventory[source.page],raw)
    compiled = None
    if plan is not None:
        compiled = plan.compile(book,doc,source_page=source,raw_page=raw,
            previous_source=previous_source,previous_raw=previous_raw)
    audit = dict(version=VERSION,source_identity=source.identity,raw_sha256=source_inventory.digest(raw),
        generated_class=ANNOTATION_CLASS,excluded_from_source_conservation=True,entries=[])
    dom = _tree(fragment);root = dom.documentElement
    pending = []
    for entry in record['entries']:
        row = dict(id=entry['id'],inline_placed=False)
        audit['entries'].append(row)
        if not entry['alternatives']:
            row['reason'] = 'no_qualified_alternative';continue
        try:
            glyphs = _glyphs(book,source.page)
            endpoint = _canonical_offset(book,source,raw,entry['occurrence'],glyphs)
            expected = _projection(_tree(source.html).documentElement,glyphs)[0]
            if compiled is not None:
                endpoint, expected = _layout_offset(source,endpoint,glyphs,plan,compiled)
            bound_fragment = compiled.page_html if compiled is not None else source.html
            _need(_source_shape(root) == _source_shape(_tree(bound_fragment).documentElement), 'current_output_source_tree_differs')
            observed, locations = _projection(root,glyphs)
            _need(observed == expected, 'current_output_projection_differs')
            _need(0 < endpoint <= len(locations), 'current_output_endpoint_missing')
            location = locations[endpoint-1]
            if location[1] is None:
                _need(endpoint==len(locations) or locations[endpoint] != location, 'endpoint_inside_source_glyph')
            row.update(canonical_endpoint=_canonical_offset(book,source,raw,entry['occurrence'],glyphs),
                output_endpoint=endpoint,proof='inventory_owned_region_codepoints_and_current_atom_order')
            pending.append((endpoint,location,entry,row))
        except _Unplaced as exc:
            row['reason'] = str(exc)
    # Descending order preserves the saved DOM text offsets for multiple entries.
    for endpoint,location,entry,row in sorted(pending,key=lambda x:x[0],reverse=True):
        marker = dom.createElement('span');marker.setAttribute('class',ANNOTATION_CLASS)
        marker.setAttribute('id','reading-return-'+entry['id'])
        label = 'Synthetic fixture; ' if entry['synthetic'] else 'Independently reviewed; '
        marker.appendChild(dom.createTextNode(' ['+label+'Original: '+entry['original']+'; '))
        proposals = '; '.join('Proposed reading: %s (%.1f%%)' % (a['text'],100*a['confidence']) for a in entry['alternatives'])
        marker.appendChild(dom.createTextNode(proposals+'; original retained. '))
        link = dom.createElement('a');link.setAttribute('href','original-p%04d.xhtml#%s' % (source.page,entry['id']))
        link.appendChild(dom.createTextNode('Inspect source'));marker.appendChild(link);marker.appendChild(dom.createTextNode(']'))
        try:
            _insert(root,location,marker)
            row['inline_placed'] = True
            row['return_id'] = marker.getAttribute('id')
        except _Unplaced as exc:
            row['reason'] = str(exc)
    if not any(r['inline_placed'] for r in audit['entries']):
        return fragment,audit
    return _fragment(root),audit
