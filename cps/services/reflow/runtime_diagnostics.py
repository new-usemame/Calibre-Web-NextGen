# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Small, durable facts for a reflow run which does not return to Python.

The job ledger is intentionally the one durable record for a conversion.  These
helpers add facts that are useful after a native extension or service process
exits: a complete, text-free recovery census and a bounded build checkpoint
trail.  They do not make a crash diagnostic path, cache path, or OCR reading part
of a job record.
"""

import hashlib
import json


SCHEMA = 1
MAX_BUILD_MARKERS = 256
CROP_STRIDE = 16


def _label(value, limit=96):
    """Keep diagnostic labels single-line and never path-shaped."""
    text = str(value or "")[:limit]
    return "".join(char if char.isalnum() or char in " ._-" else "_"
                   for char in text).strip()


def _number(value, digits=3):
    try:
        return round(float(value or 0), digits)
    except (TypeError, ValueError):
        return 0


def recovery_record(recovery, mode, language):
    """Return a durable, privacy-safe recovery census and its canonical digest.

    ``mode_pages`` historically means every page assigned the OCR layer.  The
    separate word-bearing and zero-word counts prevent an incident reader from
    confusing that with ``Recovery.recovered_pnos``.
    """
    pages = []
    for pno, provenance in sorted(recovery.provenance.items()):
        words = int(getattr(provenance, "words", 0) or 0)
        pages.append({
            "page": int(pno),
            "layer": _label(getattr(provenance, "layer", ""), 20),
            "reason": _label(getattr(provenance, "reason", ""), 64),
            "engine": _label(getattr(provenance, "engine", ""), 96),
            "language": _label(getattr(provenance, "language", ""), 64),
            "language_identity": _label(getattr(provenance, "language_identity", ""), 96),
            "requested_dpi": _number(getattr(provenance, "requested_dpi", 0), 1),
            "effective_dpi": _number(getattr(provenance, "effective_dpi", 0), 1),
            "orientation": int(getattr(provenance, "orientation", 0) or 0),
            "orientation_confidence": _number(
                getattr(provenance, "orientation_confidence", 0), 2),
            "words": words,
            "uncertain_words": int(getattr(provenance, "uncertain_words", 0) or 0),
            "reused": bool(getattr(provenance, "reused", False)),
            "failed": bool(getattr(provenance, "failed", "")),
            "seconds": _number(getattr(provenance, "seconds", 0), 3),
        })
    options = {"mode": _label(mode, 20), "language": _label(language, 64)}
    canonical = json.dumps({"schema": SCHEMA, "options": options, "pages": pages},
                           ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    ocr_rows = [row for row in pages if row["layer"] == "ocr"]
    return {
        "kind": "recovery_provenance",
        "schema": SCHEMA,
        "options": options,
        "digest": hashlib.sha256(canonical.encode("ascii")).hexdigest(),
        "page_count": len(pages),
        "ocr_layer_pages": len(ocr_rows),
        "ocr_pages_with_words": sum(1 for row in ocr_rows if row["words"] > 0),
        "ocr_zero_word_pages": sum(1 for row in ocr_rows if row["words"] == 0),
        "failed_pages": sum(1 for row in pages if row["failed"]),
        "pages": pages,
    }


class BuildTrace(object):
    """Persist useful build checkpoints without turning every crop into a log."""

    def __init__(self, ledger, max_markers=MAX_BUILD_MARKERS, crop_stride=CROP_STRIDE):
        self.ledger = ledger
        self.max_markers = max(1, int(max_markers))
        self.crop_stride = max(1, int(crop_stride))
        self.markers = 0
        self.suppressed = 0
        self.last_crop = None
        self.last_crop_page = None
        # The trace is optional crash evidence.  If its own durable append fails,
        # normal conversion must keep its established validation/publication path;
        # do not turn every later crop into another failed fsync attempt.
        self.degraded = False
        self.disabled = False

    def _record(self, event, reserve_finish=True, **facts):
        # Keep one slot for normal completion.  A fatal exit instead leaves the
        # most recent pre-crash checkpoint, which is the useful fact in that case.
        limit = self.max_markers - (1 if reserve_finish else 0)
        if self.disabled or self.markers >= limit:
            self.suppressed += 1
            return
        try:
            self.ledger.record(dict(kind="build_trace", event=event, **facts), durable=True)
        except OSError:
            # Keep neither exception text nor a path: both are unhelpful to a
            # reader of the job journal, and this is only optional telemetry.
            self.degraded = True
            self.disabled = True
            self.suppressed += 1
            return
        self.markers += 1

    def degradation_record(self):
        """A stable, text-free fact for the normal task path after a trace loss."""
        return {"kind": "runtime_diagnostics", "event": "build_trace_degraded",
                "reason": "durable_trace_write_failed"}

    def __call__(self, event):
        """Accept only builder-generated scalar event facts."""
        kind = event.get("kind") if isinstance(event, dict) else ""
        if kind == "phase":
            self._record("phase", phase=_label(event.get("phase"), 48))
            return
        if kind == "evidence_page":
            self._record("evidence_page", page=int(event["page"]),
                         ordinal=int(event["ordinal"]), total=int(event["total"]))
            return
        if kind != "figure_crop":
            return
        page = int(event["page"])
        ordinal = int(event["ordinal"])
        total = int(event["total"])
        self.last_crop = {"page": page, "ordinal": ordinal, "total": total}
        page_changed = page != self.last_crop_page
        self.last_crop_page = page
        if ordinal == 1 or ordinal == total or page_changed or ordinal % self.crop_stride == 0:
            self._record("figure_crop", **self.last_crop)

    def finish(self):
        """Write a compact normal-completion summary; a crash has the last marker."""
        facts = {"markers": self.markers, "suppressed": self.suppressed}
        if self.last_crop is not None:
            facts["last_crop"] = self.last_crop
        self._record("finish", reserve_finish=False, **facts)
