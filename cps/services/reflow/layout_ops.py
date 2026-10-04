"""Source-bound canonical layout with checked retained-furniture additions.

Readiness describes this source compilation contract, not activated publication.
No model response, stored fragment, or public hash issues source authority.
"""
import json
import re
from collections import Counter
from dataclasses import dataclass, asdict
from xml.etree import ElementTree as ET

from . import _layout_atoms as atoms, _layout_furniture as furniture_source, gate, source_inventory
from .enriched_source import SourcePage
from .structural_ops import ContractError, _pdf_digest
from .source_display import SourceDisplay

VERSION = 'source-bound-layout-preparation-2'


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False)


def _need(condition, reason):
    if not condition:
        raise ContractError(reason)


@dataclass(frozen=True)
class CompiledLayout:
    page: int
    snapshot_id: str
    body: str
    furniture: str
    expected_body: str
    expected_furniture: str
    groups_json: str
    answer_json: str
    word_gates_json: str
    production_ready: bool = False

    @property
    def page_html(self):
        if not self.furniture:
            return self.body
        return self.body+'\n<section class="reflow-retained-furniture" aria-label="Retained source furniture">'+self.furniture+'</section>'


@dataclass(frozen=True)
class PreparedLayout:
    page: int
    pdf_digest: str
    snapshot_id: str
    source_identity: str
    contract_json: str
    coverage_json: str
    raster: bytes

    @property
    def coverage(self):
        return json.loads(self.coverage_json)

    def model_view(self):
        view = atoms.model_view(json.loads(self.contract_json))
        view['coverage'] = self.coverage
        if view.get('previous'):
            previous=dict(view['previous'])
            previous['atoms']=previous['atoms'][-120:]
            ids={a['id'] for a in previous['atoms']}
            previous['wrap_lefts']=[a for a in previous['wrap_lefts'] if a in ids]
            previous['context_tail_atoms']=120
            view['previous']=previous
        return view

    def _current(self, book, doc, source_page, raw_page, previous_source, previous_raw):
        current = prepare(book, doc, self.page, source_page, raw_page,
                          previous_source=previous_source, previous_raw=previous_raw)
        _need(current == self, 'stale layout source, inventory, raster or contract')
        return current

    def accept(self, book, doc, response, *, source_page, raw_page,
               prototype=False, previous_source=None, previous_raw=None):
        current = self._current(book, doc, source_page, raw_page, previous_source, previous_raw)
        _need(prototype is True or current.coverage['production_ready'],
              'production layout coverage is unsupported')
        source = json.loads(current.contract_json)
        construction = ''
        if type(response) is dict and 'contract' in response:
            if response.get('contract') == 'layout-domain-2':
                from . import layout_domain
                layout_domain.validate_response_fields(current,response)
            if response.get('contract') == 'layout-range-choices-1':
                from . import layout_ranges
                layout_ranges.validate_response_fields(current,response)
            if response.get('contract') == 'layout-semantic-choices-1':
                from . import layout_choices
                layout_choices.validate_response_fields(current,response)
            from . import layout_construction
            construction = _json(response)
            response = layout_construction.compile(source, response)
        answer = _answer(source, response)
        return LayoutPlan(current, _json(answer), construction)


@dataclass(frozen=True)
class LayoutPlan:
    prepared: PreparedLayout
    answer_json: str
    construction_json: str = ''

    def compile(self, book, doc, *, source_page, raw_page, prototype=False,
                previous_source=None, previous_raw=None):
        current = self.prepared._current(book, doc, source_page, raw_page,
                                         previous_source, previous_raw)
        _need(prototype is True or current.coverage['production_ready'],
              'production layout coverage is unsupported')
        source = json.loads(current.contract_json)
        answer = _answer(source, json.loads(self.answer_json))
        if getattr(self, 'construction_json', ''):
            from . import layout_construction
            layout_construction.validate_plan(source, answer, self.construction_json)
        groups = atoms.validate(source, answer)
        rendered = _render(source, answer)
        expected, verdicts = _check_output(source, answer, groups, rendered)
        return CompiledLayout(current.page, current.snapshot_id,
            rendered['body'], rendered['furniture'], expected['body'], expected['furniture'],
            _json([dict(role=role, ids=[a['id'] for a in group]) for role,group in groups]),
            _json(answer), _json(verdicts), current.coverage['production_ready'])


def _page(book, doc, pno, source_page, raw_page):
    _need(type(pno) is int and pno in book.pages and 0 <= pno < len(doc), 'unknown source page')
    _need(type(source_page) is SourcePage and source_page.page == pno, 'factory SourcePage required')
    source_page.validate(book)
    _need(raw_page is not None and raw_page.pno == pno, 'current chosen raw page required')
    pdf = _pdf_digest(doc)
    _need(not book.source_fingerprint or book.source_fingerprint == pdf, 'source PDF changed')
    inventory = getattr(book, 'source_inventory', {}).get(pno)
    limitations = ['baseline_raw_to_canonical_mapping_not_proved']
    if inventory is None:
        limitations.append('source_inventory_missing')
    else:
        try:
            source_inventory.validate(inventory, raw_page)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ContractError('current raw inventory differs') from exc
        if inventory['unresolved']:
            limitations.append('source_inventory_unresolved')
    covered = []
    additions, reasons = furniture_source.project(book,pno,source_page,raw_page,inventory,doc=doc,covered=covered)
    mapping = {int(v): book.pages[pno][int(k)].bbox for k,v in json.loads(source_page.blocks_json).items()}
    try:
        canonical_blocks=len(_tree(source_page.html))
        mapping.update({canonical_blocks+i:row['bbox'] for i,row in enumerate(additions)})
        fragment='\n'.join([source_page.html]+[row['html'] for row in additions])
        contract = atoms.prepare(fragment, source_page.identity, pno, mapping)
        for atom in contract['atoms']:
            if atom['block']>=canonical_blocks:
                row=additions[atom['block']-canonical_blocks]
                atom['origin']=dict(kind='retained_furniture',page=pno,line_id=row['line_id'],
                    region_id=row['region_id'],source_sha256=row['source_sha256'])
        contract = atoms.bind(contract, asdict(raw_page))
    except (ValueError, TypeError, KeyError, ET.ParseError) as exc:
        raise ContractError('canonical page cannot be atomized') from exc
    contract.pop('snapshot', None)
    contract['binding'] = dict(version=VERSION, furniture_version=furniture_source.VERSION, pdf_sha256=pdf,
        raw_sha256=source_inventory.digest(raw_page),
        inventory_sha256=source_inventory.digest(inventory) if inventory is not None else None)
    if covered:
        contract['binding']['opaque_furniture_coverage'] = dict(
            version=furniture_source.OPAQUE_COVERAGE_VERSION, lines=covered)
    coverage = dict(prototype_supported=True, production_ready=not reasons,
        authority='factory_canonical_plus_checked_retained_furniture',
        coverage_limitations=limitations, retained_furniture_lines=len(additions),
        canonical_atoms=len(contract['atoms']), inventory_present=inventory is not None,
        unsupported_reasons=reasons)
    return contract, coverage


def prepare(book, doc, pno, source_page, raw_page, *, previous_source=None, previous_raw=None):
    """Bind factory source and full current evidence, with explicit incomplete coverage."""
    from .native_ipc import NativeDocument
    if isinstance(doc, NativeDocument):
        source_page.validate(book)
        if previous_source is not None: previous_source.validate(book)
        return doc.prepare_layout(book,pno,source_page,raw_page,previous_source,previous_raw)
    contract, coverage = _page(book, doc, pno, source_page, raw_page)
    _need((previous_source is None) == (previous_raw is None), 'complete previous source context required')
    if previous_source is not None:
        _need(pno > 0 and previous_source.page == pno-1, 'previous source must be adjacent')
        previous, _ = _page(book, doc, pno-1, previous_source, previous_raw)
        contract['previous'] = dict(page=pno-1, identity=previous_source.identity,
            binding=previous['binding'],
            atoms=[{k:v for k,v in a.items() if k in ('id','text','protected','raster_region')} for a in previous['atoms']],
            wrap_lefts=previous['wrap_lefts'])
    layer = json.loads(source_page.provenance_json)
    raster = SourceDisplay(doc, pno, layer).jpeg(scale=1.5, quality=85)
    if previous_source is not None and any(a.get('raster_region') for s in (contract, previous) for a in s['atoms']):
        # A neighbor's empty-text pixel atom cannot be judged from text context.
        # Send both complete source pages through the existing bounded JPEG
        # transport; this contact sheet is evidence only, never a published crop.
        import io, math
        from PIL import Image
        displays = [SourceDisplay(doc, pno-1, json.loads(previous_source.provenance_json)),
                    SourceDisplay(doc, pno, layer)]
        width = sum(d.rect.width for d in displays)
        height = max(d.rect.height for d in displays)
        scale = min(1.5, math.sqrt(3900000 / (width * height)))
        images = [Image.open(io.BytesIO(d.jpeg(scale=scale, quality=85))).convert('RGB') for d in displays]
        sheet = Image.new('RGB', (sum(i.width for i in images), max(i.height for i in images)), 'white')
        offset = 0
        panels = []
        for page, img in zip((pno-1, pno), images):
            sheet.paste(img, (offset, 0))
            panels.append(dict(page=page, panel=[offset, 0, offset+img.width, img.height]))
            offset += img.width
        out = io.BytesIO(); sheet.save(out, format='JPEG', quality=85)
        raster = out.getvalue()
        contract['binding']['raster_panels'] = panels
    import hashlib
    contract['binding']['raster_sha256'] = hashlib.sha256(raster).hexdigest()
    contract['snapshot'] = atoms.digest(contract)
    return PreparedLayout(pno, contract['binding']['pdf_sha256'], contract['snapshot'],
        source_page.identity, _json(contract), _json(coverage), raster)


def _answer(source, response):
    try:
        # JSON value copy prevents an accepted caller-owned mutable plan graph.
        answer = json.loads(_json(response))
        atoms.validate(source, answer)
        return answer
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise ContractError('invalid canonical layout response: ' + str(exc)) from exc


def _render(source, answer):
    return atoms.render(source, answer)


def _tree(fragment):
    return ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+fragment+'</root>')


def _shape(node):
    return (node.tag, tuple(sorted(node.attrib.items())), node.text or '',
            tuple((_shape(child), child.tail or '') for child in node))


def _protected(fragment):
    def protected(node):
        return atoms.tag(node) in ('a','sup','img') or 'id' in node.attrib or 'name' in node.attrib or bool(
            set(node.get('class','').split()) & {'reflow-uncertain','source-glyph','source-raster','noteref','noteref-unresolved'})
    return [_shape(n) for n in _tree(fragment).iter() if protected(n)]


def _visible(node):
    value = node.text or ''
    for child in node:
        value += _visible(child)
        if atoms.tag(child) in ('p','aside','blockquote','h1','h2','h3','figure','figcaption','br'):
            value += ' '
        value += child.tail or ''
    return value


def _check_output(source, answer, groups, rendered):
    """Expected words come from immutable IDs, never renderer-returned HTML."""
    from . import _layout_notes
    rendered = _layout_notes.verified_source_output(source, answer, groups, rendered)
    from . import _layout_emphasis
    rendered = _layout_emphasis.verified_source_output(source, answer, groups, rendered)
    joins = {j['left']:j for j in answer['joins']}
    words = {'body':[], 'furniture':[]}
    protected = {k:[] for k in words}
    block_shapes = {k:[] for k in words}
    allowed_nodes = {k:Counter() for k in words}
    role_tags = dict(paragraph='p', heading1='h1', heading2='h2', heading3='h3',
                     quote='blockquote', lineblock='p', furniture='p', note='aside')
    for role, group in groups:
        channel = 'furniture' if role in ('furniture', 'source_furniture') else 'body'
        opaque_note = role == 'note' and atoms.can_be_source_note(source['blocks'][group[0]['block']])
        if opaque_note:
            wrapper = ET.Element('aside', {'class': 'source-note-unbound'})
            wrapper.append(list(_tree(group[0]['html']))[0])
        block_shapes[channel].append((_shape(wrapper) if opaque_note else _shape(list(_tree(group[0]['html']))[0])
            if opaque_note or role in ('source', 'source_furniture') else (role_tags[role],
                (('class','source-note-unbound'),) if role=='note' else ())))
        parts=[]
        for i,atom in enumerate(group):
            value = atom['text']
            if atom['id'] in joins and joins[atom['id']]['hyphen'] == 'drop':
                value = value[:-1]
            parts.append(value)
            if i+1 < len(group) and atom['id'] not in joins:
                parts.append(' ')
            protected[channel].extend(_protected(atom['html']))
            for n in _tree(atom['html']).iter():
                if n.tag != 'root': allowed_nodes[channel][(n.tag,tuple(sorted(n.attrib.items())))]+=1
        if role == 'lineblock':
            line_ends={pair[1] for pair in next(g for g in answer['groups']
                if g['role']==role and g['ranges'][0][0]==group[0]['id'])['ranges']}
            allowed_nodes[channel][('br',())]+=sum(a['id'] in line_ends and a['id'] not in joins for a in group[:-1])
        if role not in ('source', 'source_furniture'):
            attrs=(('class','source-note-unbound'),) if role=='note' else ()
            allowed_nodes[channel][(role_tags[role],attrs)]+=1
        words[channel].append(''.join(parts))
    expected = {k:'\n'.join(v) for k,v in words.items()}
    verdicts={}
    for channel,value in expected.items():
        html = rendered[channel]
        _need(_protected(html)==protected[channel], 'protected source tree or asset changed')
        root=_tree(html)
        _need(len(root)==len(block_shapes[channel]), 'output group count changed')
        for node,shape in zip(root,block_shapes[channel]):
            actual_shape=_shape(node) if len(shape)==4 else (node.tag,tuple(sorted(node.attrib.items())))
            _need(actual_shape==shape, 'opaque source or admitted wrapper changed')
        actual=Counter((n.tag,tuple(sorted(n.attrib.items()))) for n in root.iter() if n.tag!='root')
        _need(actual==allowed_nodes[channel], 'unadmitted output markup')
        normal=lambda s: re.sub(r'\s+',' ',s).strip()
        _need(normal(value)==normal(_visible(root)), 'immutable source words changed')
        verdict=gate.check_word_preservation(value,html)
        _need(verdict.ok or verdict.verdict=='NOT_APPLICABLE', 'word gate rejected compiled source')
        verdicts[channel]=verdict.verdict
    return expected, verdicts


def compile_boundary(left_plan, right_plan, book, doc, *, left_source, right_source,
                     left_raw, right_raw, prototype=False,
                     left_previous_source=None, left_previous_raw=None):
    """Return a source-bound decision only; no page join or publication is performed.

    Both pages are recompiled; missing/fallback pages cannot supply either plan.
    An admitted right-page proposal must name the actual emitted body endpoints.
    """
    _need(type(left_plan) is LayoutPlan and type(right_plan) is LayoutPlan, 'two admitted page plans required')
    _need(right_plan.prepared.page == left_plan.prepared.page+1, 'nonadjacent boundary')
    left=left_plan.compile(book,doc,source_page=left_source,raw_page=left_raw,prototype=prototype,
        previous_source=left_previous_source,previous_raw=left_previous_raw)
    right=right_plan.compile(book,doc,source_page=right_source,raw_page=right_raw,
        previous_source=left_source,previous_raw=left_raw,prototype=prototype)
    proposal=json.loads(right.answer_json)
    if not proposal.get('continuation',False):
        return None
    from . import _layout_flow
    def edge(compiled, prepared, last):
        owned={a['id']:a for a in json.loads(prepared.contract_json)['atoms']}
        groups=[(g['role'], [owned[i] for i in g['ids']]) for g in json.loads(compiled.groups_json)]
        value=_layout_flow.edge(groups,last)
        _need(value is not None, 'heading or protected block is a continuation barrier')
        role,group=value
        return role,group[-1 if last else 0]['id']
    left_role,left_id=edge(left,left_plan.prepared,True)
    right_role,right_id=edge(right,right_plan.prepared,False)
    _need(left_role == right_role, 'continuation roles differ')
    boundary=proposal.get('boundary_join')
    if boundary:
        _need(boundary['left']==left_id and boundary['right']==right_id, 'boundary endpoints differ from admitted body')
        from . import _layout_notes
        selected={b['reference'] for b in json.loads(left.answer_json).get('note_bindings', [])}
        spans=_layout_notes.candidates(json.loads(left_plan.prepared.contract_json))['references']
        _need(not any(r.get('atom')==left_id and r['id'] in selected for r in spans),
              'boundary joins a note reference span')
    left_atom=next(a for a in json.loads(left_plan.prepared.contract_json)['atoms'] if a['id']==left_id)
    right_atom=next(a for a in json.loads(right_plan.prepared.contract_json)['atoms'] if a['id']==right_id)
    pixel_boundary=bool(left_atom.get('raster_region') or right_atom.get('raster_region'))
    _need(not pixel_boundary or boundary is None, 'protected raster cannot receive a lexical join')
    _need(pixel_boundary or not left_atom['text'].endswith('-') or boundary is not None, 'boundary hyphen requires explicit choice')
    return (dict(pixel_boundary=True) if pixel_boundary else {}) | dict(left_snapshot=left.snapshot_id,right_snapshot=right.snapshot_id,
                left=left_id,right=right_id,role=left_role,hyphen=boundary['hyphen'] if boundary else None,
                production_ready=left.production_ready and right.production_ready)
