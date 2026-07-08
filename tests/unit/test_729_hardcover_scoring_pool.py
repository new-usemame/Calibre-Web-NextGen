# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioural tests for the Hardcover auto-match scoring pool (fork #729).

Adopted from community PR #729 by @Schmavery (Avery Morin). Two behaviours are
pinned:

1. `_process_book` scores the **whole** result pool the Hardcover API returns
   (per_page=50), not just the first 10. The reporter's symptom was that the
   correct book ("Dune") came back at position ~19 because Hardcover ranks
   author-in-title hits first, so the old `results[:10]` cutoff threw the real
   match away before scoring. The test proves a best match sitting at index 40
   is now found and applied — which is impossible under the old `[:10]` slice.

2. `_queue_for_review` stores the top **3** candidates (results and scores in
   lockstep), matching the review template, whose "Top N" label is derived from
   `results|length` and whose card loop renders `results[:3]`. Storing 5 made
   the label say "Top 5" while only 3 rendered (the reporter's screenshot);
   storing 3 keeps label and render consistent.
"""

from __future__ import annotations

import json
import pytest
from types import SimpleNamespace

from cps.tasks import auto_hardcover_id as mod
from cps.tasks.auto_hardcover_id import TaskAutoHardcoverID


def _bare_task(min_confidence=0.85):
    """A task instance without the real __init__ (which opens a Calibre DB).
    Only the attributes the methods under test touch are set."""
    t = object.__new__(TaskAutoHardcoverID)
    t.log = SimpleNamespace(debug=lambda *a, **k: None, info=lambda *a, **k: None,
                            warning=lambda *a, **k: None, error=lambda *a, **k: None)
    t.min_confidence = min_confidence
    t.auto_matched = 0
    t.queued_for_review = 0
    t.skipped_no_results = 0
    t.total_confidence = 0.0
    return t


def _fake_result(idx):
    """Minimal MetaRecord-like object the scoring/queue paths read."""
    return SimpleNamespace(
        id=f"hc-{idx}", title=f"Result {idx}", authors=["A"], url=f"u{idx}",
        cover=f"c{idx}", description="", series="", series_index=None,
        publisher="", publishedDate="", identifiers={"hardcover-id": str(idx)},
    )


def _fake_book():
    return SimpleNamespace(
        id=1, title="Dune", series_index=None,
        authors=[SimpleNamespace(name="Frank Herbert")],
        identifiers=[], series=[], publishers=[], pubdate=None,
    )


def test_process_book_scores_the_full_50_result_pool(monkeypatch):
    """A best match at index 40 of 50 must be found. Under the old results[:10]
    cutoff it would never be scored, and the task would pick a low-scoring early
    result (or queue) instead of applying the real match."""
    results = [_fake_result(i) for i in range(50)]

    class FakeProvider:
        def search(self, query):
            return results

        # Score = idx/100, except index 40 which is a confident 0.95 match.
        @staticmethod
        def calculate_confidence_score(result, **kwargs):
            idx = int(result.identifiers["hardcover-id"])
            return (0.95 if idx == 40 else idx / 1000.0), f"reason-{idx}"

    monkeypatch.setattr(mod, "Hardcover", FakeProvider)

    task = _bare_task()
    applied = {}
    monkeypatch.setattr(task, "_apply_hardcover_id",
                        lambda book_id, result: applied.update(id=result.id))
    monkeypatch.setattr(task, "_queue_for_review",
                        lambda *a, **k: applied.update(queued=True))

    task._process_book(_fake_book())

    # The index-40 result was scored and auto-applied — only possible if the
    # scoring loop covers the full pool.
    assert applied.get("id") == "hc-40", (
        "best match at index 40 was not applied; scoring pool is truncated")
    assert "queued" not in applied
    assert task.auto_matched == 1


def test_queue_for_review_caps_stored_candidates_at_three(monkeypatch):
    """Both hardcover_results and confidence_scores must store exactly the top 3
    so the review template's derived "Top N" label matches the 3 rendered cards."""
    captured = {}

    class FakeSession:
        def add(self, entry): captured["entry"] = entry
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr(mod.ub, "init_db_thread", lambda: FakeSession())

    task = _bare_task()
    scored = [{"result": _fake_result(i), "score": (50 - i) / 100.0,
               "reason": f"r{i}"} for i in range(5)]

    task._queue_for_review(1, "Dune", "Frank Herbert", "Dune Frank Herbert", scored)

    entry = captured["entry"]
    results = json.loads(entry.hardcover_results)
    scores = json.loads(entry.confidence_scores)
    assert len(results) == 3, f"expected 3 stored candidates, got {len(results)}"
    assert len(scores) == 3, f"results and scores must cap in lockstep, got {len(scores)}"
    # Highest-scoring three, in order.
    assert [r["id"] for r in results] == ["hc-0", "hc-1", "hc-2"]
