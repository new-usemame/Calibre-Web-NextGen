"""Static regression pins for the 360px overflow Luna found in #498 verification."""

from pathlib import Path


def test_advanced_search_range_controls_can_shrink_on_mobile():
    css = (Path(__file__).resolve().parents[2] / "frontend/src/pages/AdvancedSearch.module.css").read_text()
    assert ".rangeRow .input { min-width: 0; }" in css
    assert "flex-wrap: wrap" in css
