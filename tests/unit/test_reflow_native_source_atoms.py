"""Source10.1 factory authority and indivisible word images across real exec."""
import copy
import json
import zipfile
from dataclasses import replace
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import native_codec as codec, structural_ops as ops, enriched_source, build_epub
from cps.services.reflow.native_ipc import NativeDocument
from cps.services.reflow.native_text import descriptor
from tests.unit.test_reflow_quote_units import fixture

pytestmark = pytest.mark.unit


def test_native_build_retains_distinct_atom_and_legacy_conservation(tmp_path):
    """The child must not collapse an explicit old lexical failure into the
    current atom-aware success when transporting Book into publication metadata.
    This is a transport fixture, not permission to waive source discrepancies.
    """
    from cps.services.reflow.assemble import ConservationReport
    book, original, raw, start, end = fixture(tmp_path)
    path = original.name
    original.close()
    legacy = dict(ok=False, source_total=4, output_total=5,
                  missing=['physicalwrap'], added=['physical', 'wrap'])
    book.conservation = ConservationReport(True, 5, 5, legacy_lexical=legacy)
    with NativeDocument(path, scratch_root=tmp_path / 'scratch') as doc:
        built = build_epub.build(book, str(tmp_path / 'legacy.epub'), doc=doc)
        assert build_epub.validate(built.path) == []
    with zipfile.ZipFile(built.path) as archive:
        report = json.loads(archive.read('META-INF/reflow.json'))['conservation']
    assert report == book.conservation.to_dict()
    assert report['ok'] is True and report['legacy_lexical']['ok'] is False


def test_native_quote_atom_roundtrip_reissues_source_authority_and_preserves_pixels(tmp_path, monkeypatch):
    book, original_doc, raw, start, end = fixture(tmp_path)
    element = book.pages[0][1]; text = element.runs[0][1]
    a = text.index('complete'); b = a + len('complete')
    box = next(w[:4] for w in original_doc[0].get_text('words') if w[4] == 'complete' and w[1] > 140)
    element.runs = [['t', text[:a]], ['glyph', text[a:b],
        descriptor(0, box, 11, 'Times-Roman', reason='transcript')], ['t', text[b:]]]
    original_runs = copy.deepcopy(element.runs)
    source = enriched_source.prepare_source_page(book, 0, {'layer': 'native'}, [])
    direct = ops.prepare(book, original_doc, 0, 'source10.1-atoms', {'layer': 'native'},
                         source_page=source, raw_page=raw)
    path = original_doc.name; original_doc.close()
    with NativeDocument(path, scratch_root=tmp_path / 'scratch') as doc:
        remote = ops.prepare(book, doc, 0, 'source10.1-atoms', {'layer': 'native'},
                             source_page=source, raw_page=raw)
        assert remote.model_view() == direct.model_view()
        # An empty receiving factory has no authority from wire values alone.
        # Equal objects in the already-issued parent domain intentionally share
        # its identity; that is not evidence of a seal surviving process exec.
        unsealed = codec.loads(codec.dumps(source))
        choice = next(c for c in remote.candidates() if c['kind'] == 'quote' and c['element_id'] == 'e1')
        response = dict(protocol=ops.PROTOCOL, snapshot_id=remote.snapshot_id, select=[choice['candidate_id']])
        import weakref
        with monkeypatch.context() as context:
            context.setattr(enriched_source, '_issued', weakref.WeakKeyDictionary())
            with pytest.raises(ops.ContractError): unsealed.validate(book)
            with pytest.raises(ops.ContractError): remote.accept(book, doc, response, source_page=unsealed)
        plan = remote.accept(book, doc, response, source_page=source)
        built = build_epub.build(book, str(tmp_path / 'atom.epub'), doc=doc,
            source_pages={0: source}, operation_plans=[plan])
        assert build_epub.validate(built.path) == []
        forged = replace(remote, specs=(ops._Spec(1, 'quote', a + 1, end),))
        bad = forged.candidates()[0]['candidate_id']
        with pytest.raises(ops.ContractError):
            build_epub.build(book, str(tmp_path / 'cut.epub'), doc=doc,
                source_pages={0: source}, operation_plans=[ops.OperationPlan(forged, (bad,))])
    assert element.runs == original_runs
    with zipfile.ZipFile(built.path) as z:
        blocks = [b for n in z.namelist() if n.endswith('.xhtml')
                  for b in ET.fromstring(z.read(n)).iter('{http://www.w3.org/1999/xhtml}blockquote')]
        assert len(blocks) == 1
        images = list(blocks[0].iter('{http://www.w3.org/1999/xhtml}img'))
        assert len(images) == 1
        assert z.getinfo('OEBPS/' + images[0].attrib['src']).file_size > 0
