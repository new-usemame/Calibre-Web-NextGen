# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]


@pytest.mark.unit
def test_settle_by_id_reports_exact_successes_and_failures():
    helper = (ROOT / "frontend" / "src" / "lib" / "bulkResults.ts").as_uri()
    script = f"""
      import assert from 'node:assert/strict';
      import {{ settleById }} from '{helper}';
      const result = await settleById([223, 222, 221], (id) =>
        id === 222 ? Promise.reject(new Error('injected')) : Promise.resolve(id));
      assert.deepEqual(result, {{ succeededIds: [223, 221], failedIds: [222] }});
    """
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "--eval", script],
        text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.unit
def test_every_bulk_caller_uses_accounting_and_delete_evicts_only_successes():
    queries = (ROOT / "frontend" / "src" / "lib" / "queries.ts").read_text()
    bulk_bar = (ROOT / "frontend" / "src" / "components" / "BulkBar.tsx").read_text()
    assert queries.count("settleById(") == 4
    assert "succeededIds.forEach(removeBookFromCache)" in queries
    assert "err instanceof ApiError && err.status === 409" in queries
    assert bulk_bar.count("result.failedIds.length") >= 4
    assert "result.succeededIds.length" in bulk_bar
