# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Reading order in columns.

A confirmed readiness defect lives here, measured on a real synthetic two-column
probe (state/pdf2epub-ai/readiness-20260915/columns-probe.pdf): the page came
back with left and right lines interleaved row by row, because a MuPDF block can
hold same-baseline lines from both columns and the final region sort ordered
everything by (y, x).

Every test pairs the defect shape with a control that must NOT change: single
columns and ruled tables keep their order.
"""

import pytest

from cps.services.reflow import assemble
from tests.fixtures import reflow_pdfs as F

pytestmark = pytest.mark.unit


def _book(*builders):
    doc = F.new_doc()
    for build in builders:
        build(doc)
    try:
        return assemble.deterministic_book(doc)
    finally:
        doc.close()


def _whole_text(book):
    return " ".join(el.text for el in book.elements)


class TestColumnReadingOrder(object):
    """Two and three prose columns read top-to-bottom, then left-to-right."""

    def test_two_columns_are_not_interleaved(self):
        """The readiness probe: same-baseline lines inside one MuPDF block must
        still read all-left then all-right, with every word preserved."""
        book = _book(F.two_column_page)

        left = " ".join(line for line, _ in F.TWO_COLUMN_ROWS)
        right = " ".join(line for _, line in F.TWO_COLUMN_ROWS)
        assert _whole_text(book) == left + " " + right, _whole_text(book)
        assert book.conservation.ok, book.conservation.to_dict()

    def test_three_columns_read_left_to_right(self):
        book = _book(F.three_column_page)

        text = _whole_text(book)
        order = [text.index(word) for word in
                 ("Alpha", "continues", "Beta", "second.", "Gamma", "third.")]
        assert order == sorted(order), text

    def test_a_spanning_heading_stays_between_its_bands(self):
        """A full-width heading between column bands belongs between the bands --
        not absorbed into a column and not floated to the top of one."""
        book = _book(F.column_bands_page)

        text = _whole_text(book)
        day = text.index("Day charts are read first")
        leads = text.index("The sect light leads the day")
        night = text.index("Night charts are read second")
        leaves = text.index("The sect light leaves at night")
        mid = text.index("How the Remaining Planets Behave")
        saturn = text.index("Saturn behaves by day then")
        jupiter = text.index("Jupiter witnesses the day")
        mars = text.index("Mars behaves by night then")
        assert day < leads < night < leaves, text
        assert night < mid < saturn < jupiter < mars, text

    def test_a_paragraph_crossing_the_column_break_is_one_paragraph(self):
        """Column order is a reading order, not a chopping rule: the sentence that
        flows off the bottom of the left column continues at the top of the right."""
        book = _book(F.column_continuity_page)

        paragraphs = [el.text for el in book.elements if el.kind == "p"]
        assert paragraphs == [F.CONTINUITY_TEXT], paragraphs

    def test_notes_stay_a_side_channel_on_a_column_page(self):
        """Columns must not push bottom-zone prose into the notes, and the marker
        still binds its note."""
        book = _book(F.column_notes_page)

        text = _whole_text(book)
        first_left = text.index("the account is given by Diodorus")
        second_left = text.index("Its second step follows the first")
        first_right = text.index("A different argument starts here")
        second_right = text.index("This evidence belongs with the next")
        assert first_left < second_left < first_right < second_right, text
        assert any(n.num == 24 for n in book.notes), book.notes
        assert not any("Diodorus Siculus" in el.text for el in book.elements)
        markers = [int(run[1]) for el in book.elements for run in el.runs
                   if run[0] in ("sup", "mark") and str(run[1]).isdigit()]
        assert 24 in markers

    def test_a_ruled_table_is_not_read_column_major(self):
        """The control: a ruled grid is a table, not prose columns. Reading it
        column-major destroys every row."""
        book = _book(F.ruled_table_page)

        text = _whole_text(book)
        assert text.index("Day") < text.index("Night") < text.index("Sun") \
               < text.index("Moon"), text

    def test_an_ordinary_page_keeps_its_order(self):
        """The single-column control: nothing about a normal page may change."""
        book = _book(F.prose_page)

        text = _whole_text(book)
        assert text.startswith("The astrologers of this period")
        assert "multi_column" not in (book.page_reasons(0) or [])
