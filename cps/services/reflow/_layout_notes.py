"""Narrow, source-derived note links; model replies choose only existing IDs."""
import re
import hashlib
import json
from xml.etree import ElementTree as ET

from .structural_ops import ContractError

EPUB_TYPE = '{http://www.idpf.org/2007/ops}type'


def need(value, reason):
    if not value:
        raise ContractError('note binding: ' + reason)


def tree(fragment):
    return ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">' + fragment + '</root>')


def candidates(source):
    references, labels = [], []
    for atom in source['atoms']:
        if source['blocks'][atom['block']]['opaque']:
            continue
        root = tree(atom['html'])
        if (len(root) == 1 and not root.text and not root[0].tail
                and root[0].tag == 'sup' and root[0].attrib == {'class': 'noteref-unresolved'}
                and not len(root[0]) and re.fullmatch(r'[0-9]+', root[0].text or '')):
            references.append(dict(id=atom['id'], label=root[0].text))
        if (not atom['protected'] and not len(root)
                and re.fullmatch(r'[0-9]+\.?', root.text or '')):
            labels.append(dict(id=atom['id'], label=root.text.rstrip('.')))
    # Plain references are punctuation-delimited ASCII digit runs. Positions
    # are Python Unicode code points in XML-decoded atom text, never byte offsets.
    for atom in source['atoms']:
        if atom['protected'] or source['blocks'][atom['block']]['opaque']:
            continue
        root = tree(atom['html'])
        if len(root):
            continue
        text = root.text or ''
        if re.fullmatch(r'[0-9]+\.', text):
            continue  # A bare printed label is not a plain reference span.
        for match in re.finditer(r'(?<![\w])[0-9]+(?![\w])', text):
            digits = match.group()
            if not any(n['label'] == digits and n['id'] != atom['id'] for n in labels):
                continue
            key = json.dumps([source['snapshot'], atom['id'], match.start(), match.end(), digits],
                             ensure_ascii=True, separators=(',', ':'))
            references.append(dict(id='s'+hashlib.sha256(key.encode()).hexdigest(),
                label=digits, atom=atom['id'], start=match.start(), end=match.end()))
    return dict(references=references, labels=labels)


def identities(source, reference, note):
    # Page + the complete factory source digest isolate different documents and
    # snapshots; source IDs are collision-checked before they can be emitted.
    prefix = 'layout-p%d-%s-' % (source['page'], source['snapshot'])
    return prefix + 'ref-' + reference, prefix + 'note-' + note


def attributes(ref_id, note_id):
    return (dict(id=ref_id, href='#'+note_id, **{'class': 'noteref', EPUB_TYPE: 'noteref'}),
            dict(id=note_id, **{'class': 'footnote', EPUB_TYPE: 'footnote'}),
            dict(href='#'+ref_id))


def checked(source, answer, groups):
    bindings = answer.get('note_bindings', [])
    need(type(bindings) is list, 'bindings must be an array')
    if not bindings:
        return []
    available = candidates(source)
    refs = {r['id']: r for r in available['references']}
    labels = {r['id']: r['label'] for r in available['labels']}
    membership = {a['id']: (i, role, group) for i, (role, group) in enumerate(groups) for a in group}
    occupied = {n.get(key) for a in source['atoms'] for n in tree(a['html']).iter()
                for key in ('id', 'name') if n.get(key) is not None}
    used_refs, used_notes, result = set(), set(), []
    used_spans = {}
    for binding in bindings:
        need(type(binding) is dict and set(binding) == {'reference', 'note'}, 'binding fields')
        ref, note = binding['reference'], binding['note']
        need(type(ref) is str and type(note) is str, 'atom IDs must be strings')
        need(ref in refs and note in labels, 'ineligible reference or note label')
        need(ref not in used_refs and note not in used_notes, 'duplicate reference or note')
        candidate = refs[ref]
        atom_id = candidate.get('atom', ref)
        ri, role, rg = membership[atom_id]
        ni, note_role, ng = membership[note]
        need(role in ('paragraph', 'quote') and note_role == 'note' and ri != ni,
             'reference must be in prose and target in a separate note')
        need(ng[0]['id'] == note and len(ng) > 1, 'target must begin a complete note group')
        need(candidate['label'] == labels[note], 'labels differ')
        if 'atom' in candidate:
            need(not any(atom_id in (j['left'], j['right']) for j in answer['joins']) and
                 atom_id != (answer.get('boundary_join') or {}).get('right'), 'joined reference atom')
            need(not any(a['protected'] for a in ng), 'protected target note content')
            lo, hi = candidate['start'], candidate['end']
            need(not any(lo < b and a < hi for a, b in used_spans.get(atom_id, [])), 'overlapping reference spans')
            used_spans.setdefault(atom_id, []).append((lo, hi))
        # No label splitting or joining: the original printed label is backlink text.
        need(not any(note in (j['left'], j['right']) for j in answer['joins']), 'joined note label')
        ref_id, note_id = identities(source, ref, note)
        need(ref_id not in occupied and note_id not in occupied, 'navigation ID collision')
        occupied.update((ref_id, note_id)); used_refs.add(ref); used_notes.add(note)
        value = dict(reference=ref, note=note, reference_group=ri, note_group=ni,
                     ref_id=ref_id, note_id=note_id)
        if 'atom' in candidate:
            value.update({k:candidate[k] for k in ('atom', 'start', 'end', 'label')})
        result.append(value)
    return result


def wrap(fragment, attrs):
    node = ET.Element('a', attrs)
    content = tree(fragment)
    node.text = content.text
    node.extend(list(content))
    return ET.tostring(node, encoding='unicode')


def verified_source_output(source, answer, groups, rendered):
    """Prove each generated wrapper against source, then recover source-only DOM.

    Renderer output never supplies expectations. Only exact checked wrappers are
    removed; existing protected trees and all unapproved markup remain for the
    independent compiler gates. Placement is tied to group and source text offset.
    """
    bindings = checked(source, answer, groups)
    if not bindings:
        return rendered
    root = tree(rendered['body'])
    need(not (root.text or '').strip(), 'unadmitted text before output groups')
    body_groups = [i for i, (role, _) in enumerate(groups)
                   if role not in ('furniture', 'source_furniture')]
    body_indices = {i: n for n, i in enumerate(body_groups)}
    owned = {a['id']: a for a in source['atoms']}
    joins = {j['left']: j for j in answer['joins']}
    normal = lambda s: re.sub(r'\s+', ' ', s).strip()

    def shape(node):
        return node.tag, tuple(sorted(node.attrib.items())), node.text or '', tuple((shape(c), c.tail or '') for c in node)

    def prefix(group, atom_id=None):
        parts = []
        for atom in group:
            if atom['id'] == atom_id:
                return normal(''.join(parts))
            value = atom['text']
            if atom['id'] in joins and joins[atom['id']]['hyphen'] == 'drop':
                value = value[:-1]
            parts.append(value)
            if atom['id'] not in joins:
                parts.append(' ')
        need(atom_id is None, 'missing source atom')
        return normal(''.join(parts))

    def exact_text(group, atom_id=None):
        parts = []
        for i, atom in enumerate(group):
            if atom['id'] == atom_id:
                return ''.join(parts)
            value = atom['text']
            if atom['id'] in joins and joins[atom['id']]['hyphen'] == 'drop':
                value = value[:-1]
            parts.append(value)
            if i+1 < len(group) and atom['id'] not in joins:
                parts.append(' ')
        need(atom_id is None, 'missing source atom')
        return ''.join(parts)

    def locate(identifier):
        found = [n for n in root.iter() if n.get('id') == identifier]
        need(len(found) == 1, 'missing or duplicate generated ID')
        return found[0]

    def prove_position(node, parent, atom_id, group):
        need(node in list(parent), 'generated reference moved or nested')
        before = parent.text or ''
        for child in parent:
            if child is node:
                break
            before += ''.join(child.itertext()) + (child.tail or '')
        need(normal(before) == prefix(group, atom_id), 'generated anchor source position changed')

    def prove_anchor(anchor, parent, attrs, atom_id, group):
        prove_position(anchor, parent, atom_id, group)
        expected = ET.Element('a', attrs)
        original = tree(owned[atom_id]['html'])
        expected.text = original.text
        expected.extend(list(original))
        need(shape(anchor) == shape(expected), 'generated anchor or source child changed')

    # Prove all wrappers before removing any; one group can own multiple refs.
    removals = []
    notes = []
    for binding in bindings:
        ri, ni = binding['reference_group'], binding['note_group']
        need(max(body_indices[ri], body_indices[ni]) < len(root), 'missing output group')
        parent, note = root[body_indices[ri]], root[body_indices[ni]]
        ref_attrs, note_attrs, back_attrs = attributes(binding['ref_id'], binding['note_id'])
        need(note is locate(binding['note_id']) and note.tag == 'div' and note.attrib == note_attrs,
             'generated note container changed')
        ref = locate(binding['ref_id'])
        if 'atom' in binding:
            need(ref in list(parent), 'span reference moved or nested')
            expected_anchor = ET.Element('a', ref_attrs)
            original_text = tree(owned[binding['atom']]['html']).text or ''
            expected_anchor.text = original_text[binding['start']:binding['end']]
            need(shape(ref) == shape(expected_anchor), 'span anchor or digits changed')
            before = parent.text or ''
            for child in parent:
                if child is ref:
                    break
                before += ''.join(child.itertext()) + (child.tail or '')
            expected_before = exact_text(groups[ri][1], binding['atom']) + original_text[:binding['start']]
            need(before == expected_before, 'span source position or prefix changed')
            carrier = parent
        else:
            # Construct the one permitted SUP(anchor(numeral)) directly from the
            # immutable atom, independently of the rendering implementation.
            original = tree(owned[binding['reference']]['html'])[0]
            expected_sup = ET.Element(original.tag, dict(original.attrib))
            expected_anchor = ET.SubElement(expected_sup, 'a', ref_attrs)
            expected_anchor.text = original.text
            carriers = [n for n in parent if ref in list(n)]
            need(len(carriers) == 1, 'reference must be directly inside its source SUP')
            sup = carriers[0]
            need(shape(sup) == shape(expected_sup), 'source SUP, inner anchor or numeral changed')
            prove_position(sup, parent, binding['reference'], groups[ri][1])
            carrier = sup
        need(len(note) > 0, 'missing backlink')
        back = note[0]
        prove_anchor(back, note, back_attrs, binding['note'], groups[ni][1])
        need(normal(''.join(note.itertext())) == prefix(groups[ni][1]),
             'complete target note text changed')
        if 'atom' in binding:
            need(''.join(note.itertext()) == exact_text(groups[ni][1]),
                 'complete target note spacing changed')
        removals.extend(((carrier, ref), (note, back))); notes.append(note)
    for parent, anchor in removals:
        index = list(parent).index(anchor)
        previous = parent[index-1] if index else None
        def append_text(value):
            if previous is None:
                parent.text = (parent.text or '') + value
            else:
                previous.tail = (previous.tail or '') + value
        append_text(anchor.text or '')
        children = list(anchor)
        parent.remove(anchor)
        for child in children:
            parent.insert(index, child); index += 1; previous = child
        append_text(anchor.tail or '')
    for index in {b['reference_group'] for b in bindings if 'atom' in b}:
        need(''.join(root[body_indices[index]].itertext()) == exact_text(groups[index][1]),
             'span source group text or spacing changed')
    for note in notes:
        note.tag = 'aside'
        note.attrib.clear(); note.set('class', 'source-note-unbound')
    return dict(rendered, body=''.join(ET.tostring(n, encoding='unicode') for n in root))
