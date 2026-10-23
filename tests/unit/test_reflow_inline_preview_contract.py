"""Synthetic optional preview metrics never authorize arbitrary CSS or coverage."""
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import build_epub, occurrence_coverage

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('style,valid', [('height:1.2em;vertical-align:-0.36em;width:auto',True),
    ('height:999em;vertical-align:-0.36em;width:auto',False),
    ('height:1.2em;vertical-align:-0.36em;width:auto;position:fixed',False),
    ('height:1.2em;vertical-align:0.36em;width:auto',False)])
def test_only_bounded_factory_source_preview_metrics_are_admitted(style,valid):
    root=ET.fromstring('<root xmlns="http://www.w3.org/1999/xhtml"><a class="source-word-image" href="#v4-d-0-b1-1"><img src="images/v4-d-0-b1-1-inline.png" alt="" style="'+style+'"/></a></root>')
    link=root[0]
    assert build_epub._written_word_preview_style(link[0],link) is valid
    link.set('class','arbitrary')
    assert not build_epub._written_word_preview_style(link[0],link)


def test_presentation_preview_cannot_claim_source_occurrence_coverage():
    source=[dict(text='garden',bbox=[10,20,50,32])]
    images={'images/preview.png':dict(page=0,bbox=[8,15,52,35],coverage_eligible=False)}
    rows=occurrence_coverage.classify(source,[],{}, {},images,0)
    assert rows[0]['disposition']=='missing'
    images['images/original.jpg']=dict(page=0,bbox=[8,15,52,35])
    rows=occurrence_coverage.classify(source,[],{}, {},images,0)
    assert rows[0]['disposition']=='print' and rows[0]['image']=='images/original.jpg'


def test_appended_print_does_not_insert_a_source_word_seam():
    from cps.services.reflow import reading_tiers, reading_flow
    markup='<p>'+reading_tiers.flagged_html('bridge-', 'images/v4-d-0-b1-1.jpg', 'v4-d-0-b1-1')+'side</p>'
    root=ET.fromstring(markup)
    assert reading_flow.projection(root,{})=='bridge-side'
    assert occurrence_coverage._text(root)=='bridge-side'


def test_only_appended_assistance_uses_a_temporary_seam_carrier():
    from cps.services.reflow import reading_tiers, vision_pipeline
    assist=reading_tiers.flagged_html('bridge-', 'images/v4-d-0-b1-1.jpg', 'v4-d-0-b1-1')
    opaque='<a class="source-word-image" href="#v4-d-0-b2-1"><img src="images/v4-d-0-b2-1.jpg" alt=""/></a>'
    pages=[dict(pno=0,body=['<p>'+assist+opaque+'</p>'])]
    carriers=vision_pipeline._join_control_carriers(pages)
    assert len(carriers)==1
    assert list(carriers.values())==['<a class="source-word-image" title="Inspect original print" href="#v4-d-0-b1-1"><img src="images/v4-d-0-b1-1.jpg" alt=""/></a>']
    assert opaque in pages[0]['body'][0]
    for carrier,original in carriers.items():
        assert carrier in pages[0]['body'][0]
        assert pages[0]['body'][0].replace(carrier,original)=='<p>'+assist+opaque+'</p>'


@pytest.mark.parametrize('title,valid', [('Inspect original print', True), ('Other label', False)])
def test_named_source_preview_link_keeps_a_fixed_factory_label(title, valid):
    link = ET.fromstring('<a xmlns="http://www.w3.org/1999/xhtml" class="source-word-image" '
                         'href="#v4-d-0-b1-0" title="'+title+'"><img src="images/v4-word.png" '
                         'alt="" style="height:1.2em;vertical-align:-0.36em;width:auto"/></a>')
    assert build_epub._written_word_preview_style(link[0], link) is valid
