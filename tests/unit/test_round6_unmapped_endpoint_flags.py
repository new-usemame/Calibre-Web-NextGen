"""Known source endpoints constrain paired flags even without opening geometry."""
import pytest
from cps.services.reflow import vision_pipeline as vp


@pytest.mark.parametrize("side", ["tail", "head"])
def test_known_source_endpoint_contradiction_cannot_be_overruled_by_reader_flags(side):
    left = dict(role="body", continues_next=True, printed_tail=None)
    right = dict(role="body", continues_previous=True, printed_head=None,
                 first_line_x=None, source_regions=[])
    if side == "tail":
        left["printed_tail"] = "against"
    else:
        right["printed_head"] = "Different"
    decision = vp._continuation_decision(left, right, [],
        "<p>The observed movement was by</p>",
        "<p>Oakbay during the evening survey</p>")
    assert decision["status"] == "unmapped", "Known SOURCE disagreement must not become a flag-only join"


def test_unknown_optical_endpoints_retain_explicit_paired_reader_flag_policy():
    left = dict(role="body", continues_next=True, printed_tail=None)
    right = dict(role="body", continues_previous=True, printed_head=None,
                 first_line_x=None, source_regions=[])
    decision = vp._continuation_decision(left, right, [],
        "<p>The observed movement was by</p>",
        "<p>Oakbay during the evening survey</p>")
    assert decision == dict(status="joined", reason="reader_pair_without_optical_mapping")
