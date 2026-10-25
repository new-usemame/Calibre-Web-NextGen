"""Publication endpoints must be actual matched ink, not rectangle extremes.

All source words and coordinates are synthetic. Compilation, its physical row
metadata, and the checked paragraph merger use the actual production functions.
"""
import pytest
if __package__:
    from .test_round2_assembly import answer, block, optical, source
else:
    from test_round2_assembly import answer, block, optical, source
from cps.services.reflow import build_epub, source_display, vision_pipeline


@pytest.mark.parametrize('matched_tail', [True, False])
def test_published_endpoint_survives_unpublished_next_row_ink(tmp_path, matched_tail):
    prefix = 'Readers cross quiet fields while patient workers prepare'
    suffix = 'small tools.'
    pixels = optical(prefix, 20, 90, columns=8, step=45)
    pixels += optical(suffix, 385, 90, columns=2, step=45)
    pixels += optical('Several unrelated observations.', 20, 103, columns=3, step=45)
    if not matched_tail:
        # A similarly located but absent published edge must never become proof.
        prefix = prefix.rsplit(' ', 1)[0] + ' plan'
    reading = answer(block(prefix, ident='opening', next=True),
                     block(suffix, ident='following', previous=True))
    regions = {'opening': [[20, 90, 378, 109]],
               'following': [[385, 90, 473, 100]]}
    document = source([pixels])
    package = build_epub._Package(str(tmp_path / 'synthetic.epub'))
    try:
        page, report = vision_pipeline.compile_page(reading, pixels,
            source_display.SourceDisplay(document, 0), 0, package, {},
            pixel_words=pixels,
            regions=regions, body_owners={})
        opening, following = report['blocks']
        assert opening['published_tokens'] == prefix.split()
        assert following['published_tokens'] == suffix.split()
        if matched_tail:
            assert opening['printed_tail'] == prefix.split()[-1], (
                'Unpublished ink from the next row replaced the published tail')
            assert opening['printed_tail_box'] == pixels[7]['bbox']
            assert opening['printed_tail_row'] == following['printed_head_row']
        else:
            assert opening['printed_tail'] is None, (
                'An unmatched published endpoint acquired fabricated source proof')
            assert opening['printed_tail_box'] is None
            assert opening['printed_tail_row'] is None
        vision_pipeline._join_control_carriers([page])
        vision_pipeline._join_scanned_regions(page, report['blocks'])
        assert len(page['body']) == (1 if matched_tail else 2), (
            'Same-row joining must follow exact published endpoints, with no '
            'new paragraph break or unsupported physical endpoint')
        if matched_tail:
            from xml.etree import ElementTree as ET
            from tests.unit.test_round4_defects import plain
            fragment=ET.fromstring('<div xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'+page['body'][0]+'</div>')
            assert plain(fragment.find('{*}p')) == prefix + ' ' + suffix
    finally:
        package.abandon()
        document.close()
