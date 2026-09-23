"""Versioned JSON transport for the existing pure reflow value types.

No import names, constructors or executable values are selected by input. Field
names come from the explicit class registry, including additive source fields.
"""
import base64
from collections import Counter
from dataclasses import fields, is_dataclass
import json
import math
from functools import lru_cache

VERSION = 1
MAX_BYTES = 256 * 1024 * 1024
MAX_DEPTH = 80


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
           'BookStyle': {'folio_boxes'}}


def encode(value, depth=0):
    if depth > MAX_DEPTH: raise ValueError('native value nesting exceeds bound')
    enc = lambda v: encode(v, depth + 1)
    if value is None or type(value) in (str, bool, int): return value
    if type(value) is float:
        if not math.isfinite(value): raise ValueError('nonfinite native value')
        return value
    if isinstance(value, bytes): return {'type': 'bytes', 'value': base64.b64encode(value).decode('ascii')}
    if isinstance(value, (dict, Counter)):
        return {'type': 'counter' if isinstance(value, Counter) else 'dict',
                'value': [[enc(k), enc(v)] for k, v in value.items()]}
    if isinstance(value, (list, tuple, set)):
        return {'type': type(value).__name__, 'value': [enc(v) for v in value]}
    if is_dataclass(value) and registry().get(type(value).__name__) is type(value):
        name = type(value).__name__
        allowed = {f.name for f in fields(value)} | EXTRAS.get(name, set()) | FORWARD.get(name, set())
        if set(vars(value)) - allowed: raise ValueError('unregistered native fields')
        return {'type': name, 'value': {k: enc(v) for k, v in vars(value).items()}}
    raise ValueError('unregistered native value type')


def decode(value, depth=0):
    if depth > MAX_DEPTH: raise ValueError('native value nesting exceeds bound')
    dec = lambda v: decode(v, depth + 1)
    if value is None or type(value) in (str, bool, int): return value
    if type(value) is float and math.isfinite(value): return value
    if not isinstance(value, dict) or set(value) != {'type', 'value'}: raise ValueError('invalid native value')
    kind, data = value['type'], value['value']
    if type(kind) is not str: raise ValueError('invalid native type name')
    if kind == 'bytes': return base64.b64decode(data, validate=True)
    if kind in ('dict', 'counter'):
        if not isinstance(data, list): raise ValueError('invalid mapping')
        pairs = [(dec(k), dec(v)) for k, v in data]
        result = dict(pairs)
        if len(result) != len(pairs): raise ValueError('duplicate mapping key')
        return Counter(result) if kind == 'counter' else result
    if kind in ('list', 'tuple', 'set'):
        if not isinstance(data, list): raise ValueError('invalid sequence')
        return {'list': list, 'tuple': tuple, 'set': set}[kind](dec(v) for v in data)
    cls = registry().get(kind)
    if cls is None or not isinstance(data, dict): raise ValueError('unknown native type')
    declared = {f.name for f in fields(cls)}
    if set(data) - declared - EXTRAS.get(kind, set()) - FORWARD.get(kind, set()):
        raise ValueError('unknown native fields')
    data = {k: dec(v) for k, v in data.items()}
    for key in FORWARD.get(kind, ()):
        if key not in data: continue
        item = data[key]
        if key in ('transcription_uncertain', 'transcript_unverified'):
            if type(item) is not bool: raise ValueError('invalid source certainty flag')
        elif not isinstance(item, dict): raise ValueError('invalid source metadata')
        elif key == 'folio_boxes':
            if any(type(p) is not int or p < 0 or not isinstance(box, tuple)
                   or len(box) != 4 or any(type(n) not in (int, float) for n in box)
                   for p, box in item.items()):
                raise ValueError('invalid folio geometry')
    obj = cls(**{k: v for k, v in data.items() if k in declared})
    for k in set(data) - declared: object.__setattr__(obj, k, data[k])
    return obj


def dumps(value):
    raw = json.dumps({'version': VERSION, 'value': encode(value)}, ensure_ascii=True,
                     allow_nan=False, separators=(',', ':')).encode('ascii')
    if len(raw) > MAX_BYTES: raise ValueError('native payload exceeds size bound')
    return raw


def loads(raw):
    if len(raw) > MAX_BYTES: raise ValueError('native payload exceeds size bound')
    def pairs(rows):
        result = dict(rows)
        if len(result) != len(rows): raise ValueError('duplicate JSON key')
        return result
    envelope = json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    if (not isinstance(envelope, dict) or set(envelope) != {'version', 'value'}
            or type(envelope['version']) is not int or envelope['version'] != VERSION):
        raise ValueError('native protocol version mismatch')
    return decode(envelope['value'])
