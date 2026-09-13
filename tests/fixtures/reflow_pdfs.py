# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Synthetic PDFs that reproduce the page shapes Reflow has to survive.

Each builder here recreates a layout measured on a real scanned monograph
(calibre book 567, *Hellenistic Astrology*), so a unit test can drive the same
code path the acceptance run drives without shipping an 9 MB copyrighted PDF.

The measurements that matter and are reproduced faithfully:

* body type 11.3 pt on a 15 pt leading;
* a run-in sub-heading set BOLD at 13 pt but on the body's own leading, so MuPDF
  hands the heading and the paragraph under it back as a single block;
* footnote numbers set at 5.3 pt (0.56 of the note text's 9.4 pt) in the bottom
  zone, with the note text beside them;
* inline note markers at 8.8 pt (0.78 of body) — sometimes with the superscript
  flag, sometimes sitting on the baseline, which is what a commercial OCR layer
  produces.
"""

import pymupdf

BODY_SIZE = 11.3
BODY_LEADING = 15.0
HEAD_SIZE = 13.0
MARKER_SIZE = 8.8
NOTE_NUM_SIZE = 5.3
NOTE_TEXT_SIZE = 9.4

PAGE_W = 504.0
PAGE_H = 720.0
LEFT = 54.0
BODY_TOP = 84.0
NOTE_TOP = 520.0

_ROMAN = "tiro"
_BOLD = "tibo"


def _put(page, x, y, text, size=BODY_SIZE, font=_ROMAN, rise=0.0):
    page.insert_text((x, y - rise), text, fontname=font, fontsize=size)
    return pymupdf.get_text_length(text, fontname=font, fontsize=size)


def new_doc():
    return pymupdf.open()


def add_page(doc):
    return doc.new_page(width=PAGE_W, height=PAGE_H)


def add_running_head(page, folio, title):
    """A page's furniture: folio and running title in the top band."""
    _put(page, LEFT, 40.0, folio, size=10.2)
    _put(page, LEFT + 40, 40.0, title, size=9.4)


def add_body_lines(page, lines, top=BODY_TOP, size=BODY_SIZE):
    """Plain body lines, one paragraph, at body leading. Returns the next y."""
    y = top
    for line in lines:
        _put(page, LEFT, y, line, size=size)
        y += BODY_LEADING
    return y


def add_run_in_heading(page, heading, body_lines, top=BODY_TOP):
    """A bold heading set at body leading, immediately followed by its paragraph.

    This is defect A: MuPDF returns one block because the heading sits on the
    body's own leading, so a naive reader emits
    ``Serapio of Alexandria (First Century CE?) Serapio of Alexandria was ...``
    as a single paragraph and the reader loses a navigable heading.
    """
    _put(page, LEFT, top, heading, size=HEAD_SIZE, font=_BOLD)
    y = top + BODY_LEADING
    for line in body_lines:
        _put(page, LEFT, y, line, size=BODY_SIZE)
        y += BODY_LEADING
    return y


def add_marker(page, x, y, digits, superscript=True):
    """An inline footnote marker. ``superscript`` raises it off the baseline the
    way the real OCR layer does (it sets the flag for some and not others)."""
    rise = 4.0 if superscript else 0.0
    return _put(page, x, y, digits, size=MARKER_SIZE, rise=rise)


def add_line_with_marker(page, y, before, digits, after="", superscript=True):
    """One body line whose text is interrupted by a note marker."""
    x = LEFT
    x += _put(page, x, y, before)
    x += add_marker(page, x, y, digits, superscript=superscript)
    if after:
        _put(page, x, y, after)
    return y + BODY_LEADING


def add_notes(page, notes, top=NOTE_TOP):
    """The page's footnote zone: small leading numbers, small text."""
    y = top
    for num, text in notes:
        x = LEFT + 4
        x += _put(page, x, y, str(num), size=NOTE_NUM_SIZE)
        _put(page, x + 2, y, text, size=NOTE_TEXT_SIZE)
        y += 12.0
    return y


def defect_a_page(doc):
    """Page 121 of book 567: the ``Serapio of Alexandria`` run-in heading."""
    page = add_page(doc)
    add_running_head(page, "94", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    add_run_in_heading(
        page,
        "Serapio of Alexandria (First Century CE?)",
        ["Serapio of Alexandria was an astrologer who wrote on",
         "inceptional astrology and possibly other topics, although",
         "only fragments of his work survive."],
        top=BODY_TOP,
    )
    return page


def defect_b_pages(doc):
    """A paragraph broken mid-sentence at a page turn, with footnotes between.

    The tail page ends ``...oftentimes Firmicus`` with seven notes under it; the
    head page opens lowercase with ``is more expansive...``. The notes sit
    between the two paragraphs in reading order, which is what defeated the
    previous converter's stitcher.
    """
    tail = add_page(doc)
    add_running_head(tail, "95", "ANUBIO (FIRST CENTURY CE?)")
    y = add_body_lines(tail, [
        "of Firmicus, which seemed to imply that he had translated",
        "much material from the Greek text of Anubio.",
    ])
    add_line_with_marker(tail, y, "However, the editor noted that", "166",
                         " oftentimes Firmicus", superscript=True)
    add_notes(tail, [(160, "As noted in Pingree, Yavanajataka, vol. 2, p. 441."),
                     (161, "Hephaestio, Apotelesmatika, 2, 2: 11-18."),
                     (166, "Anubio, Carmen, ed. Obbink, pp. 23-37.")])

    head = add_page(doc)
    add_running_head(head, "96", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    add_body_lines(head, [
        "is more expansive in the delineations he gives, and sometimes",
        "there can be major discrepancies between Anubio and Firmicus.",
    ])
    return tail, head


def defect_c_page(doc):
    """The three OCR marker-damage modes, as measured, on one page.

    * C1 the leading ``1`` read as an apostrophe: ``CE.'`` + a ``56`` span;
    * C2 the trailing ``0`` read as a degree sign: ``caution.`` + ``16`` + ``°``;
    * C3 the whole superscript read as punctuation: ``directly.'"`` and nothing else.
    """
    page = add_page(doc)
    add_running_head(page, "95", "ANUBIO (FIRST CENTURY CE?)")
    y = BODY_TOP
    y = add_line_with_marker(page, y, "sometime prior to the second century CE.'",
                             "56", " Pingree notes that", superscript=True)
    x = LEFT
    x += _put(page, x, y, "so it should be used with caution.")
    x += add_marker(page, x, y, "16", superscript=False)
    _put(page, x, y, "° It is in this text that he is called")
    y += BODY_LEADING
    _put(page, LEFT, y, "the domicile lords of the luminaries.'\" Rhetorius cited him")
    y += BODY_LEADING
    add_notes(page, [(156, "Cumont first made this argument in CCAG 8, 4, p. 225."),
                     (160, "As noted in Pingree, Yavanajataka, vol. 2, p. 441."),
                     (161, "Hephaestio, Apotelesmatika, 2, 2: 11-18.")])
    return page


def numbered_bibliography_page(doc):
    """A page of bold-numbered endnote entries — 103 of them in book 567.

    These are run-in heading *candidates* by font weight and every one is a correct
    rejection. A heading rule that accepts them fills the table of contents with
    ``15.`` and ``70.`` Both printed shapes are here: the number alone on its line,
    and the number leading a line that runs on into the entry.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "15.", size=BODY_SIZE, font=_BOLD)
    y += BODY_LEADING
    for line in ["Brennan, Hellenistic Astrology, p. 15. See also the",
                 "discussion in Schmidt, Sages, pp. 44-48."]:
        _put(page, LEFT, y, line, size=BODY_SIZE)
        y += BODY_LEADING
    y += 6

    x = LEFT
    x += _put(page, x, y, "70. ", size=BODY_SIZE, font=_BOLD)
    _put(page, x, y, "Pingree, Yavanajataka, vol. 2, p. 441, noted the same.")
    y += BODY_LEADING
    _put(page, LEFT, y, "Compare Neugebauer, HAMA, pp. 613-618.")
    return page


def lead_in_page(doc):
    """A bold small-caps lead-in whose sentence carries on in lowercase.

    Measured 206 times per book. It is bold, it is short, it is the first line of its
    block, and it is NOT a heading — the paragraph it opens is the same sentence.
    Loosening the run-in heading rule without this veto invents a heading here.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "IT IS AN HONOR AND A PRIVILEGE", size=BODY_SIZE, font=_BOLD)
    y += BODY_LEADING
    for line in ["to introduce this new translation of the Anthology to",
                 "an audience that has waited a very long time for it."]:
        _put(page, LEFT, y, line, size=BODY_SIZE)
        y += BODY_LEADING
    return page


def degree_control_page(doc):
    """Genuine ``N\u00b0`` degree tokens, and a note that is properly marked.

    Book 567 prints 207 legitimate degree tokens. The C2 repair turns ``16`` + ``\u00b0``
    into note 160, so this page holds the case that must survive untouched: the digits
    are body size, not marker size, and note 160 already has a real marker.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "the Sun was at 16\u00b0 of Aries and the Moon at 45\u00b0 of Leo,")
    y += BODY_LEADING
    y = add_line_with_marker(page, y, "which Valens calls a whole-sign trine.", "160",
                             " Rhetorius agrees.", superscript=True)
    add_notes(page, [(160, "Valens, Anthology, 2, 17, ed. Pingree, p. 71.")])
    return page


def apostrophe_control_page(doc):
    """Printed apostrophes that the C1 repair must not eat.

    The repair strips an apostrophe the OCR read in place of a marker's leading
    ``1``. Two printed shapes must survive it: a possessive (no sentence punctuation
    in front of it) and a closing quotation after a full stop, where only the fact
    that the marker resolved on its own face tells the two apart.
    """
    page = add_page(doc)
    y = BODY_TOP
    y = add_line_with_marker(page, y, "he called it the astrologers'", "61",
                             " and left it there.", superscript=True)
    add_line_with_marker(page, y, 'the oracle answered "it shall be so.\'', "62",
                         " and departed.", superscript=True)
    add_notes(page, [(61, "Schmidt, Sages, p. 12."),
                     (62, "Pingree, From Astral Omens, p. 40.")])
    return page


def ambiguous_residue_page(doc):
    """Two quotation residues and one unmarked note: the counts disagree.

    Pairing them off anyway binds the note to whichever quotation came first, which
    is a coin flip printed into the reader's book. The page goes to the model
    instead.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "Valens calls it 'the place of fortune.'\" Rhetorius agrees.")
    y += BODY_LEADING
    _put(page, LEFT, y, "Antiochus says it is 'the lot of the daemon.'\" Porphyry too.")
    add_notes(page, [(88, "Valens, Anthology, 4, 4, ed. Pingree, p. 155.")])
    return page


def chart_label_page(doc):
    """A chart key set in large type: the thing that outranks real headings on size.

    ``DAY CHART a 9 -5 e`` is what an astrological chart's legend looks like after
    OCR. By type size alone it beats every chapter title in the book, and a table of
    contents built from these is unusable.
    """
    page = add_page(doc)
    _put(page, LEFT, BODY_TOP, "DAY CHART a 9 -5 e", size=16.0, font=_BOLD)
    y = BODY_TOP + BODY_LEADING
    for line in ["The chart above is the one Valens uses to illustrate",
                 "the doctrine of sect throughout the Anthology."]:
        _put(page, LEFT, y, line, size=BODY_SIZE)
        y += BODY_LEADING
    return page


def balanced_quotation_page(doc):
    """A closing quotation ``.'\"`` on a page whose notes are all marked already.

    The C3 repair pairs leftover quote residue with unmarked notes. With nothing
    unmarked there is nothing to pair, and this punctuation must stay punctuation.
    """
    page = add_page(doc)
    y = BODY_TOP
    y = add_line_with_marker(page, y, "Valens wrote that the lot is 'the place of fortune.'\"",
                             "77", " Rhetorius repeats it.", superscript=True)
    _put(page, LEFT, y, "The phrase recurs in the later compilations.")
    add_notes(page, [(77, "Valens, Anthology, 4, 4, ed. Pingree, p. 155.")])
    return page


def hyphenated_page(doc):
    """A line-break hyphen that must be repaired, and a compound that must not."""
    page = add_page(doc)
    add_body_lines(page, [
        "the planets are said to be in conjunc-",
        "tion when they occupy the same degree, and the Sun-",
        "Moon relationship is called a syzygy.",
    ])
    return page


def image_only_page(doc, png_bytes):
    """A page that is nothing but a raster: no text layer at all."""
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    return page


def solid_png(width=60, height=80, colour=(20, 20, 20)):
    """A small opaque PNG, for pages that must carry real ink."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.set_rect(pix.irect, colour)
    return pix.tobytes("png")
