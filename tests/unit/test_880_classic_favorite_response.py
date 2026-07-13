# SPDX-License-Identifier: GPL-3.0-or-later
"""#880 — object responses must not abort classic detail-page toggles."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
DETAILS_JS = Path(__file__).resolve().parents[2] / "cps" / "static" / "js" / "details.js"


def test_flash_response_handler_only_iterates_arrays():
    source = DETAILS_JS.read_text(encoding="utf-8")
    block = source.split("function handleResponse (data)", 1)[1].split('$(".sendbtn-form")', 1)[0]
    assert "Array.isArray(data)" in block
    assert block.index("Array.isArray(data)") < block.index("data.forEach")
