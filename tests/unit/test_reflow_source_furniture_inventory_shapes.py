"""Eligibility uses real inventory Image objects as well as serialized audits."""
import copy
import pytest
from cps.services.reflow import build_epub,extract
from tests.unit.test_reflow_synthetic_furniture_ownership import inventory_book

pytestmark=pytest.mark.unit

@pytest.mark.parametrize('background',[True,False])
def test_real_inventory_image_shape_retains_source_ownership(background):
    book,figure=inventory_book();image=extract.Image((0,0,400,600),1.0)
    image.page_background=background
    book.source_inventory[0]['raw_images']=[image]
    before=copy.deepcopy(book.source_inventory)
    owned,protected=build_epub._source_furniture_pixel_boxes(book,0,figure)
    assert bool(owned)==background
    assert book.source_inventory==before
    if background:assert (20,100,180,112) in protected
