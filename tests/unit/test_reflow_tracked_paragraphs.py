"""Tracked native lettering stays in complete source paragraph crops."""
import pytest
from cps.services.reflow import extract, skeleton
from tests.unit.test_reflow_rules_first_seams import _line
pytestmark=pytest.mark.unit


def test_tracked_text_fragments_keep_complete_source_paragraph_in_one_crop():
    lines=[_line('The diurnal s u n rejoices in its own sign. Diurnal',50,100,10),
           _line('m e r c u r y continues this same printed paragraph.',50,113,10),
           _line('Another distinct paragraph ends here.',50,140,10)]
    for line in lines[:2]:line.spacing_uncertain=True
    blocks=[extract.Block(i,line.bbox,[line]) for i,line in enumerate(lines)]
    raw=extract.RawPage(0,300,500,blocks)
    skel=skeleton.PageSkeleton(0,300,500)
    kept=skeleton._preserve_tracked_native_lines(raw,[(b,b.lines) for b in blocks],skel)
    assert len([r for r in skel.regions if r.kind=='figure'])==1
    artwork=next(r for r in skel.regions if r.kind=='artwork')
    assert artwork.lines==lines[:2]
    assert [line for _,group in kept for line in group]==lines[2:]


def test_closed_tracked_paragraphs_do_not_share_a_source_crop():
    lines=[_line('First tracked source paragraph ends.',50,100,10),
           _line('another distinct printed paragraph.',50,113,10)]
    for line in lines:line.spacing_uncertain=True
    blocks=[extract.Block(i,line.bbox,[line]) for i,line in enumerate(lines)]
    raw=extract.RawPage(0,300,500,blocks);skel=skeleton.PageSkeleton(0,300,500)
    skeleton._preserve_tracked_native_lines(raw,[(b,b.lines) for b in blocks],skel)
    assert len([r for r in skel.regions if r.kind=='figure'])==2
