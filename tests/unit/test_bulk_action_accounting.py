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
def test_settle_by_batch_chunks_and_never_silently_drops_an_id():
    helper = (ROOT / "frontend" / "src" / "lib" / "bulkResults.ts").as_uri()
    script = f"""
      import assert from 'node:assert/strict';
      import {{ settleByBatch }} from '{helper}';
      const ids = Array.from({{ length: 205 }}, (_, index) => index + 1);
      const calls = [];
      const injected = new Error('batch_too_large');
      const result = await settleByBatch(ids, 200, async (chunk) => {{
        calls.push(chunk);
        if (chunk[0] === 201) throw injected;
        // Deliberately omit id 2: a truncated/malformed success response must
        // classify it as failed instead of losing it from the accounting.
        return {{ succeededIds: chunk.filter((id) => id !== 2), failedIds: [] }};
      }});
      assert.deepEqual(calls.map((chunk) => chunk.length), [200, 5]);
      assert.equal(result.succeededIds.length, 199);
      assert.deepEqual(result.failedIds, [2, 201, 202, 203, 204, 205]);
      assert.deepEqual(result.errors, [injected]);
      assert.equal(result.succeededIds.length + result.failedIds.length, ids.length);
    """
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "--eval", script],
        text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.unit
def test_my_library_bulk_remove_is_mode_gated_primary_and_not_css_hidden():
    catalog = (ROOT / "frontend" / "src" / "pages" / "Catalog.tsx").read_text()
    bulk_bar = (ROOT / "frontend" / "src" / "components" / "BulkBar.tsx").read_text()
    styles = (ROOT / "frontend" / "src" / "components" / "BulkBar.module.css").read_text()
    queries = (ROOT / "frontend" / "src" / "lib" / "queries.ts").read_text()

    assert "personalLibrary={personalLibrary}" in catalog
    assert "{personalLibrary && (" in bulk_bar
    assert 'type="button" className={styles.actionPrimary}' in bulk_bar
    assert "onClick={onRemoveFromMyLibrary}" in bulk_bar
    assert "t('Remove from my library')" in bulk_bar
    assert "t('Delete from the global library')" in bulk_bar
    assert "announce(message, { assertive: failed > 0 })" in bulk_bar
    assert ".actionPrimary {" in styles
    assert "background: var(--accent)" in styles
    assert ".action, .actionPrimary, .actionDanger { gap: 0; font-size: 0; }" in styles
    assert "display: none" not in styles
    assert "settleByBatch(ids, 200" in queries
    assert "'/api/v1/books/my-library/batch'" in queries
    assert "{ operation: 'remove', book_ids: bookIds }" in queries


@pytest.mark.unit
def test_every_bulk_caller_uses_accounting_and_delete_evicts_only_successes():
    queries = (ROOT / "frontend" / "src" / "lib" / "queries.ts").read_text()
    bulk_bar = (ROOT / "frontend" / "src" / "components" / "BulkBar.tsx").read_text()
    assert queries.count("settleById(") == 4
    assert queries.count("settleByBatch(") == 1
    assert "succeededIds.forEach(removeBookFromCache)" in queries
    assert "err instanceof ApiError && err.status === 409" in queries
    assert bulk_bar.count("result.failedIds.length") >= 4
    assert "result.succeededIds.length" in bulk_bar
