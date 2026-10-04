"""A detached raised citation may split one printed line into two PDF blocks."""
import pytest
from cps.services.reflow import assemble
pytestmark=pytest.mark.unit


def fragments(x=171,y=198):
    left=assemble.Element('p',runs=[['t','The preceding hemisphere being feminine,']],
        bbox=(40,100,280,214),line_boxes=[(40,100,280,114),(40,200,170,214)])
    right=assemble.Element('p',runs=[['mark','1'],['t',' We can therefore expect these manifestations.']],
        bbox=(x,y,280,y+16),line_boxes=[(x,y,280,y+16)])
    return left,right


def test_same_printed_line_continues_past_marker_even_when_next_word_is_capitalized():
    left,right=fragments()
    joined=assemble._join_within_page([left,right],set())
    assert len(joined)==1
    assert joined[0].text.endswith('We can therefore expect these manifestations.')
    assert ['mark','1'] in joined[0].runs
    assert len(joined[0].line_boxes)==3


@pytest.mark.parametrize('x,y',[(171,217),(235,198)])
def test_vertical_or_horizontal_gap_does_not_authorize_a_capitalized_join(x,y):
    left,right=fragments(x,y)
    assert len(assemble._join_within_page([left,right],set()))==2
