"""The full preparation reply must not multiply the shared source graph."""
import copy
import pytest
from cps.services.reflow import native_codec as codec, pipeline, source, extract

pytestmark = pytest.mark.unit


def test_shared_recovery_pages_roundtrip_once_instead_of_multiplying_source_graph():
    pages = [extract.RawPage(p, 500., 700., source_geometry={'evidence': 'source' * 1000}) for p in range(12)]
    shared = pipeline.ReflowResult(raw_pages=pages, recovery=source.Recovery(pages=pages))
    separate = pipeline.ReflowResult(raw_pages=pages, recovery=source.Recovery(pages=copy.deepcopy(pages)))
    wire = codec.dumps(shared)
    assert len(wire) < len(codec.dumps(separate)) * .65
    restored = codec.loads(wire)
    assert restored.raw_pages is restored.recovery.pages
    assert restored.raw_pages[5].source_geometry == pages[5].source_geometry


def test_graph_cycle_and_nonexistent_reference_are_not_wire_capabilities():
    cyclic = []; cyclic.append(cyclic)
    with pytest.raises(ValueError): codec.dumps(cyclic)
    with pytest.raises(ValueError):
        codec.loads(b'{"version":2,"nodes":[\n[0,"list",[{"ref":100}]]\n],"root":{"ref":0}}\n')


@pytest.mark.parametrize('record', [
    '[0,"os.system",{}]', '[0,"Span",{"unknown":"value"}]',
    '[0,"list",[{"ref":true}]]', '[0,"dict",[["x",1],["x",2]]]',
    '[0,"tuple",[NaN]]', '[0.0,"list",[]]', '[1,"list",[]]',
    '[0,"list",[{"ref":0}]]', '[0,"list",[{"ref":0,"ref":1}]]',
])
def test_streamed_graph_rejects_unknown_ambiguous_and_executable_shapes(record):
    raw = ('{"version":2,"nodes":[\n' + record + '\n],"root":{"ref":0}}\n').encode()
    with pytest.raises((ValueError, TypeError)): codec.loads(raw)


def test_streamed_graph_decoding_enforces_depth_not_only_encoding(monkeypatch):
    raw = codec.dumps([[[[[[]]]]]])
    monkeypatch.setattr(codec, 'MAX_DEPTH', 3)
    with pytest.raises(ValueError, match='nesting'): codec.loads(raw)


def test_build_needs_recovery_geometry_not_raw_analysis_pages(tmp_path, monkeypatch):
    from tests.unit.test_reflow_build_epub import _scanned_book
    from cps.services.reflow.native_ipc import NativeDocument
    from cps.services.reflow import build_epub
    scan, result = _scanned_book(monkeypatch, 2)
    path = tmp_path / 'scan.pdf'
    try: scan.save(path)
    finally: scan.close()
    # Analysis pages are not a builder input. An unencodable sentinel detects
    # accidental re-transfer, without creating a giant object in a routine test.
    result.recovery.pages = [object()]
    with NativeDocument(path, scratch_root=tmp_path / 'scratch') as doc:
        built = build_epub.build(result.book, str(tmp_path / 'scan.epub'), doc=doc,
            page_html=result.page_html, source_pages=result.source_pages,
            figure_transform=result.recovery.figure_rect)
    assert len(built.sidecar['source_evidence']) == 2
    assert build_epub.validate(built.path) == []


@pytest.mark.parametrize('kind', ['dict', 'counter', 'set'])
@pytest.mark.parametrize('direction', ['encode', 'decode'])
def test_nested_shared_tuple_cannot_be_a_native_key_or_member(kind, direction):
    """Two tiny tuple nodes must be refused before dict/set hashes the graph."""
    from collections import Counter
    inner = ('page', 1)
    nested = (inner, inner)
    value = {nested} if kind == 'set' else Counter({nested: 1}) if kind == 'counter' else {nested: 1}
    if direction == 'encode':
        with pytest.raises(ValueError, match='native key/member'):
            codec.dumps(value)
        return
    # Handwritten ordinary three-node input also checks the decoder boundary.
    data = '[{"ref":1}]' if kind == 'set' else '[[{"ref":1},1]]'
    raw = ('{"version":2,"nodes":[\n[0,"tuple",["page",1]]\n'
           ',[1,"tuple",[{"ref":0},{"ref":0}]]\n'
           ',[2,"' + kind + '",' + data + ']\n],"root":{"ref":2}}\n').encode()
    with pytest.raises(ValueError, match='native key/member'):
        codec.loads(raw)


def test_native_hash_keys_preserve_scalars_flat_identifiers_and_value_aliases():
    from collections import Counter
    identifier = ('Times', 12.0, True, None, b'font')
    shared = ((1, 2), (3, 4))
    value = [{identifier: shared, 5: shared, b'page': 'source'}, {identifier, 7}, Counter({identifier: 2})]
    restored = codec.loads(codec.dumps(value))
    assert restored == value
    assert restored[0][identifier] is restored[0][5]


def test_native_flat_key_width_is_bounded_on_both_sides():
    key = tuple(range(33))
    with pytest.raises(ValueError, match='native key/member'):
        codec.dumps({key: 1})
    # Encoding a wide tuple as a value remains valid; only using it as a key
    # requires the bounded contract. There is no deep or large graph here.
    raw = codec.dumps(key)
    mapping = raw.replace(b'],"root":{"ref":0}}',
                          b',[1,"dict",[[{"ref":0},1]]]\n],"root":{"ref":1}}')
    with pytest.raises(ValueError, match='native key/member'):
        codec.loads(mapping)
