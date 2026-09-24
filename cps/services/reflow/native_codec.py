"""Versioned JSON transport for the existing pure reflow value types.

No import names, constructors or executable values are selected by input. Field
names come from the explicit class registry, including additive source fields.
Keys and set members are scalars or bounded flat scalar tuples, not arbitrary
hashable graphs. Shared graphs remain supported at ordinary value positions.
"""
import base64
from collections import Counter
from dataclasses import fields, is_dataclass
import json
import math
import io
from functools import lru_cache

VERSION = 2
MAX_BYTES = 256 * 1024 * 1024
MAX_DEPTH = 80
MAX_KEY_ITEMS = 32


@lru_cache(maxsize=1)
def registry():
    from . import assemble, assess, extract, pipeline, skeleton, source, structural_ops, enriched_source, build_epub
    modules = {
        assemble: ('Element', 'Note', 'Repair', 'ConservationReport', 'Book'),
        assess: ('Assessment', 'PageCensus'),
        extract: ('Span', 'Line', 'Block', 'Image', 'RawPage'),
        pipeline: ('ReflowResult', 'PageOutcome', 'Progress'),
        skeleton: ('BookStyle',), source: ('Recovery', 'PageRecovery'),
        structural_ops: ('Prepared', '_Spec', 'OperationPlan'),
        enriched_source: ('SourcePage',), build_epub: ('BuildResult',),
    }
    return {name: getattr(module, name) for module, names in modules.items() for name in names}


# These are the only non-dataclass fields the source pipeline adds to its result.
EXTRAS = {'ReflowResult': {'source_pages', 'operation_plans', 'stage_records',
                          'structural', 'preview_html'}}
# Keep declared source10 additions when running a pre-source10 composition.
FORWARD = {'Span': {'transcription_uncertain'}, 'Line': {'transcription_uncertain'},
           'RawPage': {'transcript_unverified'}, 'PageRecovery': {'verification'},
           'BookStyle': {'folio_boxes', 'local_running_boxes'}}


def _certainty_fields(kind, data):
    for key in FORWARD.get(kind, ()):
        if key not in data: continue
        item = data[key]
        if key in ('transcription_uncertain', 'transcript_unverified'):
            if type(item) is not bool: raise ValueError('invalid source certainty flag')
        elif not isinstance(item, dict): raise ValueError('invalid source metadata')
        elif key in ('folio_boxes', 'local_running_boxes'):
            if any(type(p) is not int or p < 0 or not isinstance(box, tuple)
                   or len(box) != 4 or any(type(n) not in (int, float) for n in box)
                   for p, box in item.items()):
                raise ValueError('invalid folio geometry')

def _primitive(value):
    if value is None or type(value) in (str, bool, int): return True
    if type(value) is float:
        if not math.isfinite(value): raise ValueError('nonfinite native value')
        return True
    return False


def _key(value):
    """Bound hashing before inserting decoded mapping keys or set members.

    Source identifiers are scalars or short flat tuples. Nested/shared tuples
    remain valid as values, but must never reach Python's recursive tuple hash.
    Exact built-in types also exclude user-defined hash/equality behavior.
    """
    def scalar(item):
        return _primitive(item) or type(item) is bytes
    if scalar(value):
        return value
    if (type(value) is tuple and len(value) <= MAX_KEY_ITEMS
            and all(scalar(item) for item in value)):
        return value
    raise ValueError('unsupported native key/member: expected scalar or bounded flat tuple')


def dumps(value):
    """Stream a postorder value DAG into one valid JSON document.

    Full source results alias raw_pages and Recovery.pages, plus millions of
    geometry tuples. Do not first build a second tagged object graph or duplicate
    shared values. References select only prior values, never code or authority.
    Each node is a single bounded line, so decoding need not parse the whole graph
    into temporary JSON containers before constructing its final value objects.
    """
    output = io.BytesIO(); memo = {}; active = set()
    encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    def write(data):
        if output.tell() + len(data) > MAX_BYTES: raise ValueError('native payload exceeds size bound')
        output.write(data)
    def dump(item):
        for text in encoder.iterencode(item): write(text.encode('ascii'))
    write(b'{"version":2,"nodes":[\n')
    def visit(item, depth=0):
        if depth > MAX_DEPTH: raise ValueError('native value nesting exceeds bound')
        if _primitive(item): return item
        identity = id(item)
        if identity in active: raise ValueError('cyclic native value')
        if identity in memo: return {'ref': memo[identity]}
        active.add(identity)
        child = lambda v: visit(v, depth + 1)
        if isinstance(item, bytes):
            kind, data = 'bytes', base64.b64encode(item).decode('ascii')
        elif isinstance(item, (dict, Counter)):
            kind = 'counter' if isinstance(item, Counter) else 'dict'
            data = [[child(_key(k)), child(v)] for k, v in item.items()]
        elif isinstance(item, (list, tuple, set)):
            kind = type(item).__name__
            data = [child(_key(v) if isinstance(item, set) else v) for v in item]
        elif is_dataclass(item) and registry().get(type(item).__name__) is type(item):
            kind = type(item).__name__
            allowed = {f.name for f in fields(item)} | EXTRAS.get(kind, set()) | FORWARD.get(kind, set())
            if set(vars(item)) - allowed: raise ValueError('unregistered native fields')
            data = {k: child(v) for k, v in vars(item).items()}
        else: raise ValueError('unregistered native value type')
        active.remove(identity)
        index = len(memo)
        if index: write(b',')
        dump([index, kind, data]); write(b'\n')
        memo[identity] = index
        return {'ref': index}
    root = visit(value)
    write(b'],"root":'); dump(root); write(b'}\n')
    return output.getvalue()


def _json(raw):
    def pairs(rows):
        result = dict(rows)
        if len(result) != len(rows): raise ValueError('duplicate JSON key')
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def loads(raw):
    if len(raw) > MAX_BYTES: raise ValueError('native payload exceeds size bound')
    stream = io.BytesIO(raw)
    if stream.readline() != b'{"version":2,"nodes":[\n':
        raise ValueError('native protocol version mismatch')
    values = []; depths = []
    def resolve(value):
        if _primitive(value): return value, 0
        if (type(value) is not dict or set(value) != {'ref'} or type(value['ref']) is not int
                or not 0 <= value['ref'] < len(values)):
            raise ValueError('invalid native reference')
        return values[value['ref']], depths[value['ref']]
    for line in stream:
        if line.startswith(b'],"root":'):
            trailer = _json(b'{' + line[2:])
            if set(trailer) != {'root'} or stream.read(1): raise ValueError('native trailer schema')
            return resolve(trailer['root'])[0]
        if values:
            if not line.startswith(b','): raise ValueError('native node separator')
            line = line[1:]
        row = _json(line)
        if (type(row) is not list or len(row) != 3 or type(row[0]) is not int
                or row[0] != len(values) or type(row[1]) is not str):
            raise ValueError('native node schema')
        _, kind, data = row; depth = 1
        def child(value):
            nonlocal depth
            result, nested = resolve(value); depth = max(depth, nested + 1)
            if depth > MAX_DEPTH: raise ValueError('native value nesting exceeds bound')
            return result
        if kind == 'bytes':
            if type(data) is not str: raise ValueError('invalid native bytes')
            item = base64.b64decode(data, validate=True)
        elif kind in ('dict', 'counter'):
            if type(data) is not list: raise ValueError('invalid native mapping')
            pairs = []
            for pair in data:
                if type(pair) is not list or len(pair) != 2: raise ValueError('invalid native mapping pair')
                pairs.append((_key(child(pair[0])), child(pair[1])))
            item = dict(pairs)
            if len(item) != len(pairs): raise ValueError('duplicate mapping key')
            if kind == 'counter': item = Counter(item)
        elif kind in ('list', 'tuple', 'set'):
            if type(data) is not list: raise ValueError('invalid native sequence')
            members = (child(v) for v in data)
            if kind == 'set':
                item = set(_key(v) for v in members)
            else:
                item = {'list': list, 'tuple': tuple}[kind](members)
        else:
            cls = registry().get(kind)
            if cls is None or type(data) is not dict: raise ValueError('unknown native type')
            declared = {f.name for f in fields(cls)}
            if set(data) - declared - EXTRAS.get(kind, set()) - FORWARD.get(kind, set()):
                raise ValueError('unknown native fields')
            data = {k: child(v) for k, v in data.items()}
            _certainty_fields(kind, data)
            item = cls(**{k: v for k, v in data.items() if k in declared})
            for k in set(data) - declared: object.__setattr__(item, k, data[k])
        values.append(item); depths.append(depth)
    raise ValueError('missing native result')
