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
