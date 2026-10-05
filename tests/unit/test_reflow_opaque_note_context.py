"""An already measured note retains its role when OCR words become pixels."""
from dataclasses import asdict
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import assemble,build_epub,extract,skeleton
pytestmark=pytest.mark.unit


def prepared(mixed=False,carrier=False,unproved=False,neighbor=False):
    box=(40,350,220,360)
    note=extract.Line([extract.Span('9 oso2. Pickthall translation.',9,'ocr',0,box,uncertain=True)],box)
    bodybox=(40,348,270,349.5) if neighbor else (40,290,270,306)
    body=extract.Line([extract.Span('The original body continues without a terminal boundary',12,'ocr',0,bodybox)],bodybox)
    blocks=[extract.Block(0,bodybox,[body]),extract.Block(1,box,[note])]
    if mixed:blocks=[extract.Block(0,(40,290,270,360),[body,note])]
    raw=extract.RawPage(0,600,400,blocks,images=[extract.Image((0,0,600,400),1)])
    notes=[] if unproved else [skeleton.Region('note',lines=[note],number=9,bbox=box)]
    kept=[(blocks[0],[body])]
    if unproved:kept.append((blocks[-1],[note]))
    sk=skeleton.PageSkeleton(0,600,400,is_scan=True)
    candidates=[skeleton.Region('figure',bbox=(30,280,280,370))] if carrier else []
    return raw,kept,notes,sk,candidates


@pytest.mark.parametrize('boundary',[None,'mixed','carrier','unproved','neighbor'])
def test_opaque_note_context_requires_existing_isolated_source_note_ownership(boundary):
    raw,kept,notes,sk,candidates=prepared(boundary=='mixed',boundary=='carrier',boundary=='unproved',boundary=='neighbor')
    before=asdict(raw)
    kept,notes=skeleton._preserve_uncertain_ocr_regions(raw,kept,notes,sk,None,candidates)
    sk.regions.extend(skeleton.Region('body',lines=group,bbox=block.bbox) for block,group in kept)
    sk.regions.extend(notes+candidates)
    sk.regions.sort(key=skeleton._region_order)
    book=assemble.assemble([sk],skeleton.BookStyle(body_size=12),[raw])
    assert book.conservation.ok and asdict(raw)==before
    root=ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+build_epub.page_fragment(book,0)+'</root>')
    context=root.findall('aside[@class="source-note-context"]')
    if boundary is None:
        assert len(context)==1,'original note pixels must be non-body context after its body passage'
        assert len(context[0].findall('.//img'))==1
        assert not book.notes,'uncertain OCR digits must not produce an admitted numeric note'
        assert 'oso2' not in ''.join(root.itertext()),'the printed pixels own the damaged year'
        assert root.find('p') is not None and list(root).index(root.find('p'))<list(root).index(context[0])
        assert not root.findall('.//a[@class="noteref"]')
    else:
        assert not context,'mixed body pixels, an unrelated carrier, or no prior note role cannot qualify'
    assert sum(len(node.findall('.//img')) for node in root)==len(book.figures)


def test_isolated_note_padding_stops_halfway_before_detached_neighboring_source_line():
    raw,kept,notes,sk,candidates=prepared(neighbor=True)
    body=raw.blocks[0].lines[0];body.bbox=(40,345,270,348.5);body.spans[0].bbox=body.bbox;raw.blocks[0].bbox=body.bbox
    before=asdict(raw)
    kept,notes=skeleton._preserve_uncertain_ocr_regions(raw,kept,notes,sk,None,candidates)
    sk.regions.extend(skeleton.Region('body',lines=group,bbox=block.bbox) for block,group in kept)
    book=assemble.assemble([sk],skeleton.BookStyle(body_size=12),[raw])
    assert book.conservation.ok and asdict(raw)==before
    contexts=assemble.source_note_context_figures(book,0)
    assert len(contexts)==1
    element=book.pages[0][next(iter(contexts))]
    assert body.bbox[3]<element.bbox[1]<raw.blocks[1].bbox[1]
    assert element.bbox[3]>raw.blocks[1].bbox[3]
