"""No recognition: fixed witness fixtures exercise real transcript qualification."""
import hashlib
import pytest
pytestmark = pytest.mark.unit
import unittest
from types import SimpleNamespace as NS
from cps.services.reflow import (extract, transcript, source_inventory, skeleton,
    assemble, enriched_source, _layout_furniture)


def case(witness=None, records=(), orientation=0, links=(), navigation=(), pdf_rotation=0):
    text='HEADER';box=(20,30,50,40)
    chars=tuple((i,i+1,20+5*i,30,25+5*i,40) for i in range(len(text)))
    span=extract.Span(text,10,'Times',0,box,40,char_boxes=chars)
    line=extract.Line([span],box)
    raw=extract.RawPage(0,400,600,[extract.Block(0,box,[line])],[extract.Image((0,0,400,600),1)],0)
    raw.text_layer_overpainted=True
    raw.source_links=list(links)
    provenance=dict(layer='native',reason='native_trusted',verification={},uncertain_words=0,uncertain=[])
    if witness is not None:
        recognition=NS(words=[NS(text=witness,pdf_bbox=box,confidence=99)])
        raw,evidence=transcript.corroborate(raw,recognition)
        evidence.update(recognition_orientation=0,recognition_sha256=hashlib.sha256(witness.encode()).hexdigest())
        provenance.update(reason='verify_scan_transcript',verification=evidence,flags=['native_transcript_corroborated'])
    line=raw.blocks[0].lines[0]
    region=skeleton.Region(kind='furniture',lines=[line],bbox=box)
    inventory=source_inventory.capture(source_inventory.catalog(raw),skeleton.PageSkeleton(0,400,600,[region]))
    book=assemble.Book(pages={0:[]},elements=[],furniture=[text],style=skeleton.BookStyle(body_size=10))
    provenance.update(uncertain_words=len(records),uncertain=list(records),orientation=orientation)
    book.source_navigation=[dict(status='destination_unmapped',**n) if 'status' not in n else n for n in navigation]
    source=enriched_source.prepare_source_page(book,0,provenance,records)
    source_inventory.validate(inventory,raw)
    return _layout_furniture.project(book,0,source,raw,inventory,doc={0:NS(rotation=pdf_rotation)})

class FurnitureAdmission(unittest.TestCase):
    def test_corroborated_original_native_furniture_is_admissible(self):
        projections,reasons=case('HEADER')
        self.assertFalse(reasons,reasons)
        self.assertEqual(len(projections),1)
        self.assertIn('HEADER',projections[0]['html'])
    def test_uncorroborated_overpainted_furniture_is_not_promoted(self):
        projections,reasons=case()
        self.assertFalse(projections)
        self.assertTrue(reasons)
    def test_disputed_native_furniture_is_not_promoted_as_clean_text(self):
        projections,reasons=case('HEATER')
        self.assertFalse(projections)
        self.assertTrue(reasons)

if __name__=='__main__':unittest.main()


def test_unrelated_source_uncertainty_stays_canonical_and_does_not_hide_clean_furniture():
    projections, reasons = case('HEADER', [dict(token='disputed', source_bbox=[20,100,70,115])])
    assert not reasons
    assert len(projections) == 1

@pytest.mark.parametrize('box,angle', [([20,30,50,40],0), (None,0), ([20,100,70,115],90)])
def test_overlapping_or_unmapped_uncertainty_still_refuses_plain_furniture(box,angle):
    projections, reasons = case('HEADER',[dict(token='HEADER',source_bbox=box)],angle)
    assert not projections
    assert reasons


def test_unrelated_body_navigation_does_not_hide_clean_furniture():
    projections, reasons = case('HEADER',links=[dict(rect=[100,100,120,115])],
        navigation=[dict(pno=1,dest_page=0,dest_point=[100,100],status='destination_unmapped')])
    assert not reasons
    assert len(projections) == 1

@pytest.mark.parametrize('links,navigation', [
    ([dict(rect=[20,30,50,40])],[]), ([dict(status='unmapped')],[]),
    ([],[dict(pno=1,dest_page=0,dest_point=[25,35])]),
    ([],[dict(pno=1,dest_page=0,dest_point=None)]),
])
def test_furniture_owned_or_unlocated_navigation_stays_unsupported(links,navigation):
    projections,reasons=case('HEADER',links=links,navigation=navigation)
    assert not projections
    assert reasons


def test_rotated_native_navigation_cannot_clear_unrotated_furniture():
    projections,reasons=case('HEADER',navigation=[dict(pno=1,dest_page=0,dest_point=[565,25])],pdf_rotation=90)
    assert not projections
    assert reasons
