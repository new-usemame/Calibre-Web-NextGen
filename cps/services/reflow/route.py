# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stage 4: which pages are worth paying a model to look at, and why.

This is the module that decides the dollar figure in the user's consent checkbox.
A rule that fires on every page turns a $0.15 conversion into a $1.50 one; a rule
that never fires ships the defects the deterministic pass could not resolve. So
every routing decision carries its reasons, they go into the ledger, and the
estimate is the count of pages this module actually chose — never a fixed share.
"""

from dataclasses import dataclass, field
from typing import List

from . import model

#: A page-boundary join is a decision about two pages. Showing the model only one of
#: them asks it to judge a sentence whose other half it cannot see.
BOUNDARY_WINDOW = 1

#: Reasons that are about a specific page.
PAGE_REASONS = {
    "no_text_layer": "the page has no text to work from",
    "empty_page": "the page produced nothing at all",
    "multi_column": "the page looks set in columns and Reflow does not reorder columns",
    "unresolved_marker": "a footnote marker on the page matches no note on it",
    "note_marker_mismatch": "the page's notes and markers do not add up",
    "duplicate_note_numbers": "the page prints the same note number twice",
    "residue_markers_paired": "a marker was recovered from punctuation and should be confirmed",
    "run_in_candidate_rejected": "a bold line might be a heading and might be a lead-in",
    "large_type_not_a_heading": "large type on the page did not read as a heading",
    "figure_without_caption": "a figure on the page has no caption to place it by",
    "page_boundary_join_uncertain": "a paragraph may or may not carry over the page turn",
}

#: Reasons that pull the neighbouring page in with them.
BOUNDARY_REASONS = ("page_boundary_join_uncertain",)


@dataclass
class PageRoute(object):
    pno: int
    routed: bool = False
    reasons: List[str] = field(default_factory=list)
    needs_image: bool = True

    def to_dict(self):
        return {"pno": self.pno, "routed": self.routed, "reasons": self.reasons,
                "needs_image": self.needs_image,
                "why": [PAGE_REASONS.get(r, r) for r in self.reasons]}


def route_pages(book, skeletons, assessment=None, window=BOUNDARY_WINDOW):
    """One decision per page, with its reasons, in page order."""
    routes = [PageRoute(pno=skel.pno) for skel in skeletons]
    by_pno = {r.pno: r for r in routes}

    for skel in skeletons:
        target = by_pno[skel.pno]
        reasons = set(book.page_reasons(skel.pno)) | set(skel.reasons)

        if assessment is not None and assessment.verdict in ("NO_TEXT_LAYER", "GARBAGE_TEXT"):
            reasons.add("no_text_layer")

        for reason in sorted(reasons):
            if reason not in PAGE_REASONS:
                continue
            target.reasons.append(reason)
            target.routed = True
            if reason in BOUNDARY_REASONS:
                _pull_in_neighbours(by_pno, skel.pno, reason, window)

    for target in routes:
        target.reasons = sorted(set(target.reasons))
        # A page with a text layer is cheaper and more accurate to send as text plus
        # its raster; a page without one has nothing but the raster.
        target.needs_image = True
    return routes


def _pull_in_neighbours(by_pno, pno, reason, window):
    for offset in range(-window, window + 1):
        if offset == 0:
            continue
        neighbour = by_pno.get(pno + offset)
        if neighbour is None:
            continue
        neighbour.routed = True
        if reason not in neighbour.reasons:
            neighbour.reasons.append(reason)


def routed_pages(routes):
    return [r.pno for r in routes if r.routed]


def estimate(routed_count, pages, tiers=None):
    """SPEC §5: routed pages times the measured price per page, per tier.

    The worst case — every page routed — is quoted alongside, because a user about
    to authorise a spend is entitled to the ceiling and not only the expectation.
    """
    tiers = tiers or model.TIERS
    quote = {"routed_pages": int(routed_count), "pages": int(pages),
             "priced_on": model.PRICE_TABLE_MEASURED,
             "worst_case": {}}
    for name, spec in tiers.items():
        quote[name] = round(spec.price_per_page * routed_count, 4)
        quote["worst_case"][name] = round(spec.price_per_page * pages, 4)
    return quote


def summarise(routes):
    """Counts per reason, for the ledger and the 'About this conversion' page."""
    counts = {}
    for target in routes:
        for reason in target.reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return {"pages": len(routes), "routed": len(routed_pages(routes)),
            "reasons": dict(sorted(counts.items(), key=lambda kv: -kv[1]))}
