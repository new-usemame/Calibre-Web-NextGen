# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The orchestrator: read the PDF, decide, spend, check, keep or discard.

Three rules run through the whole of it.

*The deterministic pass is the product.* Every page starts with an answer that cost
nothing. The model is only ever asked to improve a page that the deterministic pass
could not resolve, and its answer is adopted only if it survives the gate. A run with
no key configured, or one that stops at its cap on page three, still produces a book.

*Nothing is paid for twice.* Every model answer is cached under the PDF's own
fingerprint, the page, the model and the prompt version, so a crash, a cancel or a
cap stop costs the finished pages nothing on the next run.

*Stopping is a normal outcome.* Cancel and cap-stop leave a consistent job behind:
the pages already paid for stay, the reason is named, and the caller is told.
"""

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import assemble, assess, build_epub, extract, gate, prompts, route, skeleton
from .ledger import CapExceeded

log = logging.getLogger(__name__)

#: Raster scale for the page image sent alongside the text. RESEARCH §2.3: 1.5x is
#: where the superscript markers stop being ambiguous, and 2x costs more for nothing.
RASTER_SCALE = 1.5
RASTER_QUALITY = 80
#: A page image larger than this is re-encoded harder rather than sent as it is.
RASTER_MAX_BYTES = 900 * 1024

STAGES = ("read", "assess", "skeleton", "assemble", "route", "model", "build")


@dataclass
class Progress(object):
    stage: str
    page: int = 0
    pages: int = 0
    spend_usd: float = 0.0
    message: str = ""

    @property
    def fraction(self):
        return min(1.0, self.page / float(self.pages)) if self.pages else 0.0


@dataclass
class PageOutcome(object):
    """What happened to one page, in the terms the report page uses."""

    pno: int
    source: str = "deterministic"      # deterministic | model
    gate: str = ""                     # PASS | FAIL | NOT_APPLICABLE
    reasons: List[str] = field(default_factory=list)
    gate_reasons: List[str] = field(default_factory=list)
    uncertain: List[object] = field(default_factory=list)
    cost_usd: float = 0.0
    cached: bool = False
    model: str = ""

    def to_dict(self):
        return {"pno": self.pno, "source": self.source, "gate": self.gate,
                "reasons": self.reasons, "gate_reasons": self.gate_reasons,
                "uncertain": self.uncertain, "cost_usd": round(self.cost_usd, 6),
                "cached": self.cached, "model": self.model}


@dataclass
class ReflowResult(object):
    book: object = None
    assessment: object = None
    style: object = None
    page_html: Dict[int, str] = field(default_factory=dict)
    outcomes: Dict[int, PageOutcome] = field(default_factory=dict)
    routed: List[int] = field(default_factory=list)
    routing: dict = field(default_factory=dict)
    pages_done: int = 0
    reused: int = 0
    gate_failures: int = 0
    spend_usd: float = 0.0
    stopped: Optional[str] = None
    fingerprint: str = ""

    @property
    def pages(self):
        return len(self.page_html)

    def to_dict(self):
        return {"pages": self.pages, "routed": len(self.routed),
                "pages_done": self.pages_done, "reused": self.reused,
                "gate_failures": self.gate_failures,
                "spend_usd": round(self.spend_usd, 6), "stopped": self.stopped,
                "routing": self.routing,
                "verdict": self.assessment.verdict if self.assessment else "",
                "outcomes": [o.to_dict() for o in self.outcomes.values()]}


class PageCache(object):
    """One JSON file per answered page, keyed by everything that could change it."""

    def __init__(self, directory):
        self.directory = str(directory)

    def key(self, fingerprint, pno, model_id):
        raw = "|".join([fingerprint, str(pno), model_id or "", prompts.PROMPT_VERSION])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _path(self, key):
        return os.path.join(self.directory, key[:2], key + ".json")

    def get(self, fingerprint, pno, model_id):
        path = self._path(self.key(fingerprint, pno, model_id))
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (IOError, OSError, ValueError):
            return None

    def put(self, fingerprint, pno, model_id, payload):
        path = self._path(self.key(fingerprint, pno, model_id))
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(tmp, path)
        return path


def sample_pages(book, style, count):
    """The first *count* body pages, starting where the front matter stops.

    A sample taken from page 1 shows the user a title page, a copyright notice and a
    table of contents, which says nothing about how the book itself converts.
    """
    pages = sorted(book.pages)
    if not pages:
        return []
    first_body = pages[0]
    for pno in pages:
        elements = book.pages.get(pno) or []
        words = sum(len(el.text.split()) for el in elements if el.kind == "p")
        if words >= 120:
            first_body = pno
            break
    start = pages.index(first_body)
    return pages[start:start + int(count)]


def run(doc, client=None, ledger=None, cache=None, page_numbers=None,
        progress=None, should_stop=None, require_figure_caption=True):
    """Convert one document. Returns what happened as well as what was produced."""
    report = _reporter(progress)
    result = ReflowResult()

    report(Progress(stage="read", message="reading the PDF"))
    raw_pages = extract.read_pages(doc, page_numbers)
    result.fingerprint = extract.document_fingerprint(doc)

    report(Progress(stage="assess", message="looking at the text layer"))
    result.assessment = assess.assess_pages(raw_pages)

    report(Progress(stage="skeleton", message="measuring the page geometry"))
    outline = extract.outline(doc)
    style = skeleton.book_style(raw_pages,
                               outline if skeleton.outline_is_useful(outline) else None)
    result.style = style
    skeletons = [skeleton.page_skeleton(raw, style) for raw in raw_pages]

    report(Progress(stage="assemble", message="putting the text back together"))
    book = assemble.assemble(skeletons, style, raw_pages)
    result.book = book

    report(Progress(stage="route", message="deciding which pages need a model"))
    routes = route.route_pages(book, skeletons, result.assessment)
    result.routing = route.summarise(routes)
    result.routed = route.routed_pages(routes)

    for pno in sorted(book.pages):
        result.page_html[pno] = build_epub.page_fragment(book, pno, style)
        result.outcomes[pno] = PageOutcome(pno=pno,
                                           reasons=list(book.page_reasons(pno)))

    pages_total = len(book.pages)
    if client is None or not result.routed:
        report(Progress(stage="build", page=pages_total, pages=pages_total))
        return result

    levels = sorted({el.level for el in book.elements if el.kind == "h" and el.level})
    ladder = levels or [1]

    for index, pno in enumerate(result.routed, start=1):
        if should_stop is not None and should_stop():
            result.stopped = "cancelled"
            break
        try:
            outcome = _edit_one_page(doc, book, pno, client, ledger, cache, result,
                                     ladder, require_figure_caption)
        except CapExceeded as exc:
            log.info("reflow: %s", exc)
            result.stopped = "cost_cap"
            break
        result.outcomes[pno] = outcome
        result.pages_done += 1
        result.spend_usd = round(result.spend_usd + outcome.cost_usd, 6)
        report(Progress(stage="model", page=index, pages=len(result.routed),
                        spend_usd=result.spend_usd,
                        message="page %d of %d reviewed" % (index, len(result.routed))))

    report(Progress(stage="build", page=pages_total, pages=pages_total,
                    spend_usd=result.spend_usd))
    return result


def _edit_one_page(doc, book, pno, client, ledger, cache, result, ladder,
                   require_figure_caption):
    outcome = PageOutcome(pno=pno, reasons=list(book.page_reasons(pno)),
                          model=getattr(client, "model_id", ""))
    source_text = assemble.page_source_text(book, pno)

    cached = cache.get(result.fingerprint, pno, outcome.model) if cache else None
    if cached is not None:
        outcome.cached = True
        result.reused += 1
        _adopt(result, book, pno, outcome, cached.get("html") or "",
               cached.get("uncertain") or [], ladder, require_figure_caption,
               ledger=ledger, cost=0.0, cached=True)
        return outcome

    image = _raster(doc, pno)
    answer = client.edit_page(source_text, image_jpeg=image, ladder=ladder,
                              page_label=str(pno + 1), ledger=ledger)
    outcome.cost_usd = float(getattr(answer, "cost_usd", 0.0) or 0.0)
    outcome.model = getattr(answer, "model", outcome.model)

    _adopt(result, book, pno, outcome, answer.html, answer.uncertain, ladder,
           require_figure_caption, ledger=ledger, cost=outcome.cost_usd,
           answer=answer)

    if cache is not None and outcome.gate == "PASS":
        cache.put(result.fingerprint, pno, outcome.model,
                  {"html": answer.html, "uncertain": list(answer.uncertain),
                   "model": outcome.model, "prompt_version": prompts.PROMPT_VERSION})
    return outcome


def _adopt(result, book, pno, outcome, html, uncertain, ladder,
           require_figure_caption, ledger=None, cost=0.0, answer=None,
           cached=False):
    """Take the model's page only if it still says what the page said.

    The figure count is part of that: the model is sent the page's text layer, in
    which an illustration leaves no words at all, so an answer that simply omits it
    would pass the word gate and lose the picture.
    """
    source_text = assemble.page_source_text(book, pno)
    figures = sum(1 for el in (book.pages.get(pno) or []) if el.kind == "fig")
    words = gate.check_word_preservation(source_text, html)
    structure = gate.check_structure(html, ladder=ladder,
                                     require_figure_caption=require_figure_caption,
                                     figures_expected=figures)

    outcome.uncertain = list(uncertain or [])
    if words.ok and structure.ok:
        outcome.gate = "PASS"
        outcome.source = "model"
        result.page_html[pno] = html
    else:
        outcome.gate = "FAIL" if words.verdict != "NOT_APPLICABLE" or not structure.ok \
            else "NOT_APPLICABLE"
        outcome.gate_reasons = _gate_reasons(words, structure)
        result.gate_failures += 1

    if ledger is not None:
        entry = {"kind": "page", "page": pno, "cost_usd": cost,
                 "cached": bool(cached),
                 "gate": outcome.gate, "model": outcome.model,
                 "reasons": outcome.reasons, "gate_reasons": outcome.gate_reasons,
                 "similarity": round(words.similarity, 4),
                 "uncertain": len(outcome.uncertain)}
        if answer is not None:
            entry["prompt_tokens"] = answer.prompt_tokens
            entry["completion_tokens"] = answer.completion_tokens
            entry["attempts"] = answer.attempts
            entry["cost_source"] = answer.cost_source
        ledger.record(entry)
    return outcome


def _gate_reasons(words, structure):
    reasons = []
    if words.verdict != "PASS":
        if words.missing_count:
            reasons.append("%d words in the page are missing from the answer"
                           % words.missing_count)
        if words.invented_count:
            reasons.append("%d words in the answer are not on the page"
                           % words.invented_count)
        if words.case_only_count:
            reasons.append("%d words came back with their capitalisation changed"
                           % words.case_only_count)
        if not reasons:
            reasons.append("word preservation: %s" % words.verdict)
    reasons.extend(structure.reasons)
    return reasons


def _raster(doc, pno):
    try:
        return extract.render_page_jpeg(doc, pno, scale=RASTER_SCALE,
                                        quality=RASTER_QUALITY,
                                        max_bytes=RASTER_MAX_BYTES)
    except Exception as exc:                                  # pragma: no cover
        log.warning("reflow: could not render page %d: %s", pno, exc)
        return None


def _reporter(progress):
    if progress is None:
        return lambda event: None

    def report(event):
        try:
            progress(event)
        except Exception:                                     # pragma: no cover
            log.exception("reflow: progress callback failed")
    return report
