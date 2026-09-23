# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Behavioural seams for durable reflow runtime evidence."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cps.services.reflow import runtime_diagnostics

pytestmark = pytest.mark.unit


class _Ledger(object):
    def __init__(self):
        self.entries = []

    def record(self, entry, durable=False):
        self.entries.append((dict(entry), durable))


def _provenance(**values):
    defaults = dict(layer="native", reason="native_trusted", engine="",
                    language="", language_identity="", requested_dpi=0,
                    effective_dpi=0, orientation=0, orientation_confidence=0,
                    words=0, uncertain_words=0, reused=False, failed="", seconds=0)
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_recovery_record_keeps_zero_word_ocr_pages_distinct_from_word_pages():
    """A reused OCR layer can honestly contain no recognized words.

    Book 569 made this distinction consequential: mode-page count refers to the
    selected OCR layer, while routing's recovered page set is word-bearing only.
    Removing zero-word rows or the options from the digest makes this fail.
    """
    recovery = SimpleNamespace(provenance={
        4: _provenance(layer="ocr", reason="image_only", words=0, reused=True),
        5: _provenance(layer="ocr", reason="damaged_layer", words=7, reused=True),
        6: _provenance(),
    })

    record = runtime_diagnostics.recovery_record(recovery, "auto", "eng")
    changed = runtime_diagnostics.recovery_record(recovery, "textless", "eng")

    assert record["ocr_layer_pages"] == 2
    assert record["ocr_pages_with_words"] == 1
    assert record["ocr_zero_word_pages"] == 1
    assert [row["page"] for row in record["pages"]] == [4, 5, 6]
    assert record["digest"] != changed["digest"]


def test_build_trace_is_bounded_but_keeps_completion_and_last_crop():
    """A crop-heavy PDF must not make a huge journal, yet a clean finish names
    the final crop.  Replacing sampling with one record per crop makes the bound
    fail; removing final state makes the recovery evidence fail.
    """
    ledger = _Ledger()
    trace = runtime_diagnostics.BuildTrace(ledger, max_markers=6, crop_stride=3)
    trace({"kind": "phase", "phase": "assembly_start"})
    for ordinal in range(1, 41):
        trace({"kind": "figure_crop", "page": ordinal // 10,
               "ordinal": ordinal, "total": 40})
    trace.finish()

    entries = [entry for entry, durable in ledger.entries]
    assert len(entries) <= 6
    assert all(durable for _, durable in ledger.entries)
    assert entries[-1]["event"] == "finish"
    assert entries[-1]["last_crop"] == {"page": 4, "ordinal": 40, "total": 40}
    assert sum(entry["event"] == "figure_crop" for entry in entries) < 40


def test_build_trace_degrades_once_when_its_optional_durable_write_fails():
    """A trace fsync failure is crash evidence loss, not a conversion failure.

    The first trace append raises an I/O error.  Later phase/crop/finish events
    must not retry it, while the public degraded fact has no exception/path text.
    """
    class BrokenTraceLedger(object):
        def __init__(self):
            self.calls = 0

        def record(self, entry, durable=False):
            self.calls += 1
            raise OSError("private journal path unavailable")

    ledger = BrokenTraceLedger()
    trace = runtime_diagnostics.BuildTrace(ledger)
    trace({"kind": "phase", "phase": "assembly_start"})
    trace({"kind": "figure_crop", "page": 7, "ordinal": 1, "total": 2})
    trace.finish()

    assert trace.degraded and trace.disabled
    assert ledger.calls == 1
    assert trace.degradation_record() == {
        "kind": "runtime_diagnostics", "event": "build_trace_degraded",
        "reason": "durable_trace_write_failed"}


def test_build_trace_does_not_suppress_cancellation_or_other_required_failures():
    """Only OSError from the optional trace append is contained.

    A cancellation-class exception must still escape to the task's established
    cancellation handling; widening the catch to Exception makes this red.
    """
    from cps.services.reflow import build_epub

    class CancelLedger(object):
        def record(self, entry, durable=False):
            raise build_epub.BuildCancelled("cancel requested")

    with pytest.raises(build_epub.BuildCancelled):
        runtime_diagnostics.BuildTrace(CancelLedger())(
            {"kind": "phase", "phase": "assembly_start"})


def test_service_exit_recorder_replaces_only_its_owned_record(tmp_path):
    """A service exit is retained as numeric supervisor evidence, atomically.

    The recorder does not accept an arbitrary destination through its CLI; this
    direct function probe supplies a temporary owned directory for the same write.
    Dropping numeric validation or atomic replacement makes this fail.
    """
    script = Path(__file__).parents[2] / "scripts" / "record_runtime_service_exit.py"
    spec = importlib.util.spec_from_file_location("runtime_exit_recorder", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    first = module.record_exit(str(tmp_path), "-1", "9", now=100)
    second = module.record_exit(str(tmp_path), "0", "0", now=101)
    path = tmp_path / module.RECORD_NAME

    assert first["exit_code"] == -1
    assert json.loads(path.read_text()) == second
    with pytest.raises(ValueError):
        module.record_exit(str(tmp_path), "not-a-code", "0", now=102)
    assert json.loads(path.read_text()) == second
