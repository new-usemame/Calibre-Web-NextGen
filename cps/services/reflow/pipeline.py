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
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import (annotate, assemble, assess, build_epub, extract, gate, prompts, route,
               skeleton, source)
from .ledger import CapExceeded
from .model import AttemptCancelled, ModelError, UncertainBilling, UnusableAnswer

log = logging.getLogger(__name__)

#: Raster scale for the page image sent alongside the text. RESEARCH §2.3: 1.5x is
#: where the superscript markers stop being ambiguous, and 2x costs more for nothing.
RASTER_SCALE = 1.5
RASTER_QUALITY = 80
#: A page image larger than this is re-encoded harder rather than sent as it is.
RASTER_MAX_BYTES = 900 * 1024

STAGES = ("read", "assess", "recover", "skeleton", "assemble", "route", "model",
          "build")


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
    marked: int = 0                    # uncertain readings highlighted in the text
    cost_usd: float = 0.0
    cached: bool = False
    model: str = ""
    recovered_markers: List[int] = field(default_factory=list)

    def to_dict(self):
        return {"pno": self.pno, "source": self.source, "gate": self.gate,
                "reasons": self.reasons, "gate_reasons": self.gate_reasons,
                "uncertain": self.uncertain, "marked": self.marked,
                "cost_usd": round(self.cost_usd, 6),
                "cached": self.cached, "model": self.model,
                "recovered_markers": list(self.recovered_markers)}


@dataclass
class ReflowResult(object):
    book: object = None
    assessment: object = None
    style: object = None
    recovery: object = None
    page_html: Dict[int, str] = field(default_factory=dict)
    outcomes: Dict[int, PageOutcome] = field(default_factory=dict)
    routed: List[int] = field(default_factory=list)
    routing: dict = field(default_factory=dict)
    pages_done: int = 0
    reused: int = 0
    gate_failures: int = 0
    spend_usd: float = 0.0
    pending_usd: float = 0.0
    stopped: Optional[str] = None
    fingerprint: str = ""

    @property
    def pages(self):
        return len(self.page_html)

    def to_dict(self):
        return {"pages": self.pages, "routed": len(self.routed),
                "pages_done": self.pages_done, "reused": self.reused,
                "gate_failures": self.gate_failures,
                "spend_usd": round(self.spend_usd, 6),
                "pending_usd": round(self.pending_usd, 6), "stopped": self.stopped,
                "routing": self.routing,
                "verdict": self.assessment.verdict if self.assessment else "",
                "outcomes": [o.to_dict() for o in self.outcomes.values()]}


class PageCache(object):
    """One JSON file per answered page, keyed by everything that could change it.

    Which includes the page itself. The book, the page number, the model and the
    prompt are the obvious four, and between releases they are the ones least likely
    to move: what moves is the deterministic reader, and with it the words that get
    sent. A note number repaired, a marker recovered off the scan, a sentence
    stitched over a page turn -- each of those makes the page a different question,
    and an answer bought for the old one is not an answer to it. So the words go into
    the key, and a reader that has learned something invalidates exactly the pages it
    now reads differently. Per-page headings, hints, ladder and raster parameters
    identify the rest of the question: equal words do not imply equal instructions.
    """

    def __init__(self, directory):
        self.directory = str(directory)

    def key(self, fingerprint, pno, model_id, source_text, context=None):
        raw = "|".join([fingerprint, str(pno), model_id or "", prompts.PROMPT_VERSION,
                        hashlib.sha256((source_text or "").encode("utf-8")).hexdigest(),
                        json.dumps(context, sort_keys=True, ensure_ascii=False)])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _path(self, key):
        return os.path.join(self.directory, key[:2], key + ".json")

    def get(self, fingerprint, pno, model_id, source_text, context=None):
        path = self._path(self.key(fingerprint, pno, model_id, source_text, context))
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (IOError, OSError, ValueError):
            return None

    def put(self, fingerprint, pno, model_id, source_text, payload, context=None):
        path = self._path(self.key(fingerprint, pno, model_id, source_text, context))
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        # A fixed "key.tmp" name is shared by every concurrent writer of this key:
        # interleaved dumps write through each other's file, and one writer's
        # os.replace can rename the temporary away under the other. Each write gets
        # its own temporary file, so a writer only ever publishes a complete payload.
        tmp = "%s.%s.tmp" % (path, uuid.uuid4().hex)
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return path


#: A page with this much prose on it is the book rather than its front matter.
BODY_WORDS = 120
#: How many refusals in a row mean the provider, not the page: a revoked key, a
#: model withdrawn, a region block. Walking a 700-page book into all of them wastes
#: the user's time and tells them nothing they could not have been told on page 3.
MAX_CONSECUTIVE_REFUSALS = 3

#: How many pages a cost estimate reads. The deterministic pass over a 700-page
#: book is a minute of work; the page that asks a user to authorise a spend has to
#: answer in the time it takes to render.
SURVEY_PAGES = 40


def first_body_page(book):
    """Where the book starts, as opposed to where the file starts."""
    pages = sorted(book.pages)
    if not pages:
        return 0
    for pno in pages:
        elements = book.pages.get(pno) or []
        if sum(len(el.text.split()) for el in elements if el.kind == "p") >= BODY_WORDS:
            return pno
    return pages[0]


def sample_pages(book, style, count):
    """The first *count* body pages, starting where the front matter stops.

    A sample taken from page 1 shows the user a title page, a copyright notice and a
    table of contents, which says nothing about how the book itself converts.
    """
    pages = sorted(book.pages)
    if not pages:
        return []
    start = pages.index(first_body_page(book))
    return pages[start:start + int(count)]


def deterministic_window(doc, pages):
    """The deterministic pass over the first *pages* pages, and nothing else."""
    count = max(1, min(int(pages), doc.page_count))
    return assemble.deterministic_book(doc, page_numbers=list(range(count)))


def survey_pages(page_count, sample=SURVEY_PAGES):
    """Pages spread evenly through the book, ends included.

    Evenly rather than the first N: front matter, plates and the index route for
    different reasons than the body does, and an estimate taken from the first forty
    pages of a scholarly book is an estimate of its front matter.
    """
    count = max(1, int(page_count))
    take = max(1, min(int(sample), count))
    if take == 1:
        return [0]
    step = (count - 1) / float(take - 1)
    return sorted({int(round(index * step)) for index in range(take)})


def survey(doc, sample=SURVEY_PAGES):
    """What a conversion would cost, measured rather than assumed.

    The deterministic pass runs on a sample of pages and the routed share is scaled
    to the book. The number the user consents to is therefore this book's own
    routing rate, not a fixed percentage — but it is an estimate from a sample, and
    ``sampled`` says how big the sample was so the page can say so. Pages that need
    OCR are recovered during the survey, so the routing rate is the recovered book's
    own, and the local time that stage takes is measured and quoted separately from
    the model's price.
    """
    pages = survey_pages(doc.page_count, sample)
    result = run(doc, page_numbers=pages)
    routed = len(result.routed)
    share = routed / float(len(pages)) if pages else 0.0
    projected = int(round(share * doc.page_count))
    quote = route.estimate(projected, doc.page_count)
    # The whole book's recovery census is text-layer geometry only: no page is
    # recognized twice for it.
    raw_all = extract.read_pages(doc)
    wanted = source.candidates(raw_all)
    ocr_seconds = result.recovery.seconds if result.recovery else 0.0
    attempted = result.recovery.attempted if result.recovery else 0
    per_page = (ocr_seconds / attempted) if attempted else 0.0
    quote.update({
        "sampled": len(pages),
        "sampled_routed": routed,
        "verdict": result.assessment.verdict if result.assessment else "",
        "text_layer": bool(result.assessment and result.assessment.verdict
                           not in ("NO_TEXT_LAYER", "GARBAGE_TEXT")),
        "reasons": dict((result.routing or {}).get("reasons") or {}),
        "non_latin_share": (result.assessment.non_latin_share
                            if result.assessment else 0.0),
        "ocr_candidates": len(wanted),
        "ocr_image_only": sum(1 for _, reason in wanted if reason == "image_only"),
        "ocr_damaged": sum(1 for _, reason in wanted if reason == "damaged_layer"),
        "ocr_estimated_seconds": int(round(per_page * len(wanted))),
        "ocr_engine_unavailable": bool(result.recovery
                                       and result.recovery.engine_unavailable),
    })
    return quote


def run(doc, client=None, ledger=None, cache=None, page_numbers=None,
        progress=None, should_stop=None, require_figure_caption=True,
        recovery_opts=None):
    """Convert one document. Returns what happened as well as what was produced."""
    report = _reporter(progress)
    result = ReflowResult()

    report(Progress(stage="read", message="reading the PDF"))
    raw_pages = extract.read_pages(doc, page_numbers)
    result.fingerprint = extract.document_fingerprint(doc)

    report(Progress(stage="assess", message="looking at the text layer"))
    result.assessment = assess.assess_pages(raw_pages)

    # The source layer is chosen before any structure is built: pages that are
    # only pictures, or whose embedded layer is demonstrably damaged, are read
    # off the printed page by local OCR. Everything after this line sees exactly
    # one layer per page, native or recovered, with its provenance. The default
    # is best-effort; an explicit choice (task/UI) names its mode and stops on a
    # missing engine before paid work.
    opts = {"mode": "auto_if_available", "language": "eng", "dpi": 300}
    opts.update(recovery_opts or {})
    if opts.get("mode") != "off":
        report(Progress(stage="recover", message="recovering the source text"))
        result.recovery = source.recover(
            doc, raw_pages, result.fingerprint,
            progress=lambda done, total: report(Progress(
                stage="recover", page=done, pages=total,
                message="page %d of %d recognized" % (done, total))),
            should_stop=should_stop, **opts)
        raw_pages = result.recovery.pages

    report(Progress(stage="skeleton", message="measuring the page geometry"))
    outline = extract.outline(doc)
    style = skeleton.book_style(raw_pages,
                               outline if skeleton.outline_is_useful(outline) else None)
    result.style = style
    trusted = result.assessment.layer_is_trusted if result.assessment else True
    skeletons = [skeleton.page_skeleton(raw, style, layer_trusted=trusted)
                 for raw in raw_pages]

    report(Progress(stage="assemble", message="putting the text back together"))
    book = assemble.assemble(skeletons, style, raw_pages)
    result.book = book

    report(Progress(stage="route", message="deciding which pages need a model"))
    routes = route.route_pages(book, skeletons, result.assessment,
                               recovered=(result.recovery.recovered_pnos
                                          if result.recovery is not None else ()))
    result.routing = route.summarise(routes)
    result.routed = route.routed_pages(routes)
    why = {page.pno: list(page.reasons) for page in routes}

    for pno in sorted(book.pages):
        result.page_html[pno] = build_epub.page_fragment(book, pno, style)
        result.outcomes[pno] = PageOutcome(pno=pno,
                                           reasons=list(book.page_reasons(pno)))

    pages_total = len(book.pages)
    if client is None or not result.routed:
        report(Progress(stage="build", page=pages_total, pages=pages_total))
        return result

    # The ladder is the book's, not the sample's. Built from the levels the
    # deterministic pass happened to emit, it refuses a model answer that uses a
    # level this book really sets but these pages do not show -- and refusing costs
    # the whole page, not just the heading. Take what the book's type defines, the
    # rung below its last (where a run-in head set on the body's own leading lands,
    # which is the one level the deterministic pass can never emit), and whatever
    # was in fact emitted.
    ladder = sorted(set(style.levels)
                    | {style.level_for(style.body_size)}
                    | {el.level for el in book.elements
                       if el.kind == "h" and el.level}) or [1]

    refusals = 0
    for index, pno in enumerate(result.routed, start=1):
        if should_stop is not None and should_stop():
            result.stopped = "cancelled"
            break
        try:
            outcome = _edit_one_page(doc, book, pno, client, ledger, cache, result,
                                     ladder, require_figure_caption,
                                     hints=page_hints(book, pno, why.get(pno)),
                                     should_stop=should_stop)
        except CapExceeded as exc:
            log.info("reflow: %s", exc)
            result.stopped = "cost_cap"
            break
        except UncertainBilling as exc:
            # A dispatched request whose billing we cannot prove did not happen.
            # The bound stays held; the run stops here rather than spending under
            # an encumbered cap; the liability is reported, not written off.
            log.info("reflow: page %d billing is unresolved: %s", pno, exc)
            result.outcomes[pno] = _refused(book, pno, exc, ledger, client)
            result.gate_failures += 1
            result.stopped = "billing_uncertain"
            break
        except AttemptCancelled as exc:
            # Cancel landed between attempts: nothing new was dispatched. Anything
            # already dispatched stays held in the ledger as unresolved.
            log.info("reflow: page %d cancelled mid-call: %s", pno, exc)
            result.outcomes[pno] = _refused(book, pno, exc, ledger, client)
            result.stopped = "cancelled"
            break
        except ModelError as exc:
            # One page the provider would not answer is one page that keeps its
            # deterministic text. A book is hundreds of chances for that to happen
            # and letting the first one out of this loop would throw away every page
            # already paid for.
            log.info("reflow: page %d was not converted: %s", pno, exc)
            result.outcomes[pno] = _refused(book, pno, exc, ledger, client)
            result.gate_failures += 1
            if not isinstance(exc, UnusableAnswer):
                # Only a provider that will not talk to us counts towards the guard.
                # An answer we cannot use is evidence the service is up, so it does
                # not advance the count -- and it does not clear it either, because a
                # dying provider interleaved with unusable pages is still dying.
                refusals += 1
                if refusals >= MAX_CONSECUTIVE_REFUSALS:
                    result.stopped = "model_errors"
                    break
            continue
        refusals = 0
        result.outcomes[pno] = outcome
        result.pages_done += 1
        result.spend_usd = _spent(result, ledger)
        report(Progress(stage="model", page=index, pages=len(result.routed),
                        spend_usd=result.spend_usd,
                        message="page %d of %d reviewed" % (index, len(result.routed))))

    result.spend_usd = _spent(result, ledger)
    if ledger is not None:
        result.pending_usd = ledger.pending_usd()
    report(Progress(stage="build", page=pages_total, pages=pages_total,
                    spend_usd=result.spend_usd))
    return result


def _spent(result, ledger):
    """What the job has spent, counted once.

    Money is added up in exactly one place. The ledger is the copy that survives
    the process — it is what the cap reserves against and what a resumed job reads
    back — so when there is one, it is the answer and this is a view of it.

    The alternative, keeping a second running total here, is not merely redundant:
    rounding a running total at every page is lossy. Three pages at half a
    micro-dollar each round individually to nothing and sum to nothing, while the
    same three entries in the ledger sum to $0.000002 — and the completion check
    (G4) compares the two and fails the job. A conversion where every page passed
    every gate is then reported to the user as a failure over arithmetic.
    """
    if ledger is not None:
        return ledger.spent()
    return round(sum(outcome.cost_usd for outcome in result.outcomes.values()), 6)


def _refused(book, pno, exc, ledger, client):
    """A page the provider would not answer, recorded rather than swallowed.

    An answer this page cannot use was still an answer, and a provider that answers
    bills for the tokens it wrote: ``UnusableAnswer`` carries what the call cost, and
    a provider that refused outright carries nothing. Writing that down is not
    bookkeeping -- ``Ledger.spent()`` is the number the cap reserves against and the
    number the reader is shown, so a real charge recorded here as $0.00 is money the
    reader's ceiling cannot stop and money the finished job does not admit to.

    The one other shape is ``UncertainBilling``: a request dispatched whose billing
    we cannot prove either way. Its ``cost_usd`` is None -- never a possible charge
    written as $0.00 -- and the held bound travels with the entry.
    """
    cost = getattr(exc, "cost_usd", 0.0)
    outcome = PageOutcome(pno=pno, reasons=list(book.page_reasons(pno)),
                          model=getattr(client, "model_id", ""),
                          gate="FAIL",
                          cost_usd=float(cost) if cost is not None else 0.0)
    if isinstance(exc, UncertainBilling):
        outcome.gate_reasons = ["the provider's answer was lost after dispatch; up "
                                "to $%.4f may still be billed and is held as "
                                "unresolved" % exc.held_usd]
    else:
        outcome.gate_reasons = ["the model could not answer: %s" % exc]
    if ledger is not None:
        entry = {"kind": "page", "page": pno,
                 "cost_usd": round(cost, 6) if cost is not None else None,
                 "cached": False, "gate": "FAIL", "model": outcome.model,
                 "reasons": outcome.reasons,
                 "gate_reasons": outcome.gate_reasons,
                 "error": str(exc)}
        if cost is None:
            entry["billing"] = "unresolved"
            entry["held_usd"] = round(float(getattr(exc, "held_usd", 0.0) or 0.0), 6)
        if getattr(exc, "attempt", None):
            # The reservation's identity, so a reconciled debit and this record
            # are one charge counted once -- even across a crash between them.
            entry["attempt"] = exc.attempt
        if getattr(exc, "cost_source", ""):
            entry["cost_source"] = exc.cost_source
        for key in ("prompt_tokens", "completion_tokens"):
            tokens = int(getattr(exc, key, 0) or 0)
            if tokens:
                entry[key] = tokens
        ledger.record(entry)
    return outcome


def page_hints(book, pno, reasons=None):
    """Why this page is being paid for, in words the model can act on.

    A page arrives at the model because something about it could not be settled
    deterministically. Sending it without saying what asks the model to re-mark a
    page that already looks finished, and the thing we are paying to have looked at
    is the thing it has no reason to look at. The unmarked notes are named because
    those numbers are the only ones the gate will accept back (G1), and the repaired
    note numbers are named because the model is reading the same small print the
    scanner misread and will otherwise contradict the text it was sent.
    """
    hints = [route.PAGE_REASONS[reason] for reason in (reasons or [])
             if reason in route.PAGE_REASONS]
    unmarked = book.unmarked_notes(pno) if book is not None else []
    if unmarked:
        hints.append(
            "notes %s are printed on this page and nothing in the text points at "
            "them: their superscripts are legible in the image but the text layer "
            "lost them, in some places leaving a stray quotation mark, an "
            "apostrophe or a run of nonsense where the number belongs. Put each "
            "one back as a noteref where the image shows it, and use no other "
            "number. You may delete one or two quotation marks or apostrophes "
            "standing exactly where the number belongs; every other character, "
            "letters and digits included, stays exactly as you were given it with "
            "the noteref straight after it."
            % ", ".join(str(number) for number in unmarked))
    swept = book.swept_notes(pno) if book is not None else []
    for number in swept:
        hints.append(
            "the page also prints note %d under the rule and the text layer lost it "
            "whole: its number is gone and its text reads on from the end of note "
            "%d. Cut note %d where the image shows it starting, open it with %d in "
            "place of whatever the scan left there, and move no words between the "
            "two." % (number, number - 1, number, number))
    for returned, kept in (book.renumbered_notes(pno) if book is not None else []):
        hints.append(
            "the scan read note %d's own number as %s, and the numbering either side "
            "of it settles it at %d -- which is what the text you have been given "
            "carries. A superscript that small reads either way in the image: keep "
            "%d, in the note and in the marker that points at it."
            % (kept, returned, kept, kept))
    return hints


def _edit_one_page(doc, book, pno, client, ledger, cache, result, ladder,
                   require_figure_caption, hints=None, should_stop=None):
    outcome = PageOutcome(pno=pno, reasons=list(book.page_reasons(pno)),
                          model=getattr(client, "model_id", ""))
    source_text = assemble.page_source_text(book, pno)

    # A page's words alone do not identify the question: its measured headings,
    # note hints and image crop can change while every word stays the same.
    headings = assemble.page_headings(book, pno)
    request_model = outcome.model
    context = {"headings": headings, "hints": hints, "ladder": ladder,
               "crop": book.page_box(pno),
               "raster": [RASTER_SCALE, RASTER_QUALITY, RASTER_MAX_BYTES]}

    cached = (cache.get(result.fingerprint, pno, request_model, source_text, context)
              if cache else None)
    if cached is not None:
        outcome.cached = True
        outcome.model = cached.get("model") or request_model
        result.reused += 1
        _adopt(result, book, pno, outcome, cached.get("html") or "",
               cached.get("uncertain") or [], ladder, require_figure_caption,
               ledger=ledger, cost=0.0, cached=True)
        return outcome

    image = _raster(doc, pno, book.page_box(pno))
    answer = client.edit_page(source_text, image_jpeg=image, ladder=ladder,
                              hints=hints, page_label=str(pno + 1), ledger=ledger,
                              headings=headings, should_stop=should_stop)
    outcome.cost_usd = float(getattr(answer, "cost_usd", 0.0) or 0.0)
    outcome.model = getattr(answer, "model", outcome.model)

    _adopt(result, book, pno, outcome, answer.html, answer.uncertain, ladder,
           require_figure_caption, ledger=ledger, cost=outcome.cost_usd,
           answer=answer)

    if cache is not None and outcome.gate == "PASS":
        cache.put(result.fingerprint, pno, request_model, source_text,
                  {"html": answer.html, "uncertain": list(answer.uncertain),
                   "model": outcome.model, "prompt_version": prompts.PROMPT_VERSION},
                  context=context)
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
    # The notes this page prints and never points at. They are the only numbers the
    # model is allowed to conjure out of the scan, and only in place of the
    # punctuation the scanner left behind — see gate._marker_recovery.
    # ... plus the numbers the page printed and the text layer never returned at
    # all, which are missing from that list for the same reason they are missing
    # from the page: there is no note left to be unmarked (see Book.swept_notes).
    recoverable = book.unmarked_notes(pno) + book.swept_notes(pno)
    words = gate.check_word_preservation(source_text, html,
                                         recoverable_markers=recoverable)
    structure = gate.check_structure(html, ladder=ladder,
                                     require_figure_caption=require_figure_caption,
                                     figures_expected=figures,
                                     headings=assemble.page_headings(book, pno))

    outcome.uncertain = list(uncertain or [])
    outcome.recovered_markers = list(words.recovered_markers)
    if words.ok and structure.ok:
        outcome.gate = "PASS"
        outcome.source = "model"
        # R3, after the gate and not before it: the gate judges what the model
        # said, and the mark is ours. It is placed only where it cannot change a
        # word (see annotate.mark_uncertain), so a gate re-run on this page would
        # reach the same verdict.
        html, outcome.uncertain, outcome.marked = annotate.annotate_page(
            html, outcome.uncertain, recovered=outcome.recovered_markers)
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
                 "uncertain": len(outcome.uncertain), "marked": outcome.marked}
        if outcome.recovered_markers:
            entry["recovered_markers"] = list(outcome.recovered_markers)
        if answer is not None:
            entry["provider"] = getattr(answer, "provider", "")
            entry["prompt_tokens"] = answer.prompt_tokens
            entry["completion_tokens"] = answer.completion_tokens
            entry["attempts"] = answer.attempts
            entry["cost_source"] = answer.cost_source
            if getattr(answer, "attempt", None):
                # The reservation this answer reconciled: one charge, counted once,
                # durable even if the crash lands between reconcile and this record.
                entry["attempt"] = answer.attempt
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


def _raster(doc, pno, clip=None):
    try:
        return extract.render_page_jpeg(doc, pno, scale=RASTER_SCALE,
                                        quality=RASTER_QUALITY,
                                        max_bytes=RASTER_MAX_BYTES, clip=clip)
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
