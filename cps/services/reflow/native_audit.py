"""Small fixed audit wire contract. No supplied crop/hash is source authority."""
import hashlib
import json
import re

MAX_BYTES = 8 * 1024 * 1024
MAX_SELECTIONS = 4096
HEX = re.compile(r'[a-f0-9]{64}\Z')


def bounded(value):
    from . import native_codec
    if len(native_codec.dumps(value)) > MAX_BYTES:
        raise ValueError('native audit exceeds bound')


def request(value, fingerprint, pages):
    bounded(value)
    if (type(value) is not dict or set(value) != {'book_id', 'fingerprint', 'operations', 'stage_records'}
            or type(value['book_id']) is not int or value['book_id'] < 1
            or value['fingerprint'] != fingerprint or type(value['operations']) is not list
            or len(value['operations']) > min(pages, MAX_SELECTIONS)):
        raise ValueError('native audit request schema')
    seen = set(); count = 0
    for row in value['operations']:
        if (type(row) is not dict or set(row) != {'page', 'snapshot_id', 'source_identity', 'selected'}
                or type(row['page']) is not int or not 0 <= row['page'] < pages or row['page'] in seen
                or type(row['snapshot_id']) is not str or not HEX.fullmatch(row['snapshot_id'])
                or (row['source_identity'] is not None and
                    (type(row['source_identity']) is not str or not HEX.fullmatch(row['source_identity'])))
                or type(row['selected']) is not list or not row['selected']
                or any(type(cid) is not str or not re.fullmatch(r'op-[a-f0-9]{24}', cid) for cid in row['selected'])
                or len(set(row['selected'])) != len(row['selected'])):
            raise ValueError('native audit selection schema')
        seen.add(row['page']); count += len(row['selected'])
    if count > MAX_SELECTIONS: raise ValueError('native audit selection bound')
    records = value['stage_records']
    if type(records) is not list or len(records) > MAX_SELECTIONS * 4:
        raise ValueError('native audit stage records')
    for row in records:
        if (type(row) is not dict or set(row) - {'page', 'stage', 'request_sha256', 'cached', 'attempt'}
                or type(row.get('page')) is not int or row['page'] not in seen
                or row.get('stage') not in ('proposer', 'verifier')
                or type(row.get('request_sha256')) is not str or not HEX.fullmatch(row['request_sha256'])
                or ('cached' in row and type(row['cached']) is not bool)
                or ('attempt' in row and (type(row['attempt']) not in (str, int) or len(str(row['attempt'])) > 128))):
            raise ValueError('native audit stage record schema')
    return value


def validate_reply(reply, expected, local_matching, plans, book):
    """Bind child evidence to parent's admitted selections, never an EPUB claim."""
    from . import native_text, structural_ops
    bounded(reply)
    if type(reply) is not tuple or len(reply) != 2:
        raise ValueError('native audit response shape')
    rows, matching = reply
    if type(rows) is not list or type(matching) is not dict or len(rows) != len(expected):
        raise ValueError('native audit response rows')
    wanted = {r['candidate_id'] for r in expected}
    if set(matching) != wanted: raise ValueError('native audit matching selections')
    resources = {}
    for plan in plans:
        candidates = {c['candidate_id']: c for c in plan.prepared.candidates()}
        for cid in plan.selected:
            c = candidates[cid];element = book.pages[plan.prepared.page][int(c['element_id'][1:])]
            runs = structural_ops._slice(element.runs, *c['source_range'])
            resources[cid] = {native_text.image_name(r[2]) for r in runs if r[0] == 'glyph'}
    for actual, prior in zip(rows, expected):
        if type(actual) is not dict or {k:v for k,v in actual.items() if k != 'source_atoms_sha256'} != prior:
            raise ValueError('native audit row binding')
        cid = prior['candidate_id']; material = matching[cid]
        if not resources[cid]:
            if actual != prior or material != local_matching[cid]:
                raise ValueError('native audit text binding')
            continue
        if material is None:
            if actual != prior: raise ValueError('unsupported native audit evidence')
            continue
        if (type(material) is not dict or set(material) != {'projection', 'resources'}
                or type(material['projection']) is not list or type(material['resources']) is not dict
                or len(material['resources']) != len(resources[cid])):
            raise ValueError('native audit pixel evidence shape')
        paths = {p[:-4] for p in resources[cid]}
        for path, digest in material['resources'].items():
            if (type(path) is not str or path[-4:] not in ('.jpg', '.png') or path[:-4] not in paths
                    or type(digest) is not str or not HEX.fullmatch(digest)):
                raise ValueError('native audit resource binding')
        if {p[:-4] for p in material['resources']} != paths:
            raise ValueError('native audit duplicate resource aliases')
        # Reconstruct the non-pixel shape from parent's current sealed source.
        # Only the child can supply crop digests, not source structure/ordering.
        from . import build_epub, enriched_source, operation_audit
        from xml.etree import ElementTree as ET
        plan = next(p for p in plans if cid in p.selected)
        source = plan.prepared.source_page
        if source is None: raise ValueError('missing native audit source authority')
        candidate = next(c for c in plan.prepared.candidates() if c['candidate_id'] == cid)
        index = int(candidate['element_id'][1:]); element = book.pages[plan.prepared.page][index]
        spec = next(s for s in plan.prepared.specs if s.element == index and
                    [s.start, s.end] == candidate['source_range'] and s.kind == candidate['kind'])
        block = build_epub.split_blocks(source.html)[json.loads(source.blocks_json)[str(index)]]
        fragment = enriched_source.wrap(block, element, [spec])
        tree = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+fragment+'</root>')
        wrappers = list(tree.iter(prior['proposed_state']))
        if len(wrappers) != 1: raise ValueError('native audit wrapper shape')
        aliases = {path[:-4]:path for path in material['resources']}
        for image in wrappers[0].iter('img'):
            image.set('src', aliases[image.get('src')[:-4]])
        if material['projection'] != operation_audit._projection(wrappers[0]):
            raise ValueError('native audit source projection')
        # Codec bounds depth; projection is pure JSON, not bytes/objects/sets.
        encoded = json.dumps(material, sort_keys=True, allow_nan=False)
        if actual.get('source_atoms_sha256') != hashlib.sha256(encoded.encode()).hexdigest():
            raise ValueError('native audit atom digest')
    return rows, matching
