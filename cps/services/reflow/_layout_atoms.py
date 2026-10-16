"""Private immutable canonical atom operations. No factory/publication authority.

Derived from the measured v5 experiment contract; ownership admission lives in
layout_ops. This module accepts only that module's regenerated canonical source.
"""
from .structural_ops import ContractError as Invalid
VERSION = 'canonical-layout-atoms-2'
ROLES = {'paragraph','heading1','heading2','heading3','quote','lineblock','furniture','source','note','source_furniture'}

import copy, hashlib, html, json, re
from xml.etree import ElementTree as ET
from collections import Counter

# The publication note pass recognizes the standard EPUB prefix. Keep it
# stable across XML parsing so it cannot add a duplicate expanded attribute.
ET.register_namespace('epub', 'http://www.idpf.org/2007/ops')

def need(value, reason):
    if not value:
        raise Invalid(reason)

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def tag(node):
    return node.tag.split('}')[-1]

def xml(node):
    return ET.tostring(node, encoding='unicode', short_empty_elements=True)

def text(node):
    return ''.join(node.itertext())

def can_be_source_furniture(block):
    """Exclude known semantic containers; raster classification needs review."""
    attributes = block.get('attributes', {})
    classes = set(attributes.get('class', '').split())
    types = set(attributes.get('{http://www.idpf.org/2007/ops}type', '').split())
    return (block['opaque'] and block['kind'] in ('p', 'div', 'figure', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6')
            and not classes.intersection({'source-evidence-notice', 'source-check-notice', 'source-note-unbound'})
            and not types.intersection({'footnote', 'endnote', 'rearnote'}))

def can_be_source_note(block):
    """Eligibility only: independent review must establish printed note semantics.

    Keep a source list's complete immutable tree; never expose or rewrite its
    labels/items. Figures, headings and arbitrary opaque prose stay barriers.
    """
    return (block['opaque'] and block['kind'] in ('ol', 'ul') and
            set(block.get('attributes', {}).get('class', '').split()) == {'source-list'})


def is_raster_block(node):
    """Exact factory display shape; arbitrary image-only blocks stay opaque."""
    return (tag(node) == 'p' and not node.attrib and len(node) == 1
            and not text(node).strip() and node[0].tag == 'span'
            and node[0].attrib == {'class': 'source-raster'} and len(node[0]) == 1
            and node[0][0].tag == 'img' and not len(node[0][0])
            and set(node[0][0].attrib) == {'src', 'alt'}
            and re.fullmatch(r'images/fig_p\d{4,}_\d+\.jpg', node[0][0].get('src', '')) is not None)


def prepare(fragment, identity, page, blocks=None, wrap_lefts=(), wrap_rights=()):
    """Preserve every source node; indivisible assets retain their exact DOM tree."""
    root = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">' + fragment + '</root>')
    need(not (root.text or '').strip(), 'top-level source text')
    atoms = []
    groups = []

    def add(fragment, visible, block, protected=False):
        atom = dict(id='a' + str(len(atoms)), html=fragment, text=visible, block=block, protected=protected)
        atoms.append(atom)
        return atom['id']

    def walk(element, ancestors, block):
        from xml.dom import minidom, Node
        dom = minidom.parseString(xml(element))
        node = dom.documentElement
        bounds = {}
        atomic = set()
        parts = []
        offset = 0
        image_slots = set()

        def inspect(n):
            nonlocal offset
            lo = offset
            if n.nodeType == Node.TEXT_NODE:
                parts.append(n.data)
                offset += len(n.data)
            elif n.nodeType == Node.ELEMENT_NODE:
                for child in n.childNodes:
                    inspect(child)
                if n.tagName == 'img' and offset == lo:
                    # Give an empty image a position in the slicing coordinate
                    # space, not a generated source word. An enclosing link or
                    # adjoining word still becomes one indivisible source atom.
                    image_slots.add(offset)
                    parts.append('\ufffc')
                    offset += 1
                classes = set(n.getAttribute('class').split())
                if n is not node and (n.hasAttribute('id') or n.hasAttribute('name') or n.tagName in ('a', 'sup', 'img', 'br') or classes.intersection({'reflow-uncertain', 'source-glyph', 'noteref', 'noteref-unresolved', 'source-raster'})):
                    need(offset > lo, 'zero-length protected inline source requires complete block')
                    atomic.add(id(n))
            else:
                raise Invalid('unsupported source node')
            bounds[id(n)] = (lo, offset)
        inspect(node)
        visible = ''.join(parts)
        ranges = [list(m.span()) for m in re.finditer('\\S+', visible)]
        # Union all word/protected intervals together. Incremental mutation in
        # set iteration order can truncate an enclosing protected interval when
        # its previously merged children are no longer position-sorted.
        ranges.extend(list(bounds[key]) for key in atomic)
        ranges.sort()
        merged = []
        for lo, hi in ranges:
            if merged and lo < merged[-1][1]:
                merged[-1][1] = max(hi, merged[-1][1])
            else:
                merged.append([lo, hi])

        def sliced(n, lo, hi):
            a, b = bounds[id(n)]
            if b <= lo or a >= hi:
                return None
            if lo <= a and b <= hi:
                return n.cloneNode(True)
            need(id(n) not in atomic, 'split protected source atom')
            if n.nodeType == Node.TEXT_NODE:
                return dom.createTextNode(n.data[max(0, lo - a):min(b - a, hi - a)])
            out = n.cloneNode(False)
            for child in n.childNodes:
                part = sliced(child, lo, hi)
                if part is not None:
                    out.appendChild(part)
            return out
        for lo, hi in merged:
            fragment = ''.join((part.toxml() for child in node.childNodes if (part := sliced(child, lo, hi)) is not None))
            protected = any((bounds[k][0] < hi and bounds[k][1] > lo for k in atomic))
            source_text = ''.join(char for pos, char in enumerate(visible[lo:hi], lo)
                                  if pos not in image_slots)
            add(fragment, source_text, block, protected)
    for i, node in enumerate(root):
        need(not (node.tail or '').strip(), 'top-level tail')
        first = len(atoms)
        classes = set(node.get('class', '').split())
        image_only = not text(node).strip() and bool(node.findall('.//img'))
        raster = is_raster_block(node)
        opaque = (tag(node) not in ('p', 'h1', 'h2', 'h3') or bool(node.attrib)
                  or bool(node.findall('.//br')) or (image_only and not raster))
        if opaque:
            owned = copy.deepcopy(node)
            owned.tail = None
            add(xml(owned), text(owned), i, True)
        else:
            try:
                walk(node, [], i)
            except Invalid:
                del atoms[first:]
                owned = copy.deepcopy(node)
                owned.tail = None
                add(xml(owned), text(owned), i, True)
                opaque = True
        if raster:
            need(len(atoms) == first + 1, 'source raster must be indivisible')
            atoms[first]['raster_region'] = dict(page=page, bbox=(blocks or {}).get(i))
        need(len(atoms) > first, 'empty canonical block')
        groups.append(dict(id='b' + str(i), range=[atoms[first]['id'], atoms[-1]['id']], kind=tag(node), opaque=opaque, attributes=dict(node.attrib), bbox=(blocks or {}).get(i)))
    source = dict(version=VERSION, identity=identity, page=page, atoms=atoms, blocks=groups, wrap_lefts=list(wrap_lefts), wrap_rights=list(wrap_rights))
    need(set(wrap_lefts) | set(wrap_rights) <= set((a['id'] for a in atoms)), 'unknown trusted wrapping endpoint')
    source['snapshot'] = digest(source)
    return source

def validate(source, answer):
    original = dict(source)
    original.pop('snapshot', None)
    need(digest(original) == source.get('snapshot'), 'source bytes changed')
    need(isinstance(answer, dict) and {'snapshot', 'groups', 'joins'} <= set(answer) <= {'snapshot', 'groups', 'joins', 'continuation', 'boundary_join', 'note_bindings', 'emphasis'}, 'response fields')
    need(answer['snapshot'] == source['snapshot'], 'source identity changed')
    need(isinstance(answer['groups'], list), 'groups')
    atoms = source['atoms']
    ids = {a['id']: i for i, a in enumerate(atoms)}
    seen = []
    compiled = []
    opaque = {b['range'][0] for b in source['blocks'] if b['opaque']}
    furniture_ids = {b['range'][0] for b in source['blocks'] if can_be_source_furniture(b)}
    note_ids = {b['range'][0] for b in source['blocks'] if can_be_source_note(b)}
    for g in answer['groups']:
        need(isinstance(g, dict) and set(g) == {'role', 'ranges'}, 'group fields')
        need(g['role'] in ROLES, 'role')
        need(isinstance(g['ranges'], list) and g['ranges'], 'empty ranges')
        ordered = []
        for pair in g['ranges']:
            need(isinstance(pair, list) and len(pair) == 2 and all((isinstance(x, str) and x in ids for x in pair)), 'unknown atom')
            a, b = map(ids.get, pair)
            need(a <= b, 'reversed atom range')
            ordered.extend(atoms[a:b + 1])
        own = [a['id'] for a in ordered]
        seen.extend(own)
        if opaque.intersection(own):
            need(len(own) == 1 and (g['role'] in ('source', 'source_furniture') or
                (g['role'] == 'note' and own[0] in note_ids)), 'protected source block must remain intact')
        else:
            need(g['role'] not in ('source', 'source_furniture'), 'source role requires complete protected block')
        if g['role'] == 'source_furniture':
            need(own[0] in furniture_ids, 'known semantic source block cannot become furniture')
        if g['role'] == 'furniture':
            need(not any((a['protected'] and not a.get('raster_region') for a in ordered)), 'protected inline cannot become furniture')
        compiled.append((g['role'], ordered))
    need(Counter(seen) == Counter(ids.keys()), 'missing or duplicate source atom')
    need(not any((not b['opaque'] for b in source['blocks'])) or any((role not in ('source', 'source_furniture', 'furniture') for role, seq in compiled)), 'all source prose classified as furniture')
    need(isinstance(answer['joins'], list), 'joins')
    used = set()
    for j in answer['joins']:
        need(isinstance(j, dict) and set(j) == {'left', 'right', 'hyphen'}, 'join fields')
        left, right = (j['left'], j['right'])
        need(isinstance(left, str) and isinstance(right, str) and (left in ids) and (right in ids), 'join atom')
        need(j['hyphen'] in ('keep', 'drop'), 'hyphen choice')
        need(left in source.get('wrap_lefts', []) and right in source.get('wrap_rights', []), 'join lacks trusted printed-line endpoints')
        a, b = (atoms[ids[left]], atoms[ids[right]])
        need(not a['protected'] and (not b['protected']), 'protected join')
        need(len(a['text']) > 1 and a['text'].endswith('-') and b['text'][0].isalpha(), 'not a split word')
        need(left not in used and right not in used, 'overlapping joins')
        used.update((left, right))
        need(any((role not in ('source', 'source_furniture', 'furniture') and any((x['id'] == left and y['id'] == right for x, y in zip(seq, seq[1:]))) for role, seq in compiled)), 'join endpoints not adjacent in body')
    continuation = answer.get('continuation', False)
    boundary = answer.get('boundary_join')
    need(type(continuation) is bool, 'continuation flag')
    if continuation:
        need(bool(source.get('previous')), 'continuation lacks previous source')
    if boundary is not None:
        need(continuation, 'boundary without continuation')
        need(isinstance(boundary, dict) and set(boundary) == {'left', 'right', 'hyphen'}, 'boundary fields')
        previous = source['previous']
        known = {a['id']: a for a in previous['atoms']}
        left, right = (boundary['left'], boundary['right'])
        need(isinstance(left, str) and isinstance(right, str) and (left in known) and (right in ids), 'unknown boundary atom')
        need(left in previous['wrap_lefts'] and right in source['wrap_rights'], 'boundary lacks printed endpoints')
        need(not known[left]['protected'] and (not atoms[ids[right]]['protected']), 'protected boundary atom')
        need(boundary['hyphen'] in ('keep', 'drop'), 'boundary hyphen choice')
    from . import _layout_notes
    _layout_notes.checked(source, answer, compiled)
    from . import _layout_emphasis
    _layout_emphasis.checked(source, answer, compiled)
    return compiled

def render(source, answer):
    compiled = validate(source, answer)
    from . import _layout_notes as notes
    bindings = notes.checked(source, answer, compiled)
    references = {}
    for b in bindings:
        references.setdefault(b.get('atom', b['reference']), []).append(b)
    labels = {b['note']: b for b in bindings}
    from . import _layout_emphasis
    emphasis = _layout_emphasis.checked(source, answer, compiled)
    starts = {e['first']: e for e in emphasis}
    ends = {e['last'] for e in emphasis}
    body = []
    furniture = []
    tags = {'paragraph': 'p', 'heading1': 'h1', 'heading2': 'h2', 'heading3': 'h3', 'quote': 'blockquote', 'lineblock': 'p', 'furniture': 'p', 'note': 'aside'}
    joins = {j['left']: j for j in answer['joins']}

    def content(atom):
        fragment = atom['html']
        if atom['id'] in joins and joins[atom['id']]['hyphen'] == 'drop':
            from xml.dom import minidom, Node
            dom = minidom.parseString('<root xmlns:epub="http://www.idpf.org/2007/ops">' + fragment + '</root>')

            def texts(node):
                for child in node.childNodes:
                    if child.nodeType == Node.TEXT_NODE:
                        yield child
                    else:
                        yield from texts(child)
            last = list(texts(dom.documentElement))[-1]
            need(last.data.endswith('-'), 'source join endpoint changed')
            last.data = last.data[:-1]
            fragment = ''.join((n.toxml() for n in dom.documentElement.childNodes))
        if atom['id'] in references:
            selected = references[atom['id']]
            if 'atom' in selected[0]:
                original = notes.tree(fragment).text or ''
                parts, offset = [], 0
                for b in sorted(selected, key=lambda item:item['start']):
                    parts.append(html.escape(original[offset:b['start']]))
                    anchor = ET.Element('a', notes.attributes(b['ref_id'], b['note_id'])[0])
                    anchor.text = original[b['start']:b['end']]
                    parts.append(xml(anchor)); offset = b['end']
                parts.append(html.escape(original[offset:]))
                fragment = ''.join(parts)
            else:
                b = selected[0]
                sup = notes.tree(fragment)[0]
                anchor = ET.SubElement(sup, 'a', notes.attributes(b['ref_id'], b['note_id'])[0])
                anchor.text, sup.text = sup.text, None
                fragment = xml(sup)
        elif atom['id'] in labels:
            b = labels[atom['id']]
            fragment = notes.wrap(fragment, notes.attributes(b['ref_id'], b['note_id'])[2])
        return fragment
    for index, (role, atoms) in enumerate(compiled):
        if role in ('source', 'source_furniture'):
            result = atoms[0]['html']
        else:
            line_ends = {r[1] for r in answer['groups'][index]['ranges']} if role == 'lineblock' else set()
            parts = []
            for i, atom in enumerate(atoms):
                if atom['id'] in starts:
                    parts.append('<strong id="' + starts[atom['id']]['id'] + '">')
                parts.append(content(atom))
                if atom['id'] in ends:
                    parts.append('</strong>')
                if i + 1 < len(atoms):
                    parts.append('' if atom['id'] in joins else '<br />' if atom['id'] in line_ends else ' ')
            result = '<' + tags[role] + (' class="source-note-unbound"' if role == 'note' else '') + '>' + ''.join(parts) + '</' + tags[role] + '>'
        if role == 'note' and atoms[0]['id'] in labels:
            b = labels[atoms[0]['id']]
            node = notes.tree(result)[0]
            node.tag = 'div'
            node.attrib.clear()
            node.attrib.update(notes.attributes(b['ref_id'], b['note_id'])[1])
            result = xml(node)
        (furniture if role in ('furniture', 'source_furniture') else body).append(result)
    return dict(body='\n'.join(body), furniture='\n'.join(furniture), snapshot=source['snapshot'])

def protected_constraints(source):
    """Expose existing mechanical eligibility and flow, never semantic labels."""
    from . import _layout_flow
    opaque = {b['range'][0]: b for b in source['blocks'] if b['opaque']}
    result = []
    for atom in source['atoms']:
        block = opaque.get(atom['id'])
        if block:
            roles = ['source']
            if can_be_source_furniture(block): roles.append('source_furniture')
            if can_be_source_note(block): roles.append('note')
            result.append(dict(atom=atom['id'], allowed_roles=roles,
                flow_by_role={r: 'separate' if _layout_flow.separate(r, [atom]) else 'barrier' for r in roles},
                binding_authority='none' if can_be_source_note(block) else 'existing_source_only'))
        elif atom['protected']:
            roles = ROLES - {'source', 'source_furniture'}
            if not atom.get('raster_region'): roles = roles - {'furniture'}
            result.append(dict(atom=atom['id'], allowed_roles=sorted(roles),
                indivisible=True, binding_authority='supplied_note_candidates_only'))
    return result


def model_view(source):
    from . import _layout_notes
    candidates = _layout_notes.candidates(source)
    metadata = {'note_candidates': candidates} if candidates['references'] and candidates['labels'] else {}
    from . import _layout_emphasis
    metadata['emphasis_ranges'] = _layout_emphasis.eligible_ranges(source)
    metadata['protected_constraints'] = protected_constraints(source)
    return metadata | {k: v for k, v in source.items() if k not in ('atoms', 'blocks')} | {'atoms': [{k: v for k, v in a.items() if k not in ('html', 'block')} for a in source['atoms']], 'protected_blocks': [b for b in source['blocks'] if b['opaque']]}

def bind(source, raw):
    result = copy.deepcopy(source)
    blocks = {i: b for i, b in enumerate(source['blocks'])}
    atoms = source['atoms']
    lines = [line for block in raw['blocks'] for line in block.get('lines', [])]
    line_words = [re.findall('\\S+', ''.join((sp['text'] for sp in line.get('spans', [])))) for line in lines]

    def inside(a, b):
        return bool(a and b and (len(a) == len(b) == 4) and (b[0] - 1.5 <= a[0]) and (b[1] - 1.5 <= a[1]) and (a[2] <= b[2] + 1.5) and (a[3] <= b[3] + 1.5))
    mapped = []
    for index, (line, words) in enumerate(zip(lines, line_words)):
        if not words:
            continue
        matches = []
        for start, a in enumerate(atoms):
            if blocks[a['block']]['opaque'] or not a['text'].split() or a['text'].split()[0] != words[0]:
                continue
            collected = []
            for end in range(start, len(atoms)):
                if blocks[atoms[end]['block']]['opaque']:
                    break
                collected.extend(atoms[end]['text'].split())
                if collected == words:
                    matches.append((start, end))
                if len(collected) >= len(words):
                    break
        geometric = [(lo, hi) for lo, hi in matches if atoms[lo]['block'] == atoms[hi]['block'] and inside(line['bbox'], blocks[atoms[lo]['block']]['bbox'])]
        binding = 'complete_line_geometry'
        if len(geometric) == 1:
            match = geometric[0]
        elif not geometric and len(matches) == 1 and (len(words) >= 2) and (line_words.count(words) == 1):
            match = matches[0]
            binding = 'unique_complete_line'
        else:
            continue
        mapped.append(dict(source_line=index, bbox=line['bbox'], bounds=match, binding=binding))
    claims = Counter((i for m in mapped for i in range(m['bounds'][0], m['bounds'][1] + 1)))
    mapped = [m for m in mapped if all((claims[i] == 1 for i in range(m['bounds'][0], m['bounds'][1] + 1)))]
    printed = []
    evidence = []
    for m in mapped:
        lo, hi = m['bounds']
        printed.append(dict(source_line=m['source_line'], bbox=m['bbox'], ranges=[[atoms[lo]['id'], atoms[hi]['id']]]))
        for side, index in [('left', hi), ('right', lo)]:
            atom = atoms[index]
            word = atom['text']
            if atom['protected'] or not word:
                continue
            if side == 'left' and (not (len(word) > 1 and word.endswith('-'))):
                continue
            if side == 'right' and (not word[0].isalpha()):
                continue
            if sum((a['text'] == word for a in atoms if a['block'] == atom['block'])) != 1:
                continue
            evidence.append(dict(side=side, atom=atom['id'], source_line=m['source_line'], bbox=m['bbox'], text=word, binding=m['binding']))
    for side in ('left', 'right'):
        result['wrap_' + side + 's'] = [e['atom'] for e in evidence if e['side'] == side]
    result['wrap_evidence'] = evidence
    result['printed_lines'] = printed
    result.pop('snapshot', None)
    result['snapshot'] = digest(result)
    return result
