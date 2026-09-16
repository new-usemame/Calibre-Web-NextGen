# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stage 6: what the book says about how it was made.

A conversion nobody can audit is one the reader has to take on trust, and this one
used a language model. So the page is written to be true in the directions that do
not flatter it: it names the pages the gate refused, it says when no model ran
rather than reporting a clean sweep, and it says how much of the book a stopped run
never reached.

``numbers()`` is the single source of both the page and the JSON twin, and
``check_completion()`` (SPEC §6 G4) refuses to let either ship while they disagree
with the ledger. A report that can drift from its own evidence reads exactly like
one that cannot, which is what makes the drift worth a gate.
"""

import re
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from .annotate import uncertain_readings
from .build_epub import CONVERTER, CONVERTER_VERSION

#: Hundreds of uncertain readings on a bad scan is normal. A page that prints all of
#: them is a page nobody reads; the total is the number that means something.
MAX_UNCERTAIN_LISTED = 10
MAX_FONTS_LISTED = 6
MAX_REPAIRS_LISTED = 8

STATEMENT = "No wording was changed by this conversion."

_TABLE = re.compile(r"<table\b", re.I)
_BLOCKQUOTE = re.compile(r"<blockquote\b", re.I)

STOP_REASONS = {
    "cost_cap": "The conversion stopped when it reached its cost cap.",
    "cancelled": "The conversion was cancelled before every page was reviewed.",
    "not_configured": "No model was configured, so no page was reviewed.",
    "model_errors": "The conversion stopped because the model service refused "
                    "several pages in a row. The pages already reviewed are in "
                    "this book; the rest keep the text read from the PDF.",
    "billing_uncertain": "The conversion stopped because a model request's answer "
                         "was lost in transit and what it billed could not be "
                         "confirmed. The pages already reviewed are in this book; "
                         "the possibly owed amount is held as unresolved.",
}


def numbers(result, ledger=None, client=None):
    """Everything the about page says, as data. The JSON twin is this, verbatim."""
    book = result.book
    outcomes = list(result.outcomes.values())
    adopted = [o for o in outcomes if o.source == "model"]
    refused = [o for o in outcomes if o.gate in ("FAIL", "NOT_APPLICABLE")]
    answered = [o for o in outcomes if o.gate]
    uncertain = [dict(span, page=span.get("page", o.pno))
                 for o in outcomes for span in (o.uncertain or [])]

    described = client.describe() if client is not None and hasattr(client, "describe") \
        else {}
    model_id = described.get("model") or next((o.model for o in answered if o.model), "")

    markup = "\n".join(result.page_html.values())
    stats = dict(book.stats or {}) if book is not None else {}
    totals = ledger.totals() if ledger is not None else {}
    # ``notes_unmarked`` is counted while the book is assembled, before any page has
    # been shown to a model. Every marker the model then read back off the scan is
    # one fewer orphaned footnote in the finished book, and saying otherwise sends
    # the reader looking for a problem that is not there any more.
    # Counted per page, never book-wide: a book numbers its notes from 1 again
    # every chapter, so "note 3 came back" is only ever a statement about the page
    # it came back on, and a set of numbers counts one repair for every page that
    # happened to lose the same one.
    repaired = sorted({(o.pno, number)
                       for o in outcomes for number in (o.recovered_markers or [])})
    recovered = sorted({number for _, number in repaired})
    # The same correction for the other footnote loss, and the reason the two
    # cannot share a total: a swept note was never in the book to be unmarked.
    swept = {o.pno: set(book.swept_notes(o.pno)) for o in outcomes} if book is not None else {}
    swept_back = sum(1 for pno, number in repaired if number in swept.get(pno, ()))
    unmarked_back = len(repaired) - swept_back

    payload = {
        "converter": CONVERTER,
        "converter_version": CONVERTER_VERSION,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "source": _source(result),
        "source_recovery": _recovery(result),
        "fidelity": {
            "pages": len(result.page_html),
            "pages_deterministic": len(result.page_html) - len(adopted),
            "pages_model": len(adopted),
            "pages_routed": len(result.routed),
            "pages_reviewed": len(answered),
            "pages_routed_not_reviewed": max(0, len(result.routed) - len(answered)),
            "uncertain_total": len(uncertain),
            # How many of those a reader will actually find highlighted. A reading
            # can only be marked where the conversion can point at the word it
            # names, so this is the honest half of the count above.
            "uncertain_marked": sum(o.marked for o in outcomes),
            "uncertain": uncertain[:MAX_UNCERTAIN_LISTED],
            "conservation": (book.conservation.to_dict()
                             if book is not None and book.conservation else None),
        },
        "model": {
            "used": bool(answered),
            "model": model_id,
            "tier": described.get("tier", ""),
            "prompt_version": described.get("prompt_version", ""),
            "structure_only": True,
            "pages_sent": len(answered),
            "pages_adopted": len(adopted),
            "pages_refused": len(refused),
            "pages_reused": sum(1 for o in answered if o.cached),
        },
        "structure": {
            "headings": stats.get("headings", 0),
            "footnotes": stats.get("notes", 0),
            "footnotes_unmarked": max(0, stats.get("notes_unmarked", 0) - unmarked_back),
            "footnotes_unmarked_before_review": stats.get("notes_unmarked", 0),
            "footnotes_swept": max(0, stats.get("notes_swept", 0) - swept_back),
            "footnotes_swept_before_review": stats.get("notes_swept", 0),
            "footnotes_swept_restored": swept_back,
            "markers_recovered": len(repaired),
            "markers_recovered_notes": recovered[:MAX_REPAIRS_LISTED],
            "markers_recovered_notes_total": len(recovered),
            "markers_unresolved": stats.get("markers_unresolved", 0),
            "figures": stats.get("figures", 0),
            "figures_recovered": sum(1 for figure in (book.figures or [])
                                     if figure.get("found") not in (None, "embedded"))
            if book is not None else 0,
            "artwork_words": stats.get("artwork_words", 0),
            "tables": len(_TABLE.findall(markup)),
            "blockquotes": len(_BLOCKQUOTE.findall(markup)),
            "page_joins": stats.get("page_joins", 0),
            "page_joins_refused": stats.get("page_joins_refused", 0),
            "repairs": stats.get("repairs", 0),
            "repairs_listed": [r.to_dict() for r in (book.repairs or [])[:MAX_REPAIRS_LISTED]]
            if book is not None else [],
        },
        "routing": dict(result.routing or {}),
        "spend": {
            "usd": round(result.spend_usd, 6),
            # Confirmed spend and held amounts are different things and stay
            # separate everywhere they are shown: pending is the strict bound of
            # dispatched requests whose billing is unresolved.
            "pending_usd": totals.get("pending_usd", 0.0),
            "unresolved_attempts": totals.get("unresolved_attempts", 0),
            "cap_usd": totals.get("cap_usd"),
            "calls": totals.get("calls", 0),
            "reused": totals.get("reused", 0),
            "prompt_tokens": totals.get("prompt_tokens", 0),
            "completion_tokens": totals.get("completion_tokens", 0),
            "models": totals.get("models", {}),
        },
        "stopped": result.stopped,
        "statement": STATEMENT,
    }
    payload["unplaced"] = _unplaced(payload, result)
    return payload


def _recovery(result):
    """The source-layer story for the report and the sidecar, in plain counts.

    Pages the PDF could not answer for itself were read off the printed page by
    local OCR: that is a different kind of fidelity from the native layer's
    word-identity, and the two are never summed into one number. An OCR page's
    honesty is its engine, its language, its orientation fixes and its uncertain
    readings, with crops named as evidence — not a conservation claim.
    """
    recovery = getattr(result, "recovery", None)
    if recovery is None:
        return {"performed": False}
    pages_ocr = [prov for _, prov in sorted(recovery.provenance.items())
                 if prov.layer == "ocr" and prov.words]
    engines = sorted({prov.engine for prov in pages_ocr if prov.engine})
    languages = sorted({prov.language for prov in pages_ocr if prov.language})
    return {
        "performed": True,
        "pages_ocr": len(pages_ocr),
        "pages_recovered_for_damage": sum(
            1 for prov in pages_ocr if prov.reason == "damaged_layer"),
        "pages_recovered_image_only": sum(
            1 for prov in pages_ocr if prov.reason == "image_only"),
        "pages_failed": recovery.failed,
        "pages_reused": recovery.reused,
        "attempted": recovery.attempted,
        "words": recovery.ocr_words,
        "uncertain_words": recovery.uncertain_words,
        "engine": engines[0] if len(engines) == 1 else engines,
        "language": languages[0] if len(languages) == 1 else languages,
        "engine_unavailable": recovery.engine_unavailable,
        "seconds": round(recovery.seconds, 2),
        # The per-page record: which pages are not the PDF's own layer, and
        # why -- the claim a global verdict can never make truthfully (book
        # 567's cover is OCR at index 0 and native everywhere it matters).
        "pages_detail": {str(pno): prov.to_dict()
                         for pno, prov in sorted(recovery.provenance.items())
                         if prov.layer != "native" or prov.failed},
    }


def _source(result):
    assessment = result.assessment
    if assessment is None:
        return {"pages": len(result.page_html), "verdict": "", "verdict_plain": "",
                "fonts": {}}
    fonts = sorted((assessment.fonts or {}).items(), key=lambda kv: -kv[1])
    return {
        "pages": assessment.pages,
        "verdict": assessment.verdict,
        "verdict_plain": assessment.describe(),
        "layer_is_trusted": assessment.layer_is_trusted,
        "median_chars": round(assessment.median_chars, 1),
        "prose_share": round(assessment.prose_share, 4),
        "scan_share": round(assessment.scan_share, 4),
        "drawings": assessment.drawings_total,
        "fonts": dict(fonts[:MAX_FONTS_LISTED]),
    }


def _count(count, one, many):
    """One of a thing, said as one of a thing.

    This page is asking a reader to believe a set of numbers about their own book.
    "1 footnotes are printed in the book" is how they work out that nobody read the
    page before it shipped, and a reader who stops believing this page has no other
    way to audit the conversion. So each sentence carries both of its forms.
    """
    return one if count == 1 else many % count


def _unplaced(payload, result):
    """What the conversion could not do, in the reader's terms rather than ours."""
    out = []
    structure = payload["structure"]
    if structure["footnotes_unmarked"]:
        out.append(_count(
            structure["footnotes_unmarked"],
            "One footnote is printed in the book but no marker for it was found in "
            "the text; it is kept with the page it was printed on.",
            "%d footnotes are printed in the book but no marker for them was found "
            "in the text; they are kept with the page they were printed on."))
    if structure.get("footnotes_swept"):
        out.append(_count(
            structure["footnotes_swept"],
            "One footnote lost its own printed number to the scan and is still "
            "joined to the note printed above it; every word of both is in the "
            "book, set as one note rather than two.",
            "%d footnotes lost their own printed number to the scan and are still "
            "joined to the note printed above them; every word of both is in the "
            "book, set as one note rather than two."))
    if structure.get("markers_unresolved"):
        out.append(_count(
            structure["markers_unresolved"],
            "One footnote marker in the text points at a note that was not printed "
            "on its page; it is kept as a printed superscript rather than made into "
            "a link that opens nothing.",
            "%d footnote markers in the text point at a note that was not printed "
            "on their page; they are kept as printed superscripts rather than made "
            "into links that open nothing."))
    if structure["page_joins_refused"]:
        out.append(_count(
            structure["page_joins_refused"],
            "One paragraph that may run over a page turn was left as two paragraphs "
            "rather than joined on a guess.",
            "%d paragraphs that may run over a page turn were left as two "
            "paragraphs rather than joined on a guess."))
    columns = (payload["routing"].get("reasons") or {}).get("multi_column")
    if columns:
        out.append(_count(
            columns,
            "One page looks set in columns. The text is kept in the order the page "
            "stored it and is not re-ordered into column order.",
            "%d pages look set in columns. The text is kept in the order the page "
            "stored it and is not re-ordered into column order."))
    conservation = payload["fidelity"]["conservation"]
    if conservation and not conservation.get("ok"):
        out.append(_count(
            len(conservation.get("missing") or []),
            "One word of the source did not reach the finished book.",
            "%d words of the source did not reach the finished book."))
    return out


# ------------------------------------------------------------------------- G4

def check_completion(payload, ledger):
    """G4: the report's numbers are the ledger's numbers, or nothing ships."""
    problems = []
    if ledger is None:
        return problems
    totals = ledger.totals()
    gate = totals.get("gate") or {}
    model = payload.get("model") or {}
    spend = payload.get("spend") or {}

    checks = [
        ("pages sent", model.get("pages_sent"), totals.get("pages")),
        ("pages adopted", model.get("pages_adopted"), gate.get("PASS", 0)),
        ("pages refused", model.get("pages_refused"),
         gate.get("FAIL", 0) + gate.get("NOT_APPLICABLE", 0)),
    ]
    for label, reported, recorded in checks:
        if reported != recorded:
            problems.append("%s: the report says %s and the ledger says %s"
                            % (label, reported, recorded))
    if round(float(spend.get("usd") or 0.0), 6) != round(totals.get("spend_usd", 0.0), 6):
        problems.append("spend: the report says %s and the ledger says %s"
                        % (spend.get("usd"), totals.get("spend_usd")))
    return problems


# -------------------------------------------------------------------- the page

def about_page(payload, show_cost=False, links=None, losses=()):
    """The XHTML body of ``reflow-about.xhtml``, in plain language.

    ``losses`` are the things only the EPUB builder knows it could not place; they
    join the rest of what the conversion could not do rather than forming a second
    list of bad news somewhere else on the page.
    """
    links = links or {}
    out = ["<h1>About this conversion</h1>",
           "<p>This book was made from a PDF by %s %s on %s. %s</p>"
           % (escape(CONVERTER), escape(CONVERTER_VERSION),
              escape(payload.get("generated", "")), escape(STATEMENT))]

    out.append("<h2>The PDF this came from</h2>")
    source = payload["source"]
    out.append("<p>%s. %s</p>"
               % (_count(source.get("pages", 0), "One page", "%d pages"),
                  escape(source.get("verdict_plain", ""))))
    if source.get("fonts"):
        out.append("<p>Type seen on the page: %s.</p>"
                   % escape(", ".join(sorted(source["fonts"]))))
    out.extend(_recovery_section(payload))

    out.extend(_fidelity_section(payload))
    out.extend(_structure_section(payload))
    unplaced = list(payload.get("unplaced") or []) + list(losses or ())
    if unplaced:
        out.append("<h2>What could not be placed</h2><ul>%s</ul>"
                   % "".join("<li>%s</li>" % escape(item) for item in unplaced))
    out.extend(_uncertain_section(payload, links))
    if show_cost:
        out.extend(_spend_section(payload))
    return "\n".join(out)


def _recovery_section(payload):
    """The source-layer story, told straight: which pages are the PDF's own text
    and which were read off the printed page by a local engine, with the honest
    limits of the second kind."""
    recovery = payload.get("source_recovery") or {}
    if not recovery.get("performed"):
        return []
    pages = recovery.get("pages_ocr", 0)
    failed = recovery.get("pages_failed", 0)
    out = ["<h2>Source text recovery</h2>"]
    if recovery.get("engine_unavailable"):
        out.append("<p>The local text recognition engine or its language data is "
                   "not installed on this server, so pages that are only pictures "
                   "kept their images and any damaged layer was left as printed. "
                   "They are facsimiles, not reflowed text.</p>")
        return out
    if pages:
        damaged = recovery.get("pages_recovered_for_damage", 0)
        image_only = recovery.get("pages_recovered_image_only", 0)
        parts = []
        if image_only:
            parts.append(_count(image_only,
                                "one was only a picture of a page",
                                "%d were only pictures of pages"))
        if damaged:
            parts.append(_count(damaged,
                                "one had a damaged text layer",
                                "%d had a damaged text layer"))
        out.append(
            "<p>%s, and read off the printed page with local text recognition "
            "(%s, %s). Those words are a transcription of the scan, not the PDF's "
            "own text layer: reading order and structure were rebuilt from it, but "
            "no sentence was rewritten. Uncertain readings are marked where the "
            "engine was unsure.</p>"
            % (_count(pages, "One page", "%d pages had no usable text")
               + (" (%s)" % " and ".join(parts) if parts else ""),
               escape(str(recovery.get("engine", ""))),
               escape(str(recovery.get("language", "")))))
        if recovery.get("uncertain_words"):
            out.append("<p>%s.</p>" % _count(
                recovery["uncertain_words"],
                "One word was read with low confidence by the engine",
                "%d words were read with low confidence by the engine"))
    if failed:
        out.append("<p>%s; those pages were kept as they printed.</p>" % _count(
            failed,
            "One page could not be recognized",
            "%d pages could not be recognized"))
    return out


def _fidelity_section(payload):
    fidelity = payload["fidelity"]
    model = payload["model"]
    out = ["<h2>How faithful this is</h2>"]

    if not model["used"]:
        out.append("<p>No model was used: every page here is the converter's own "
                   "reading of the PDF, and no page was reviewed by a model.</p>")
    else:
        sent, kept, refused = (model["pages_sent"], model["pages_adopted"],
                               model["pages_refused"])
        if sent == 1:
            asked = ("One of the %d pages was sent to a model to have its structure "
                     "read." % fidelity["pages"])
        else:
            asked = ("%d of the %d pages were sent to a model to have their "
                     "structure read." % (sent, fidelity["pages"]))
        if not kept:
            used = "None came back word-for-word identical to the page"
        else:
            used = _count(kept,
                          "One came back word-for-word identical to the page and "
                          "was used",
                          "%d came back word-for-word identical to the page and "
                          "were used")
        if not refused:
            thrown = "none were refused by the word-preservation check"
        else:
            thrown = _count(refused,
                            "one was refused by the word-preservation check and "
                            "the converter's own reading was kept instead",
                            "%d were refused by the word-preservation check and "
                            "the converter's own reading was kept instead")
        out.append("<p>%s %s; %s.</p>" % (asked, used, thrown))
        out.append("<p>The model was asked only to mark up structure. Every answer "
                   "is compared with the page word by word, and any answer that adds, "
                   "drops or re-capitalises a word is thrown away.</p>")

    if fidelity["pages_routed_not_reviewed"]:
        out.append("<p>%s %s</p>"
                   % (_count(fidelity["pages_routed_not_reviewed"],
                             "One page the converter wanted a second opinion on was "
                             "never reviewed.",
                             "%d pages the converter wanted a second opinion on were "
                             "never reviewed."),
                      escape(STOP_REASONS.get(payload.get("stopped") or "",
                                              "The run ended first."))))
    conservation = fidelity.get("conservation")
    if conservation:
        out.append("<p>Word check: %s of %s words of the PDF's text are in this book.</p>"
                   % (conservation.get("output_total"), conservation.get("source_total")))
    return out


def _structure_section(payload):
    structure = payload["structure"]
    rows = [("Headings", structure["headings"]),
            ("Footnotes", structure["footnotes"]),
            ("Figures", structure["figures"]),
            ("Tables", structure["tables"]),
            ("Block quotations", structure["blockquotes"]),
            ("Paragraphs rejoined across a page turn", structure["page_joins"]),
            ("Damaged footnote numbers read from the page",
             structure["repairs"] + structure.get("markers_recovered", 0))]
    if structure.get("figures_recovered"):
        rows.append(("Figures cropped from the scanned pages themselves",
                     structure["figures_recovered"]))
    if structure.get("artwork_words"):
        # Lettering the text layer read off a chart travels with the chart's crop.
        # The word check counts it there, and this row says so, or a reader
        # comparing counts is left to find the difference on their own.
        rows.append(("Words kept with the artwork they were printed on",
                     structure["artwork_words"]))
    if structure.get("markers_recovered"):
        rows.append((_repaired_markers_label(structure), structure["markers_recovered"]))
    body = "".join("<tr><td>%s</td><td>%s</td></tr>" % (escape(label), value)
                   for label, value in rows)
    return ["<h2>What was recovered</h2>",
            "<table><tbody>%s</tbody></table>" % body]


def _repaired_markers_label(structure):
    """Name the notes whose marker came back, and never imply a trimmed list is all
    of them: a row reading "notes 3, 5, 6, 7, 8, 10, 11, 12" beside a count of 73 is
    a contradiction the reader is left to resolve."""
    label = "Footnote markers the scan destroyed and the model read back"
    listed = list(structure.get("markers_recovered_notes") or [])
    if not listed:
        return label
    distinct = int(structure.get("markers_recovered_notes_total") or len(listed))
    named = ", ".join(str(n) for n in listed)
    rest = distinct - len(listed)
    if rest > 0:
        return "%s (notes %s and %d more)" % (label, named, rest)
    return "%s (notes %s)" % (label, named)


def _uncertain_section(payload, links):
    fidelity = payload["fidelity"]
    total = fidelity["uncertain_total"]
    if not total:
        return []
    listed = fidelity["uncertain"]
    marked = fidelity.get("uncertain_marked") or 0
    clauses = []
    if marked:
        clauses.append("it is highlighted in the text" if total == 1
                       else "all of them are highlighted in the text" if marked == total
                       else "%d of them %s highlighted in the text"
                       % (marked, "is" if marked == 1 else "are"))
    if total > len(listed):
        clauses.append("the first %d are listed here" % len(listed))
    out = ["<h2>Readings worth checking</h2>",
           "<p>%s marked uncertain%s.</p>"
           % ("One place was" if total == 1 else "%d places were" % total,
              "".join("; " + clause for clause in clauses))]
    items = []
    for span in listed:
        token, readings = uncertain_readings(span)
        page = span.get("page")
        href = links.get(page)
        label = "page %s" % ((page or 0) + 1)
        where = ('<a href="%s#pg_%04d">%s</a>' % (escape(href), int(page), label)
                 if href is not None and page is not None else label)
        said = escape(token) if token else "<em>an unnamed reading</em>"
        likely = (" &#8212; likely %s" % escape(readings[0])) if readings else ""
        also = (" (also read as %s)" % escape(", ".join(readings[1:]))
                if len(readings) > 1 else "")
        items.append("<li>%s: %s%s%s</li>" % (where, said, likely, also))
    out.append("<ul>%s</ul>" % "".join(items))
    return out


def _spend_section(payload):
    spend = payload["spend"]
    out = ["<h2>What this cost</h2>",
           "<p>$%.4f in model calls over %s.</p>"
           % (spend["usd"], _count(spend["calls"], "one page", "%d pages"))]
    pending = float(spend.get("pending_usd") or 0.0)
    if pending:
        out.append("<p>Up to $%.4f more is unresolved: %s had answers lost in "
                   "transit and may still be billed. That amount is not confirmed "
                   "and is not included in the figure above; check the provider's "
                   "dashboard before re-running.</p>"
                   % (pending, _count(spend.get("unresolved_attempts") or 1,
                                      "one request", "%d requests")))
    if payload["model"]["model"]:
        out.append("<p>Model: %s. Prompt version: %s.</p>"
                   % (escape(payload["model"]["model"]),
                      escape(payload["model"]["prompt_version"] or "n/a")))
    return out
