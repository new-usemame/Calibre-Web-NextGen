"""Publisher uses actual canonical portrait-head reasons, rejecting invented ones."""
import pytest
from cps.services.reflow import build_epub
from tests.unit.test_reflow_synthetic_furniture_ownership import inventory_book

pytestmark=pytest.mark.unit

@pytest.mark.parametrize('reason,expected',[('repeated_portrait_head',True),('repeated_scan_portrait_head',False)])
def test_portrait_reason_matches_source_classifier(reason,expected):
    book,figure=inventory_book();book.source_inventory[0]['regions'][0]['reason']=reason
    owned,protected=build_epub._source_furniture_pixel_boxes(book,0,figure)
    assert bool(owned)==expected
    if expected:assert (20,100,180,112) in protected
