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


#: A real typeset page of this book runs to about 40 lines and 2,700 characters
#: (MEASURED: median 2701 over every seventh page of the acceptance book, p10 1127).
#: A seven-line stand-in is a thin page and would be classified as one, correctly —
#: so a fixture that stands in for an ordinary page has to be an ordinary page.
PROSE_LINES = [
    "The astrologers of this period were not in agreement about the",
    "question, and it is for that reason that we have to be careful when",
    "we read the later compilations, because they often preserve more",
    "than one view of what the doctrine was and how it should be used.",
    "This is the point that has been made by a number of scholars who",
    "have looked at the transmission of the texts in some detail, and it",
    "is one of the things that will be taken up again in a later chapter.",
    "What we can say with some confidence is that the material which",
    "came into Greek from the older traditions of Mesopotamia was not",
    "taken over without change, and that the writers who worked with",
    "it were willing to set aside what did not fit the system they were",
    "building. The result is a body of doctrine that looks unified on",
    "the surface and turns out, when it is read closely, to be a record",
    "of several centuries of argument about how the art should work.",
    "There are places where the disagreement is stated openly, and the",
    "reader is told that some of the older authorities held one view",
    "while others held another; there are many more places where it has",
    "been smoothed over by a compiler who had no interest in the",
    "history of the question and only wanted a rule that could be",
    "applied. It is the second kind of passage that causes the most",
    "trouble, because nothing in the text itself tells us that a choice",
    "was made at all, and we are left to infer it from what the other",
    "sources say about the same doctrine. That is why the later Arabic",
    "and Latin translations matter so much to this discussion, even",
    "though they are far removed in time from the Greek originals: they",
    "often preserve a version of the material that had gone out of use",
    "in the tradition we can read directly, and they allow us to see",
    "which of the two views a given compiler had decided to follow.",
]


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


def add_merged_notes(page, notes, top=NOTE_TOP):
    """Footnotes whose raised number the OCR could not keep apart from the text.

    MEASURED on the acceptance book: 138 footnotes on 70 pages open with a single
    full-size ``24 Diodorus Siculus, ...`` span, because a 5.7pt raised digit beside
    9.5pt text is below what the scanner's segmentation can separate. A reader that
    only recognises the small standalone span leaves every one of them inline in the
    body, which is the footnote defect this whole side channel exists to prevent.
    """
    y = top
    for num, text in notes:
        _put(page, LEFT + 4, y, "%d %s" % (num, text), size=NOTE_TEXT_SIZE)
        y += 12.0
    return y


def add_display_line(page, text, y=120.0, size=22.0):
    """One line of display type, as a title page carries it."""
    _put(page, LEFT, y, text, size=size, font=_BOLD)
    return page


def chapter_opening_page(doc, title, folio="31", size=16.0):
    """A chapter opening: the title in chapter type over its first paragraph."""
    page = add_page(doc)
    add_running_head(page, folio, "CHAPTER 2: ORIGINS OF HELLENISTIC ASTROLOGY")
    _put(page, LEFT, BODY_TOP, title, size=size, font=_BOLD)
    add_body_lines(page, PROSE_LINES[:12], top=BODY_TOP + 24.0)
    return page


def section_heading_page(doc, title, folio="32", size=13.0):
    """A section heading: the next step down the ladder, used far more often."""
    page = add_page(doc)
    add_running_head(page, folio, "CHAPTER 2: ORIGINS OF HELLENISTIC ASTROLOGY")
    _put(page, LEFT, BODY_TOP, title, size=size, font=_BOLD)
    add_body_lines(page, PROSE_LINES[:12], top=BODY_TOP + 20.0)
    return page


def title_page(doc):
    """The one page in a book set in display type, and the one that must not be
    allowed to define the heading ladder: nothing on it is a heading level."""
    page = add_page(doc)
    add_display_line(page, "HELLENISTIC ASTROLOGY", y=200.0, size=22.7)
    add_display_line(page, "The Study of Fate and Fortune", y=240.0, size=21.0)
    add_display_line(page, "CHRIS BRENNAN", y=320.0, size=13.8)
    return page


def merged_note_number_page(doc):
    """Page 46 of book 567: three footnotes, one of whose numbers merged into text."""
    page = add_page(doc)
    add_running_head(page, "20", "CHAPTER 2: ORIGINS OF HELLENISTIC ASTROLOGY")
    y = add_body_lines(page, PROSE_LINES[:8])
    y = add_line_with_marker(page, y, "the account is given by Diodorus", "24",
                             " and repeated later", superscript=True)
    add_merged_notes(page, [
        (24, "Diodorus Siculus, Library of History, 17: 112, trans. Oldfather."),
        (25, "Cramer, Astrology in Roman Law, p. 58."),
    ])
    return page


def broken_note_number_page(doc):
    """Page 29 of book 567: the note numbered 10 came back from the OCR as ``1``.

    Its neighbours are 9 and 11, the body carries a marker for 10, and no other
    number fits the gap. Everything needed to repair it is on the page; guessing is
    not required and is not allowed.
    """
    page = add_page(doc)
    add_running_head(page, "3", "CHAPTER 1: ASTROLOGY IN MESOPOTAMIA AND EGYPT")
    y = add_body_lines(page, PROSE_LINES[:6])
    y = add_line_with_marker(page, y, "the Diaries were continued", "10",
                             " for several centuries", superscript=True)
    add_notes(page, [(9, "Rochberg, The Heavenly Writing, p. 44."),
                     (1, "Parker, A Vienna Demotic Papyrus, p. 12."),
                     (11, "Pingree, From Astral Omens to Astrology, p. 26.")])
    return page


def ambiguous_note_number_page(doc):
    """The same damage with a wider gap: 9, then ``1``, then 13. The broken number
    could be 10, 11 or 12 and nothing on the page decides between them."""
    page = add_page(doc)
    add_running_head(page, "4", "CHAPTER 1: ASTROLOGY IN MESOPOTAMIA AND EGYPT")
    add_body_lines(page, PROSE_LINES[:6])
    add_notes(page, [(9, "Rochberg, The Heavenly Writing, p. 44."),
                     (1, "Parker, A Vienna Demotic Papyrus, p. 12."),
                     (13, "Pingree, From Astral Omens to Astrology, p. 26.")])
    return page


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


def heading_page_the_model_has_to_see(doc):
    """A run-in heading on a page the router sends anyway.

    Defect A's geometry with defect C's ambiguity on top of it, because the two
    questions the model can get wrong about a heading -- where it goes and whether
    something else is one -- can only be asked about a page that is actually sent.
    """
    page = add_page(doc)
    add_running_head(page, "94", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    y = add_run_in_heading(
        page,
        "Serapio of Alexandria (First Century CE?)",
        ["Serapio of Alexandria was an astrologer who wrote on",
         "inceptional astrology and possibly other topics, although"],
        top=BODY_TOP,
    )
    _put(page, LEFT, y, "only fragments of his work survive.\" Pingree agrees.")
    y += BODY_LEADING
    _put(page, LEFT, y, "Schmidt calls him an early systemizer.\" Cumont too.")
    add_notes(page, [(88, "Pingree, Yavanajataka, vol. 2, pp. 440-441.")])
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


def lead_in_and_lost_marker_page(doc):
    """The same bold lead-in, on a page that also lost a note's marker.

    The heading question on this page settles nothing, and the missing marker
    settles a great deal, so the page is still worth paying for.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "IT IS AN HONOR AND A PRIVILEGE", size=BODY_SIZE, font=_BOLD)
    y += BODY_LEADING
    _put(page, LEFT, y, "to introduce this new translation of the Anthology.'ts")
    y += BODY_LEADING
    _put(page, LEFT, y, "It has waited a very long time for a reader like this one.")
    add_notes(page, [(135, "Valens, Anthology, 4, 11, ed. Pingree, p. 165.")])
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


def wrecked_marker_page(doc):
    """A superscript the scanner read as digits, and the wrong digits at that.

    MEASURED on page index 117 of the acceptance book: the page prints
    ``Anthology.`` with a superscript 135 and the text layer returns
    ``Anthology.''s``. The shape varies -- 124 gets ``r's`` for 175 -- and once a
    letter the glyph table does not know is in it, nothing deterministic reads a
    number out of it at all. Guessing one anyway is the substitution the whole
    design forbids, so the note stays unreferenced and the page is routed. What
    the model can do is leave the wreckage exactly where it is and put the noteref
    beside it, and that is what this page exists to check end to end.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "He treats the same topic in book 4 of the Anthology.'ts")
    y += BODY_LEADING
    _put(page, LEFT, y, "While Valens does mention Critodemus, he says little of him.")
    add_notes(page, [(135, "Valens, Anthology, 4, 11, ed. Pingree, p. 165.")])
    return page


def swept_note_page(doc):
    """A note whose own printed number the scanner read as punctuation.

    MEASURED on the acceptance book, page index 103: the page prints notes 58, 59
    and 60 under the rule, and the text layer returns 58's text running straight on
    into 59's after a stray quotation mark, with no 59 anywhere. 29 of the book's
    notes are lost this way. The reader cannot see the note, so it cannot be one of
    the numbers a model is allowed to put back -- and the page is routed to a model
    for exactly this reason, which makes refusing its answer the worst of both.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "Valens cites the older authorities.\" Rhetorius repeats it.")
    y += BODY_LEADING
    # As the real page returns it: 59's body marker survived, fused onto the word in
    # front of it, and only the note's own number under the rule was lost.
    _put(page, LEFT, y, "Antiochus gives the same list in a later chapter.59")
    add_notes(page, [
        (58, 'Cumont, Astrology, p. 76. " Pliny, Natural History, 2, 6: 38.'),
        (60, "Hubner, Eigenschaften, p. 12."),
    ])
    return page


def two_swept_notes_page(doc):
    """The control for the gap rule: two numbers missing between two printed ones.

    One missing number has one place to go. Two do not: the page prints 58 and 61,
    and nothing on it says where 59 stops and 60 starts. A converter that splits a
    note there is guessing at a citation boundary, so the gap is left alone.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "Valens cites the older authorities.\" Rhetorius repeats it.")
    add_notes(page, [
        (58, 'Cumont, Astrology, p. 76. " Pliny, Natural History, 2, 6: 38. '
             '" Antiochus, Summary, p. 116: 3-12.'),
        (61, "Hubner, Eigenschaften, p. 12."),
    ])
    return page


def shortened_note_number_page(doc):
    """The other control: the missing number is printed, one digit short.

    MEASURED on the acceptance book, page 157: the note zone returns ``26, 27, 28,
    29, 3, 31`` -- the 3 is the 30, whose second digit the scan dropped, and the
    same damage hit the marker pointing at it, so the deterministic repair has no
    undamaged number to read it back from. The gap between 29 and 31 is real and
    there is nothing swept about it: the note is right there.
    """
    page = add_page(doc)
    y = add_line_with_marker(page, BODY_TOP, "Valens cites the older authorities.", "29")
    _put(page, LEFT, y, "Rhetorius repeats the same list in a later chapter.")
    add_notes(page, [
        (29, "Pingree, From Astral Omens, p. 41."),
        (3, "Pingree, \"Masha'allah's (?) Arabic Translation of Dorotheus.\""),
        (31, "King, \"A Hellenistic Astrological Table,\" p. 667."),
    ])
    return page


def quietly_swept_note_page(doc):
    """The same damage on a page where nothing else is wrong.

    MEASURED on the acceptance book, page 115: notes 124 and 126 are printed and both
    are marked in the body, so every count on the page agrees and the deterministic
    pass reports nothing. Note 125 is gone -- its text hangs off the end of 124 after
    the letters the scanner made of its number -- and the reader loses a citation on a
    page no one ever looks at. 6 of the book's 29 swept notes are on pages like this.
    """
    page = add_page(doc)
    y = add_line_with_marker(
        page, BODY_TOP, "Antiochus is an important source for the tradition.", "124")
    add_line_with_marker(
        page, y, "Critodemus was a semi-significant early expositor.", "126")
    add_notes(page, [
        (124, 'Pingree, "Antiochus and Rhetorius," p. 207. Its CCAG 8, 3, p. 116: 3-12.'),
        (126, "Pliny, Natural History, 2, 6: 38."),
    ])
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


def table_column_heads_page(doc):
    """A table's column heads, set as large as a chapter title, over a real section.

    MEASURED on page index 297 of the acceptance book. Table 8.2's header row comes
    back from the scan as ``Day Night  I' J/ /  0`` -- two column names, then the
    scanner's attempt at the planetary glyphs beneath them. Two real words clear the
    junk veto's "at least two words" floor, and the wreckage has letters in it, so
    nothing on the line counts as a loose glyph either. It became an <h1>: it split
    the chapter, took the section that follows it out of "Triplicities", and put
    itself in the table of contents in that section's place.
    """
    page = add_page(doc)
    add_running_head(page, "298", "CHAPTER 8: THE DOCTRINE OF SECT")
    _put(page, LEFT, BODY_TOP, "Day Night  I' J/ /  0", size=16.0, font=_BOLD)
    y = add_body_lines(page, PROSE_LINES[:4], top=BODY_TOP + 24.0)
    _put(page, LEFT, y + 10.0, "Ptolemy's Alternative Triplicity Ruler Scheme",
         size=13.0, font=_BOLD)
    add_body_lines(page, PROSE_LINES[4:10], top=y + 32.0)
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


def bold_body_block_page(doc):
    """A block of body text the size machinery promotes to a heading.

    On an OCR'd page the type sizes are estimated from glyph boxes, and a
    full-bold block of prose reads as all-heading: the element ends with a
    hyphen, 'politi-', and the wrap can never join because paragraphs do not
    join into headings. A real heading never ends mid-word.
    """
    page = add_page(doc)
    y = BODY_TOP
    for line in [
        "In the 13 Century, astrology was widely supported by military and politi-",
        "cal elites, and was commented and theorized upon by academics like St.",
        "Thomas Aquinas and his teacher, St. Albert the Great.",
    ]:
        _put(page, LEFT, y, line, font=_BOLD)
        y += BODY_LEADING
    add_body_lines(page, ["The political reading of the chart follows from it."]
                   + PROSE_LINES[:4], top=y + BODY_LEADING)
    return page


def hyphenated_page(doc):
    """A line-break hyphen that must be repaired, and a compound that must not.

    ``conjunction`` is printed whole later in the same paragraph, which is how
    the book's own vocabulary earns the heal: the wrapped form and the compound
    cannot be told apart by shape, only by what this book says.
    """
    page = add_page(doc)
    add_body_lines(page, [
        "the planets are said to be in conjunc-",
        "tion when they occupy the same degree, and the Sun-",
        "Moon relationship is called a syzygy. A conjunction",
        "is the word the older sources use for it.",
    ])
    return page


def hyphenated_multispan_page(doc):
    """Book 562's OCR-layer shape: a hyphenated word at a line end, set as
    word-per-span runs with the space as its own span.

    The stitcher healed the single-span shape and lost this one: the trailing
    space run stayed between the halves and the page read ``tripli city`` for
    ``triplicity`` -- two words printed where the source has one. The tiny size
    alternation is what a real OCR layer's per-word spans look like to MuPDF.
    """
    page = add_page(doc)
    y = BODY_TOP
    x = LEFT
    for index, word in enumerate(["looked", "at", "the", "lords", "of",
                                  "the", "tripli-"]):
        x += _put(page, x, y, word, size=BODY_SIZE + (index % 2) * 0.01)
        x += _put(page, x, y, " ", size=BODY_SIZE + 0.03)
    y += BODY_LEADING
    _put(page, LEFT, y, "city of the Moon, the first of which is Mars,")
    y += BODY_LEADING
    _put(page, LEFT, y, "and whose triplicity is read first by day.")
    return page


def hyphen_heal_needs_a_real_word_pages(doc):
    """The residual hyphen class from the OCR corpus: the heal must be earned.

    ``eighth-`` + ``is`` is a sentence break, not a wrapped word (``eighthis``
    is not a word of this book); ``under-`` + ``standing`` wraps
    ``understanding``, which the book prints whole three lines later. A
    dictionary nobody ships cannot tell these apart; the book's own vocabulary
    can: only the second heals.
    """
    page = add_page(doc)
    add_body_lines(page, [
        "the eighth house is where the sect light",
        "rests, and every planet placed there reads",
        "differently. This is the basic understanding",
        "of the doctrine. The eighth-",
        "is angular in the day chart. A full under-",
        "standing of the chart follows from it.",
    ])
    return page


def page_turn_compound_hyphen_pages(doc):
    """Book 563's shape: ``spear-`` ends one page and ``bearing`` opens the next.

    The source counter heals line-break hyphens within a page, never across a
    page turn, so an output that heals there invents ``spearbearing`` against
    the printed ``spear-bearing``: a wrap-break and a printed compound cannot be
    told apart at the turn, and the honest reading keeps the hyphen.
    """
    tail = add_page(doc)
    add_body_lines(tail, PROSE_LINES[:4] + [
        "the hunting party carried a spear-"])
    head = add_page(doc)
    add_body_lines(head, ["bearing that marked him as their leader, and"] + PROSE_LINES[:6])
    return tail, head


def hyphenated_note_page(doc):
    """A footnote whose own text breaks a word across its two lines.

    Page 504 of book 567: ``...since it is a non-`` / ``standard view. There is...``,
    printed inside the note and not in the body above it.
    """
    page = add_page(doc)
    add_running_head(page, "478", "CHAPTER 12: THE ANTHOLOGY OF VETTIUS VALENS")
    y = add_body_lines(page, PROSE_LINES[:8])
    add_line_with_marker(page, y, "the remark is made in passing", "31",
                         " and not developed", superscript=True)
    y = NOTE_TOP
    x = LEFT + 4
    x += _put(page, x, y, "31", size=NOTE_NUM_SIZE)
    _put(page, x + 2, y, "It is not clear why Valens makes this remark, since it is a non-",
         size=NOTE_TEXT_SIZE)
    _put(page, LEFT + 4, y + 12.0,
         "standard view. There is also still a considerable debate about it.",
         size=NOTE_TEXT_SIZE)
    return page


def image_only_page(doc, png_bytes):
    """A page that is nothing but a raster: no text layer at all."""
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    return page


# --------------------------------------------------------------------- two columns

COL_LEFT = 54.0
COL_RIGHT = 300.0


def _column_rows(page, rows, top=BODY_TOP, size=BODY_SIZE, left=COL_LEFT,
                 right=COL_RIGHT):
    """Alternating rows of left/right column lines on shared baselines.

    Written row by row on purpose, because that is how MuPDF comes to hand both
    columns back as one block of same-baseline lines -- the exact shape that
    interleaved the readiness two-column probe.
    """
    y = top
    for left_line, right_line in rows:
        if left_line:
            _put(page, left, y, left_line, size=size)
        if right_line:
            _put(page, right, y, right_line, size=size)
        y += BODY_LEADING
    return y


TWO_COLUMN_ROWS = [
    ("First we read the left column.", "Next we read the right column."),
    ("The argument begins with evidence.", "A different argument starts here."),
    ("Its second step follows the first.", "This evidence belongs with the next."),
    ("We finish this column before moving.", "Finally the second column concludes."),
]


def two_column_page(doc):
    """The readiness probe's shape: two prose columns whose lines share baselines."""
    page = add_page(doc)
    _column_rows(page, TWO_COLUMN_ROWS)
    return page


def spread_with_background_photo_page(doc, png_bytes):
    """Book 566 page 20's shape: two logical pages side by side, and behind the
    left one the facsimile photograph itself, whose box crosses the gutter.

    The photograph is not a column: it must be kept as the page's figure while
    the two logical pages read left then right, never interleaved.
    """
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(40.0, 80.0, 265.0, 380.0), stream=png_bytes)
    _column_rows(page, TWO_COLUMN_ROWS)
    return page


def three_column_page(doc):
    """Three clear prose columns: left-to-right column order must survive."""
    page = add_page(doc)
    y = BODY_TOP
    for row in [
        ("Alpha opens the first.", "Beta opens the second.", "Gamma opens the third."),
        ("Alpha continues on.", "Beta continues on.", "Gamma continues on."),
        ("Alpha ends its column.", "Beta ends its column.", "Gamma ends its column."),
    ]:
        _put(page, 54.0, y, row[0])
        _put(page, 204.0, y, row[1])
        _put(page, 354.0, y, row[2])
        y += BODY_LEADING
    return page


def column_bands_page(doc):
    """Full-width headings between two-column bands: bands must not absorb them."""
    page = add_page(doc)
    _put(page, LEFT, BODY_TOP,
         "The Doctrine of Sect in Day and Night Charts Across the Whole Tradition",
         size=HEAD_SIZE, font=_BOLD)
    y = _column_rows(page, [
        ("Day charts are read first.", "Night charts are read second."),
        ("The sect light leads the day.", "The sect light leaves at night."),
    ], top=BODY_TOP + 24.0)
    _put(page, LEFT, y + 6.0,
         "How the Remaining Planets Behave When the Chart Changes Sect Entirely",
         size=HEAD_SIZE, font=_BOLD)
    _column_rows(page, [
        ("Saturn behaves by day then.", "Mars behaves by night then."),
        ("Jupiter witnesses the day.", "Venus witnesses the night."),
    ], top=y + 30.0)
    return page


def column_continuity_page(doc):
    """A paragraph that crosses the column break mid-sentence is one paragraph."""
    page = add_page(doc)
    _column_rows(page, [
        ("The doctrine continues across the", "should never notice the seam at"),
        ("column boundary without any pause,", "all. The second column closes"),
        ("and the careful reader", "the argument here."),
    ])
    return page


CONTINUITY_TEXT = ("The doctrine continues across the column boundary without "
                   "any pause, and the careful reader should never notice the "
                   "seam at all. The second column closes the argument here.")


def below_band_running_head_page(doc):
    """The same below-band head over one plain column: still furniture."""
    page = add_page(doc)
    _put(page, COL_RIGHT, 62.0, "SATURN - SATURN 1257", size=9.4)
    add_body_lines(page, PROSE_LINES[:6])
    return page


def below_band_caps_line_without_folio_page(doc):
    """A caps line below the band that carries no folio is content, however
    small and however high on the page."""
    page = add_page(doc)
    _put(page, COL_RIGHT, 62.0, "SATURN WITNESSES THE DAY CHART", size=9.4)
    add_body_lines(page, PROSE_LINES[:6])
    return page


def below_band_folio_page(doc):
    """The same scan shape with only the folio below the band: a bare number at
    the top of the page is furniture too (book 569's '516' and '324')."""
    page = add_page(doc)
    _put(page, COL_RIGHT, 62.0, "516", size=9.4)
    add_body_lines(page, PROSE_LINES[:6])
    return page


def scan_panel_diagram_ocr_leading_page(doc, png_bytes):
    """Book 569 page 547's figure panel: a right-hand prose panel beside a
    left-hand diagram, at the OCR layer's own leading -- twelve points on a
    seven-point line, where a two-point band tolerance reads every line as its
    own band and the diagram's channel is never measured."""
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    add_running_head(page, "516", "CHAPTER 53")
    add_body_lines(page, PROSE_LINES[:3])
    y = BODY_TOP + 5 * BODY_LEADING
    for line in ["Planets that see and perceive one an-",
                 "other intensify their existing aspect re-",
                 "lationship. Here, the sextile between",
                 "Venus in Gemini and Jupiter in Leo is",
                 "intensified due to the planets being",
                 "equidistant from the solstitial axis.",
                 "Venus sees Jupiter, and Jupiter",
                 "perceives Venus. Similarly, the trine",
                 "between Mars in Pisces and Saturn in",
                 "Scorpio is made more potent here.",
                 "Mars sees Saturn, and Saturn",
                 "perceives Mars at the same degree."]:
        _put(page, 300.0, y, line, size=7.0)
        y += 12.0
    add_body_lines(page, PROSE_LINES[5:8], top=y + 30.0)
    return page


def column_notes_page(doc):
    """Two prose columns over a footnote zone: the notes stay a side channel."""
    page = add_page(doc)
    x = COL_LEFT
    x += _put(page, x, BODY_TOP, "the account is given by Diodorus")
    add_marker(page, x, BODY_TOP, "24")
    _column_rows(page, [
        (None, "A different argument starts here."),
        ("and repeated later in the work.", "This evidence belongs with the next."),
        ("Its second step follows the first.", "The right column adds its point."),
        ("We finish this column before moving.", "Finally the second column concludes."),
    ], top=BODY_TOP + BODY_LEADING)
    add_notes(page, [(24, "Diodorus Siculus, Library of History, 17: 112.")])
    return page


def sign_pair_table_page(doc):
    """Book 569 page 547's shape: a mirror table of sign pairs with its aspect
    column -- five rows, each 'A looks at B' beside 'b perceives a' beside the
    aspect the row belongs to. The row is the unit: column-major prints every
    'looks at' away from its 'perceives', and a lowercase-driven paragraph join
    glues every aspect to the NEXT row's signs."""
    page = add_page(doc)
    add_body_lines(page, PROSE_LINES[:5])
    y = BODY_TOP + 6 * BODY_LEADING
    for left, mid, aspect in [
            ("GEMINI looks at LEO", "l e o perceives g e m in i", "Sextile"),
            ("TAURUS looks at VIRGO", "v ir g o perceives t a u r u s", "Trine"),
            ("ARIES looks at LIBRA", "l i b r a perceives a r i e s", "Opposition"),
            ("SCORPIO looks at PISCES", "pis c e s perceives Sc o r pio", "Trine"),
            ("SAGITTARIUS looks at AQUARIUS",
             "a q u a r iu s perceives Sa g it t a r iu s", "Sextile")]:
        _put(page, 99.0, y, left, size=6.0)
        _put(page, 215.0, y, mid, size=6.0)
        _put(page, 334.0, y, aspect, size=6.5)
        y += 10.0
    add_body_lines(page, PROSE_LINES[5:8], top=y + 22.0)
    return page


def ruled_table_page(doc):
    """A ruled two-column table: cell rows must not become prose column traversal.

    The ruling is the signal a reader's eye uses: this is a grid, not two columns
    of prose. A converter that reads it column-major prints every first cell and
    then every second cell, and no row survives.
    """
    page = add_page(doc)
    rows = [("Day", "Night"), ("Sun", "Moon"), ("Venus", "Mars"),
            ("Jupiter", "Saturn")]
    top = BODY_TOP
    _column_rows(page, rows)
    bottom = top + BODY_LEADING * len(rows)
    for x in (48.0, 280.0, 460.0):
        page.draw_line((x, top - 12.0), (x, bottom), color=(0, 0, 0), width=0.7)
    for y in (top - 12.0, top + 2.0, bottom):
        page.draw_line((48.0, y), (460.0, y), color=(0, 0, 0), width=0.7)
    return page


def unruled_label_table_page(doc):
    """Book 569's zodiacal tables: a label column of one- and two-word lines
    beside a content column, with no ruling at all.

    Row relationships here are carried by position alone, so they are the easiest
    to destroy and the most important to keep: read column-major, 'Rulerships'
    prints six lines away from the rulers it names.
    """
    page = add_page(doc)
    _column_rows(page, [
        ("Astronomical features", "Northern, Commanding, Short Ascension, Crooked"),
        ("Characteristics", "Bestial, Quadrupedal, Terrestrial, Unprolific"),
        ("Places/Winds", "Babylon, South Wind"),
        ("Classifications", "Feminine, Nocturnal, Solid, Spring, Earth"),
        ("Rulerships", "domicile Venus, detriment Mars, exaltation Moon"),
        ("Triplicities", "Venus by day, Moon by night, participates Saturn"),
    ], left=97.0, right=167.0)
    return page


def ragged_single_column_page(doc):
    """Book 565 page 158's shape: an ordinary page of prose whose ragged short
    lines open fake gutters between their right edges.

    Most lines fill the measure; a handful of short ones (a paragraph tail, a
    date line) do not. Those few are not a column, and the page must read
    straight through in print order.
    """
    page = add_page(doc)
    y = add_body_lines(page, PROSE_LINES[:9])
    _put(page, LEFT, y, "Christianity was abolished.")
    y += BODY_LEADING
    y = add_body_lines(page, PROSE_LINES[9:15], top=y)
    _put(page, LEFT, y, "Mohammed and Augustine) who had strong influence upon the Christian-")
    y += BODY_LEADING
    _put(page, LEFT + 70, y, "so there would be individuals who would particularly")
    y += BODY_LEADING
    add_body_lines(page, PROSE_LINES[15:22], top=y)
    return page


# ------------------------------------------------------- artwork inside a page scan

def scan_chart_band_page(doc, png_bytes):
    """Book 567 index 220's shape: the chart is ink inside a full-page scan, and
    the OCR layer returns the chart's own labels as giant sparse lines.

    The labels are the trap: taken at face value they are 'text', so the gap the
    chart sits in never appears, and the diagram is dropped while its wreckage
    reads as prose. They are set two to four times the body size, which on a scan
    is what separates chart lettering from the book's prose."""
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    add_running_head(page, "194", "CHAPTER 7: THE PLANETS")
    _put(page, 179.6, 108.0, "DAY CHART", size=20.0, font=_BOLD)
    _put(page, 120.1, 155.0, "a", size=36.0)
    _put(page, 187.3, 156.0, "9", size=36.0)
    _put(page, 254.0, 156.0, "-5", size=36.0)
    _put(page, 325.3, 155.0, "e", size=36.0)
    _put(page, 96.8, 200.0, "+ 1 I", size=30.0)
    _put(page, 165.5, 250.0, "NIGHT CHART", size=20.0, font=_BOLD)
    _put(page, 122.3, 300.0, "9", size=36.0)
    _put(page, 96.8, 335.0, "+ 1", size=30.0)
    _put(page, 168.9, 388.0, "Figure 7.4 - Sect as a Spectrum")
    add_body_lines(page, PROSE_LINES[:8], top=416.0)
    return page


def scan_sidebar_figure_page(doc, png_bytes):
    """Book 567 index 485's shape: a natal wheel set beside a narrow prose column,
    its caption printed under the wheel, and no OCR glyphs inside the wheel."""
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    add_running_head(page, "459", "THE RULER OF THE TWELFTH PLACE")
    y = add_body_lines(page, PROSE_LINES[:3])
    _put(page, LEFT, y + 8.0, "JOHN F. KENNEDY JR.", size=HEAD_SIZE, font=_BOLD)
    top = y + 8.0 + BODY_LEADING * 1.8
    column = [
        "The native was the son of U.S.",
        "President John F. Kennedy, and",
        "he was born seventeen days",
        "after his father was elected",
        "President in November 1960.",
        "He had a night chart with",
        "Virgo rising, Leo occupying",
        "the twelfth place, and the",
        "Sun in Sagittarius in the",
        "fourth, close to the IC.",
    ]
    y = top
    for line in column:
        _put(page, LEFT, y, line)
        y += BODY_LEADING
    _put(page, 303.0, top + 6 * BODY_LEADING, "Chart 45 - John F. Kennedy Jr.")
    add_body_lines(page, PROSE_LINES[8:11], top=y + 20.0)
    return page


def vector_diagram_page(doc):
    """A born-digital page whose diagram is drawn, not embedded: vector paths.

    Nothing about this page is an image -- extract finds no embedded raster and a
    text layer around the drawing. A converter that only keeps embedded images
    loses the diagram whole.
    """
    import math
    page = add_page(doc)
    add_running_head(page, "301", "CHAPTER 8: THE DOCTRINE OF SECT")
    add_body_lines(page, PROSE_LINES[:4])
    cx, cy, radius = 252.0, 420.0, 110.0
    page.draw_circle((cx, cy), radius, color=(0, 0, 0), width=0.8)
    page.draw_circle((cx, cy), radius * 0.55, color=(0, 0, 0), width=0.8)
    for k in range(12):
        angle = k * math.pi / 6
        dx, dy = radius * math.cos(angle), radius * math.sin(angle)
        page.draw_line((cx - dx, cy - dy), (cx + dx, cy + dy),
                       color=(0, 0, 0), width=0.6)
    for k in range(48):
        angle = k * math.pi / 24
        x0 = cx + radius * 0.92 * math.cos(angle)
        y0 = cy + radius * 0.92 * math.sin(angle)
        x1 = cx + radius * math.cos(angle)
        y1 = cy + radius * math.sin(angle)
        page.draw_line((x0, y0), (x1, y1), color=(0, 0, 0), width=0.6)
    _put(page, 168.9, 556.0, "Figure 8.1 - The Twelve Places")
    add_body_lines(page, PROSE_LINES[8:12], top=580.0)
    return page


def garbage_scan_page(doc, png_bytes):
    """A photographed page whose OCR layer is noise, over a real scan.

    Book 561's shape. The text layer's boxes do not describe where the ink is, so
    any 'gap' measured from them is fiction: the layer must be trusted before
    figure territory is measured from it, or whole prose regions ship as crops.
    """
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    y = BODY_TOP
    for line in GARBAGE_LINES * 3:
        _put(page, LEFT, y, line)
        y += BODY_LEADING
    return page


def scan_chapter_opening_page(doc, png_bytes):
    """Book 567 page 93's shape: one line of display type over white space.

    A chapter opening is not a chart: the lone 'CHAPTER 4' must stay text, not
    become a mostly-blank figure crop with two words of artwork riding under it.
    """
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    add_running_head(page, "93", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    _put(page, 144.0, 200.0, "CHAPTER 4", size=19.0, font=_BOLD)
    _put(page, 144.0, 240.0, "The Hellenistic Astrologers", size=16.0, font=_BOLD)
    add_body_lines(page, PROSE_LINES[:8], top=306.0)
    return page


def scan_date_tail_page(doc, png_bytes):
    """Book 567 page 18's shape: a paragraph, air, and a body-sized date line.

    ``November 2016`` fails the junk veto (one word and a number), and it is a
    line of the book, not lettering on a diagram: absorbed as artwork it would
    leave the visible book when the blank territory under it is dropped."""
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    add_running_head(page, "18", "ACKNOWLEDGMENTS")
    add_body_lines(page, PROSE_LINES[:4])
    _put(page, 340.0, BODY_TOP + 9 * BODY_LEADING, "November 2016")
    return page


def illustrated_page(doc, png_bytes):
    """Prose with a plate set into it, and no caption under the plate.

    An illustration leaves no words in the text layer, so nothing that counts words
    can tell whether it survived the conversion. This is the page that proves a
    picture is not quietly dropped.
    """
    page = add_page(doc)
    add_running_head(page, "117", "ANUBIO (FIRST CENTURY CE?)")
    y = add_body_lines(page, PROSE_LINES[:8])
    page.insert_image(pymupdf.Rect(LEFT, y + 20.0, LEFT + 180.0, y + 140.0),
                      stream=png_bytes)
    add_body_lines(page, PROSE_LINES[8:12], top=y + 160.0)
    return page


def solid_png(width=60, height=80, colour=(20, 20, 20)):
    """A small opaque PNG, for pages that must carry real ink."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.set_rect(pix.irect, colour)
    return pix.tobytes("png")


def marked_png(marks, width=120, height=160, paper=(245, 245, 245),
               ink=(20, 20, 20)):
    """Paper with ink only where the test says the art is.

    A solid page is ink everywhere, including the margins a real scan leaves
    blank; edge-gap territory rules can then not tell a margin from a figure.
    ``marks`` are (x0, y0, x1, y1) rectangles in PNG pixels.
    """
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.set_rect(pix.irect, paper)
    for x0, y0, x1, y1 in marks:
        pix.set_rect(pymupdf.IRect(x0, y0, x1, y1), ink)
    return pix.tobytes("png")


#: Where each scan fixture's artwork sits, in PDF points, so the page raster
#: carries ink there and paper everywhere else.
CHART_BAND_ART = (90.0, 100.0, 345.0, 395.0)
SIDEBAR_WHEEL_ART = (250.0, 130.0, 440.0, 320.0)
PANEL_DIAGRAM_ART = (54.0, 140.0, 290.0, 430.0)


def art_png(*pdf_rects):
    """The marked page for one fixture: ink in the art zones, blank margins."""
    sx, sy = 120.0 / PAGE_W, 160.0 / PAGE_H
    return marked_png([(int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy))
                       for x0, y0, x1, y1 in pdf_rects])


#: Lowercase, word-shaped, and not English: what a bad OCR pass leaves on a page of
#: foxed type. A character counter sees a full page of text. A word counter sees a
#: full page of words. Only the *common* words are missing, which is the one signal
#: that separates a text layer worth having from one that has to be thrown away.
GARBAGE_LINES = [
    "aenlm rtoiu cdhes ngiol rtaem uqsli pnoew mtchi rvael",
    "sdlku ngtae rmoib phlcs evtam nrsiq dolge twhca pmrei",
    "ltnoa csdue rmigh pwtal ensvo bdrik mcuqa sltep rnaiv",
    "gtoem rslnc adpui hwtem ovlsa rnbit qcdes muplo rtiaw",
    "elsnr tohca pmiqd ugvel rnsat bcloi wtpme adsru nlogh",
    "crtem plwua sgdoi nvtea rlbam qcish potwe unldr gmsea",
    "trnol asvic pduem bghwa rlseo tqnim cdpav wtuel rngba",
    "moish rctpa elgnv wdsua rtlem bcoqi nphes atvug rmodl",
    "swcta prnei ulmgo hdbar vtlse qcnip amteo rwugd lhnsc",
    "bitre mplao cvnud gwsha rtoel mqbin adlur pctev snigo",
]


def garbage_text_page(doc):
    """A text layer that is pure noise — the shape a bad OCR pass leaves behind.

    Every character counter reports a full page of content here, and so does every
    word counter. Only the common English words are absent, which is why the census
    counts *those* rather than characters.
    """
    page = add_page(doc)
    y = BODY_TOP
    for line in GARBAGE_LINES * 3:
        _put(page, LEFT, y, line, size=BODY_SIZE)
        y += BODY_LEADING
    return page


def ocr_scan_page(doc, png_bytes, head="ANUBIO (FIRST CENTURY CE?)"):
    """A photograph of a page with a readable OCR text layer over it.

    This is the shape of the acceptance book, and the reason OCR_LAYER is its own
    verdict: the words are there and usable, but every structural signal a born-
    digital PDF would carry — font names, sizes, vector rules — is gone with the ink.
    """
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    add_running_head(page, "97", head)
    add_body_lines(page, PROSE_LINES)
    return page


def prose_page(doc, marker=None):
    """An ordinary page of prose: enough common words to read as real text."""
    page = add_page(doc)
    add_running_head(page, "97", "ANUBIO (FIRST CENTURY CE?)")
    add_body_lines(page, PROSE_LINES)
    if marker is not None:
        add_notes(page, [(marker, "Pingree, From Astral Omens to Astrology, p. 26.")])
    return page


def thin_page(doc):
    """Seven lines on a page: a sparse scan, or two printed pages photographed as one.

    Not a defect — but the user has to be told, because a book of these converts to
    an EPUB with a paragraph per page and the fault is in the PDF, not in Reflow.
    """
    page = add_page(doc)
    add_running_head(page, "97", "ANUBIO (FIRST CENTURY CE?)")
    add_body_lines(page, PROSE_LINES[:7])
    return page


def uncertain_join_pages(doc):
    """A page turn where the punctuation and the capital disagree.

    The tail page's last line carries no terminal punctuation, and the head page
    opens on a capital. Either the printer dropped a full stop, or a sentence runs
    on into a proper noun. The page image settles it and the text layer does not,
    which is precisely the page turn worth paying a model to look at — and the
    opposite of the defect-B turn, where the lowercase opening settles it for free.
    """
    tail = add_page(doc)
    add_running_head(tail, "101", "ANUBIO (FIRST CENTURY CE?)")
    add_body_lines(tail, PROSE_LINES[:12] + [
        "one manuscript breaks off at this point and the leaf after it is blank",
    ])

    head = add_page(doc)
    add_running_head(head, "102", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    add_body_lines(head, [
        "Firmicus gives the same doctrine in a fuller form, and the",
        "version he gives is the one that reached the Latin West.",
    ] + PROSE_LINES[:10])
    return tail, head


def orphan_marker_page(doc):
    """A marker printed on a page whose note is set somewhere else.

    Real books do this whenever a long note overflows its page. The marker is
    genuine; the note is simply not here. A converter that emits an EPUB footnote
    link anyway ships a button that goes nowhere.
    """
    page = add_page(doc)
    add_running_head(page, "98", "ANUBIO (FIRST CENTURY CE?)")
    y = add_body_lines(page, PROSE_LINES[:10])
    add_line_with_marker(page, y, "and the note for this passage is set overleaf",
                         "204", ".", superscript=True)
    return page


def typographers_page(doc):
    """Characters that are text on the page and markup in a file.

    ``&``, ``<`` and ``>`` are set as type in plenty of books — an ampersand in a
    publisher's name, angle brackets in an editorial insertion. Written into XHTML
    unescaped they make a file no EPUB reader will open.
    """
    page = add_page(doc)
    add_running_head(page, "99", "ANUBIO (FIRST CENTURY CE?)")
    add_body_lines(page, [
        "printed at the press of Hall & Fisher in the spring of that year,",
        "with the editor's insertion <the text breaks off here> set in brackets",
        "and the note \"cf. Ptolemy & Valens\" carried in the apparatus below.",
    ] + PROSE_LINES[:10])
    return page


def mid_page_heading_page(doc, title="Serapio of Alexandria", folio="103"):
    """A section heading that starts part way down a page which also sets notes.

    The chapter split happens at the heading, so this one page's text lands in two
    files while its footnotes stay in one of them. That is the case where a footnote
    link has to name the file it points into and not only the fragment.
    """
    page = add_page(doc)
    add_running_head(page, folio, "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    y = add_body_lines(page, PROSE_LINES[:6])
    y = add_line_with_marker(page, y, "as Pingree observed in his own edition",
                             "212", " of the text.")
    # A section heading is set with air above and below it, and the paragraph under
    # it opens a new sentence. Both are what tell a converter it is a heading and
    # not a bold lead-in to the sentence that follows.
    head_y = y + BODY_LEADING * 1.8
    _put(page, LEFT, head_y, title, size=HEAD_SIZE, font=_BOLD)
    add_body_lines(page,
                   ["Serapio is named in three of the surviving handbooks as an"]
                   + PROSE_LINES[6:14],
                   top=head_y + BODY_LEADING * 1.8)
    add_notes(page, [(212, "Pingree, Yavanajataka, vol. 2, p. 441.")])
    return page


def glyph_marker_page(doc):
    """A superscript note number the scanner read as letters, not digits.

    MEASURED on the acceptance book: a marker set two points below the body comes
    back as ``Hephaestio.s°`` for 50, ``Petosiris.si`` for 51, ``century.loo``
    for 100. There is no digit left for a marker span to recognise, so the note
    stays unreferenced and the page reads ``Hephaestio.s°``.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "Antiochus, Manetho, Ptolemy, Valens, and Hephaestio.s° Other")
    y += BODY_LEADING
    _put(page, LEFT, y, "authors such as Dorotheus were said to have drawn on them.")
    add_notes(page, [(50, "Hephaestio, Apotelesmatika, 2, 21: 26.")])
    return page


def glyph_marker_without_its_note_page(doc):
    """The control: the same damaged shape on a page that prints no note it fits.

    Nothing on this page says the letters are a number, so they stay letters and
    the page is routed instead.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "Antiochus, Manetho, Ptolemy, Valens, and Hephaestio.s° Other")
    y += BODY_LEADING
    _put(page, LEFT, y, "authors such as Dorotheus were said to have drawn on them.")
    add_notes(page, [(7, "Pingree, From Astral Omens, p. 40.")])
    return page


def split_glyph_marker_page(doc):
    """Half the marker survived as a digit and half came back as a letter.

    MEASURED: ``45`` prints as a marker span ``4`` followed by a body-size ``s``,
    so the marker resolves to nothing (there is no note 4) and the page reads
    ``above.4s``.
    """
    page = add_page(doc)
    y = BODY_TOP
    x = LEFT
    x += _put(page, x, y, "the length of life technique mentioned above.")
    x += add_marker(page, x, y, "4", superscript=False)
    _put(page, x, y, "s A system of determining the advantageous place")
    y += BODY_LEADING
    _put(page, LEFT, y, "was also attributed to them by later authors.")
    add_notes(page, [(45, "Valens, Anthology, 3, 9: 3.")])
    return page


def glyph_prefix_marker_page(doc):
    """The marker's leading digits came back as punctuation and its last as itself.

    MEASURED: note 103 prints as ``fourth.'°`` followed by a marker span ``3``.
    The window repair finds 103 from the ``3``; the ``'°`` is the ``10`` in
    front of it and has to go with it, or the page reads ``fourth.'°``.
    """
    page = add_page(doc)
    y = BODY_TOP
    x = LEFT
    x += _put(page, x, y, "Venus rejoices in the tenth and Saturn in the fourth.'°")
    x += add_marker(page, x, y, "3", superscript=False)
    _put(page, x, y, " It is not clear if Manilius is representing an")
    y += BODY_LEADING
    _put(page, LEFT, y, "otherwise unattested tradition or an error of his own.")
    add_notes(page, [(103, "Manilius, Astronomica, 2: 433-452.")])
    return page


def ambiguous_glyph_marker_page(doc):
    """The control: the damaged run is one edit away from two of the page's notes.

    ``"°`` reads as 110, and both 100 and 111 are printed here and unreferenced.
    Picking one prints a citation the page does not make.
    """
    page = add_page(doc)
    add_running_head(page, "110", "MARCUS MANILIUS")
    y = BODY_TOP
    _put(page, LEFT, y, "every four years until at least the mid-third century.\"° Marcus")
    y += BODY_LEADING
    _put(page, LEFT, y, "Manilius wrote an astrological poem in five books.")
    add_notes(page, [(100, "Ehrhardt, \"The Date of the First Balbillea.\""),
                     (111, "Volk, Manilius and his Intellectual Background.")])
    return page


def _runover_pages(doc, note_finishes):
    """Pages 138-140 of book 567: a footnote runs over onto the next page.

    Note 258's text does not fit under the page that prints its number, so its last
    line is set at the top of the next page's footnote zone with no number in front
    of it. The footnote zone is found by where the small type starts, and that line
    is above it, so it is read as the last paragraph of the body -- which puts a
    bibliographic citation in the middle of the chapter and stands between the two
    halves of the sentence the page turn split.

    ``note_finishes`` writes the control: a note that ends its own sentence on its
    own page has not run over, and the small paragraph after it is a paragraph.
    """
    first = add_page(doc)
    add_running_head(first, "112", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    y = add_body_lines(first, PROSE_LINES[:8])
    add_line_with_marker(first, y, "Sometime during the next decade Firmicus converted.",
                         "258", after=" He")
    # Short enough to be set on one line: this page's note zone is the evidence the
    # next page's unnumbered line is weighed against, so it has to read back whole.
    tail = "On Firmicus' conversion see The Error, trans. Forbes, pp. 7-8."
    if not note_finishes:
        tail = "On Firmicus' conversion see The Error, p. 7. The statement read as"
    add_notes(first, [(257, "Mommsen, \"Firmicus Maternus,\" p. 468."), (258, tail)])

    second = add_page(doc)
    add_running_head(second, "113", "FIRMICUS MATERNUS")
    y = add_body_lines(second, PROSE_LINES[8:14])
    y = add_body_lines(second, [
        "One example of this is Firmicus' different treatment of Porphyry between",
        "the two works, and it provides some interesting insight into the",
        "social climate of astrology during"], top=y + BODY_LEADING)
    _put(second, LEFT, NOTE_TOP - 40, "occurs in The Error, 8: 4, although it is not "
         "terribly overt.", size=NOTE_TEXT_SIZE)
    add_notes(second, [(259, "See Porphyry, Porphyry Against the Christians, trans. "
                             "Berchman.")])

    third = add_page(doc)
    add_running_head(third, "114", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    add_body_lines(third, [
        "the rise of Christianity in the fourth century, and how quickly views",
        "began to change, sometimes even within a single lifetime."] + PROSE_LINES[:6])
    return [first, second, third]


def runover_footnote_pages(doc):
    """The note stops mid-sentence on its own page and finishes on the next."""
    return _runover_pages(doc, note_finishes=False)


def finished_footnote_pages(doc):
    """The control: the note finishes its sentence, so nothing ran over."""
    return _runover_pages(doc, note_finishes=True)


def out_of_order_glyph_marker_page(doc):
    """Page 117 of book 567: one glyph reading fits the page's order and one does not.

    Two of this page's markers came back as punctuation. ``Anthology.\'\'s`` reads
    as 115, one substitution from the 135 this page prints and leaves unreferenced,
    and 135 is the note the sentence cites. ``a teacher he found in Egypt."\'`` reads
    as 111, one substitution from 141 -- but the markers either side of it are 135
    and 138, and the note about the teacher in Egypt is 136. Binding it to 141
    prints a citation the book does not make, and hides the fact that the page is
    missing a marker.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "of the same topic in book 4 of the Anthology.\'\'s While Valens does")
    y += BODY_LEADING
    _put(page, LEFT, y, "mention Critodemus as having dealt with profections, he does not")
    y += BODY_LEADING
    _put(page, LEFT, y, "otherwise say that he acquired the profections material from him,")
    y += BODY_LEADING
    _put(page, LEFT, y, "but instead that it was taught by a teacher he found in Egypt.\"\' Riley")
    y += BODY_LEADING
    _put(page, LEFT, y, "suspects that Valens took more from Critodemus than he says.")
    y += BODY_LEADING
    y = add_line_with_marker(page, y, "Abraham is another early author named by Firmicus.", "138",
                             after=" There he")
    _put(page, LEFT, y, "is listed as if he were a contemporary of Critodemus, which reads")
    y += BODY_LEADING
    y = add_line_with_marker(page, y, "as another instance of pseudepigrapha in the same lineage.", "140")
    add_notes(page, [(135, "Valens, Anthology, 4, 11: 8."),
                     (136, "On Valens being taught profections by a teacher in Egypt."),
                     (137, "Riley, A Survey of Vettius Valens, p. 12."),
                     (138, "Firmicus, Mathesis, 4, proem: 5."),
                     (139, "Pingree, From Astral Omens, p. 26."),
                     (140, "Valens, Anthology, 4, 12: 1."),
                     (141, "Neugebauer and van Hoesen, Greek Horoscopes, p. 176.")])
    return page


def two_damaged_note_numbers_page(doc):
    """Page 115 of book 567: two note numbers in a row lost a digit.

    The zone reads ``117, 18, 1, 120`` -- 118 lost its leading digit to the note
    above it and 119 lost two. Neither is readable on its own; together they are,
    because the gap their undamaged neighbours leave holds exactly two numbers and
    the body marks both.
    """
    page = add_page(doc)
    add_running_head(page, "115", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    y = add_body_lines(page, PROSE_LINES[:5])
    y = add_line_with_marker(page, y, "the surviving evidence", "118", " is thin")
    y = add_line_with_marker(page, y, "as Pingree notes", "119", " elsewhere")
    add_notes(page, [(117, "Neugebauer, A History of Ancient Mathematical Astronomy."),
                     (18, "Pingree, The Yavanajataka of Sphujidhvaja, p. 195."),
                     (1, "Jones, Astronomical Papyri from Oxyrhynchus, p. 12."),
                     (120, "Barton, Ancient Astrology, p. 31.")])
    return page


def note_number_read_too_high_page(doc):
    """Page 117 of book 567: the damaged numbers read HIGHER than the true ones.

    The zone reads ``27, 28, 29, 38, 39, 32, 33, 34``. Reading it left to right
    blames the four numbers after 39; the page is telling the opposite story,
    because the longest ascending run through it is ``27, 28, 29, 32, 33, 34`` and
    the two that break it are 30 and 31 read with a damaged first digit.
    """
    page = add_page(doc)
    add_running_head(page, "117", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    y = add_body_lines(page, PROSE_LINES[:4])
    y = add_line_with_marker(page, y, "his own horoscope", "30", " is preserved")
    y = add_line_with_marker(page, y, "a later compiler", "31", " repeats it")
    add_notes(page, [(27, "Valens, Anthologies, 2: 21."),
                     (28, "Valens, Anthologies, 3: 11."),
                     (29, "Riley, A Survey of Vettius Valens, p. 4."),
                     (38, "Neugebauer and Van Hoesen, Greek Horoscopes, p. 110."),
                     (39, "Pingree, From Astral Omens to Astrology, p. 26."),
                     (32, "Barton, Ancient Astrology, p. 33."),
                     (33, "Cramer, Astrology in Roman Law, p. 58."),
                     (34, "Rochberg, The Heavenly Writing, p. 44.")])
    return page


def damaged_first_note_pages(doc):
    """Two facing pages: the damage is the FIRST note of the second one.

    ``115`` came back as ``1`` at the top of its own zone, so on that page alone
    there is no earlier number for it to fail to ascend from. The page before is
    what makes it damage.
    """
    first = add_page(doc)
    add_running_head(first, "113", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    y = add_body_lines(first, PROSE_LINES[:4])
    y = add_line_with_marker(first, y, "the Anthologies", "113", " survive")
    y = add_line_with_marker(first, y, "in several recensions", "114", " of it")
    add_notes(first, [(113, "Riley, A Survey of Vettius Valens, p. 2."),
                      (114, "Riley, A Survey of Vettius Valens, p. 3.")])

    second = add_page(doc)
    add_running_head(second, "114", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    y = add_body_lines(second, PROSE_LINES[:4])
    y = add_line_with_marker(second, y, "the later tradition", "115", " knows them")
    y = add_line_with_marker(second, y, "and Rhetorius", "116", " quotes them")
    y = add_line_with_marker(second, y, "as does Olympiodorus", "117", " after him")
    add_notes(second, [(1, "Pingree, ed., Rhetorii Aegyptii Capitula, p. 8."),
                       (116, "Rhetorius, Compendium, 5: 57."),
                       (117, "Olympiodorus, Commentary, p. 41.")])
    return second


def restarting_note_numbers_pages(doc):
    """The control for the ascending-run repair: a book whose notes restart.

    Two chapters, each numbering its notes from 1. Nothing here is damaged, and a
    converter that reads the restart as damage renumbers a whole chapter's
    citations.
    """
    pages = []
    for chapter, folio in (("2", "40"), ("3", "41")):
        page = add_page(doc)
        add_running_head(page, folio, "CHAPTER %s: ORIGINS OF HELLENISTIC ASTROLOGY"
                         % chapter)
        y = add_body_lines(page, PROSE_LINES[:4])
        for digits in ("1", "2", "3"):
            y = add_line_with_marker(page, y, "the point is made", digits, " again")
        add_notes(page, [(1, "Rochberg, The Heavenly Writing, p. 44."),
                         (2, "Cramer, Astrology in Roman Law, p. 58."),
                         (3, "Barton, Ancient Astrology, p. 31.")])
        pages.append(page)
    return pages


def chapter_restart_glyph_marker_page(doc):
    """Page 380 of book 567: the notes restart, and one marker came back damaged.

    The last citation of one chapter stands in front of the first of the next, so
    the marker for 111 is printed ahead of the marker for 1 and the page's numbers
    descend. That descent is the book's numbering, not damage, and it says nothing
    about where 111 belongs: ``derived places."\'`` is still the marker for 111.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "Valens gives this list at one point in the section on places.\"\' At")
    y += BODY_LEADING
    _put(page, LEFT, y, "one point he adds a further pair of places to the same scheme,")
    y += BODY_LEADING
    _put(page, LEFT, y, "and the chapter ends with the list of what each one governs.")
    y += BODY_LEADING
    y = add_line_with_marker(page, y, "The word refers to the actualizations of the soul.", "1",
                             after=" By")
    _put(page, LEFT, y, "extension it can also mean sharing, generosity, or charity.")
    add_notes(page, [(110, "Valens, Anthology, 4, 12: 1."),
                     (111, "Valens, Anthology, 4, 25: 9."),
                     (1, "Schmidt, Definitions and Foundations, p. 180."),
                     (2, "Riley, A Survey of Vettius Valens, p. 12.")])
    return page


def marker_for_a_missing_note_page(doc):
    """Page 104 of book 567: the body marks 59 and the note zone has no 59.

    Its text was swept into note 58 above it. The number in the body is undamaged
    and means what it says; binding it to 58 because 58 is one digit away prints a
    citation the book does not make.
    """
    page = add_page(doc)
    add_running_head(page, "104", "CHAPTER 3: THE EARLY HELLENISTIC SOURCES")
    y = add_body_lines(page, PROSE_LINES[:4])
    add_body_lines(page, ["the rise of Christianity in the fourth century.59",
                          "was already an old argument by then"], top=y)
    add_notes(page, [(58, "Barton, Ancient Astrology, p. 31. Cramer, Astrology in "
                          "Roman Law, p. 58."),
                     (60, "Pingree, From Astral Omens to Astrology, p. 26."),
                     (61, "Jones, Astronomical Papyri from Oxyrhynchus, p. 12.")])
    return page


def glyph_numbered_note_page(doc):
    """Page 109 of book 567: the footnote's OWN number came back as letters.

    The zone opens ``9° Tarrant, Thrasyllan Platonism, p. 10`` -- note 90, whose
    number the scanner read as a nine and a degree sign. Nothing recognises that as
    a number, so the note is not a note: its text is printed as a stray paragraph in
    the middle of the body, note 90 does not exist, and the marker in the body that
    points at it has nothing to bind to.
    """
    page = add_page(doc)
    add_running_head(page, "82", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    y = add_body_lines(page, PROSE_LINES[:5])
    add_body_lines(page, ["choosing to suspend judgment on whether the two are related.9°",
                          "I tend to side with those who argue that Balbillus was one"],
                   top=y)
    add_notes(page, [("9°", "Tarrant, Thrasyllan Platonism, p. 10."),
                     (91, "Cramer explored the potential lineage of the family."),
                     (92, "Cramer, Astrology in Roman Law and Politics, p. 108.")])
    return page


def lowercase_word_in_the_note_zone_page(doc):
    """The control. A note that ran over from the page before opens the zone with an
    ordinary word, and ``so`` is two glyphs a digit is mistaken for -- 5 and 0. A
    reader that takes it for note 50 has invented a footnote out of a sentence."""
    page = add_page(doc)
    add_running_head(page, "83", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    y = add_body_lines(page, PROSE_LINES[:5])
    add_notes(page, [("so", "Tarrant argues, was already an old position by then."),
                     (91, "Cramer explored the potential lineage of the family.")])
    return page


def degree_in_the_note_zone_page(doc):
    """Book 569 page 355's shape: an astrological degree in body prose, set at
    note size below the zone line. ``24° Sagittarius.`` reads to the glyph rule
    as note 240, and every body line under it -- plus the real note at the
    bottom -- becomes footnote text. A degree is not a note number: the word
    after it ends its own sentence, and a citation does not do that."""
    page = add_page(doc)
    add_running_head(page, "324", "CHAPTER 28")
    y = add_body_lines(page, PROSE_LINES[:4])
    y = add_body_lines(page, [
        "Saturn at 24° Sagittarius. Because it will not make any more",
    ], top=max(y, NOTE_TOP - 40.0), size=NOTE_TEXT_SIZE)
    add_body_lines(page, [
        "24° Sagittarius. Once it enters the next sign, it has until",
        "2° Taurus to make an applying aspect to another planet here.",
    ], top=y + 8.0, size=NOTE_TEXT_SIZE)
    add_notes(page, [(10, "Ibn Sahl, The Fifty Judgments 6.")])
    return page


def glyph_note_number_page(doc):
    """Page 119 of book 567: a note whose own printed number the scan read as letters.

    The number is set two points under the note's text, and the scanner returned it
    inside the text span rather than as a raised number of its own: the line arrives
    as ``Is' Pingree, Yavanajataka, vol. 2, p. 445.`` -- the two 1s of 151 read as an
    I and an apostrophe, the 5 as an s. The number is still recoverable, because that
    is what those glyphs spell; what is left over is the note's own number printed a
    second time at the head of its text, where it is neither a citation nor a word.
    """
    page = add_page(doc)
    add_running_head(page, "102", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    y = add_body_lines(page, PROSE_LINES[:5])
    y = add_line_with_marker(page, y, "Bidez and Cumont originally argued", "150",
                             " that the Maguseans", superscript=True)
    add_body_lines(page, ["were responsible for transmitting some forms of the"], top=y)
    y = add_notes(page, [(150, "Les Mages Hellenises, ed. Bidez and Cumont, pp. 56-84.")])
    _put(page, LEFT + 4, y, "Is' Pingree, Yavanajataka, vol. 2, p. 445.",
         size=NOTE_TEXT_SIZE)
    return page


def split_glyph_note_number_page(doc):
    """Page 110 of book 567: the scan split the note's number and mangled the rest.

    ``105`` came back as a raised ``1`` and a full-size ``"``. Two digits are gone
    and what is left does not spell the number: the number is repaired from the notes
    around it, and the residue is not proof of anything. It stays in the note's text
    where a model can see what the page printed.
    """
    page = add_page(doc)
    add_running_head(page, "93", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    y = add_body_lines(page, PROSE_LINES[:5])
    y = add_line_with_marker(page, y, "his intellectual background is", "105",
                             " set out by Volk", superscript=True)
    add_body_lines(page, ["in a study of the poem that remains the standard one."], top=y)
    y = add_notes(page, [(104, "Manilius, Astronomica, 4: 294-407.")])
    x = LEFT + 4
    x += _put(page, x, y, "1", size=NOTE_NUM_SIZE)
    _put(page, x + 2, y, '" Volk, Manilius and His Background, p. 48f.',
         size=NOTE_TEXT_SIZE)
    add_notes(page, [(106, "Pingree, Review of Manilius, p. 263.")], top=y + 12.0)
    return page


def note_opening_with_a_letter_word_page(doc):
    """Page 63 of book 567: a note that opens with the word ``I``.

    One letter, and it reads as a 1 -- the control for every repair here that reads
    letters as digits. The note's own number is printed and undamaged, so the word
    behind it is the note's first word and nothing may take it for part of a number.
    """
    page = add_page(doc)
    add_running_head(page, "46", "CHAPTER 3: THE EARLY HELLENISTIC TRADITION")
    y = add_body_lines(page, PROSE_LINES[:5])
    y = add_line_with_marker(page, y, "the evidence for this lineage", "78",
                             " is set out below", superscript=True)
    add_body_lines(page, ["and rests on a passage that has not been read this way."],
                   top=y)
    add_merged_notes(page, [
        (78, "I believe that we can find further evidence for this in the Anthology."),
        (79, "Cramer, Astrology in Roman Law and Politics, p. 108."),
    ])
    return page


def ocr_size_drift_page(doc):
    """A paragraph of ordinary prose whose scanner set one line a hair larger.

    MEASURED on book 567, PDF page 112: the body of that page comes back between
    11.0 and 11.7pt, and one line of a running paragraph --

        size=12.00 font=Times-Roman flags=4
        'Schmidt published an attempt to reconstruct the original definitions o'

    -- comes back at 12.00pt in the same roman face, opening its own block over a
    line that starts a new sentence. Nothing on the printed page says heading; only
    the measurement drifted. The book's own heading rung sits at 12.96pt.
    """
    page = add_page(doc)
    add_running_head(page, "86", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    y = add_body_lines(page, PROSE_LINES[:4])
    _put(page, LEFT, y + BODY_LEADING,
         "Schmidt published an attempt to reconstruct the original",
         size=BODY_SIZE * 1.062)
    add_body_lines(page, [
        "Antiochus definitions from the fragments that survive in the later",
        "compilations, and that reconstruction is what is quoted here.",
    ], top=y + 2 * BODY_LEADING)
    return page


def roman_heading_on_the_ladder_page(doc, title="The Hellenistic Period", folio="88"):
    """The control for :func:`ocr_size_drift_page`: a heading in the body's own roman
    face, set at the size the book uses for its section heads, over a new sentence."""
    page = add_page(doc)
    add_running_head(page, folio, "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    _put(page, LEFT, BODY_TOP, title, size=HEAD_SIZE)
    add_body_lines(page, PROSE_LINES[:6], top=BODY_TOP + BODY_LEADING)
    return page


def flat_run_in_heading_page(doc):
    """A run-in heading with no size signal at all: bold, at the body's own size.

    MEASURED on book 567 the Serapio head is 12.92pt over an 11.3pt body -- a signal,
    but a small one, and the same scanner that drifts a body line up to 12.00pt can
    just as easily set a real head at the body's size. What is never in doubt is the
    weight.
    """
    page = add_page(doc)
    add_running_head(page, "94", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    _put(page, LEFT, BODY_TOP, "Serapio of Alexandria (First Century CE?)",
         size=BODY_SIZE, font=_BOLD)
    add_body_lines(page, [
        "Serapio of Alexandria was an astrologer who wrote on inceptional",
        "astrology and possibly other topics, although only fragments of",
        "his work survive in the later compilations.",
    ], top=BODY_TOP + BODY_LEADING)
    return page


def run_in_heading_the_scanner_measured_small_page(doc):
    """The same head as :func:`flat_run_in_heading_page`, measured a little *under*
    the body rather than at it.

    A scan's measurements wobble in both directions and the wobble is the whole
    reason ``LADDER_TOL`` exists. MEASURED on book 567: the book's bold section heads
    come back anywhere between 10.8 and 11.5pt against an 11.3pt body -- one printed
    rung, read as two -- so a rule that takes the ones the scanner rounded up and
    refuses the ones it rounded down splits that rung down its middle. 92 bold
    heading lines in that book are refused this way; 38 of them sit in the band this
    page is set in.
    """
    page = add_page(doc)
    add_running_head(page, "94", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    _put(page, LEFT, BODY_TOP, "Serapio of Alexandria (First Century CE?)",
         size=round(BODY_SIZE * 0.97, 2), font=_BOLD)
    add_body_lines(page, [
        "Serapio of Alexandria was an astrologer who wrote on inceptional",
        "astrology and possibly other topics, although only fragments of",
        "his work survive in the later compilations.",
    ], top=BODY_TOP + BODY_LEADING)
    return page


def bold_line_far_below_the_body_page(doc):
    """The floor. Bold type set far smaller than the body is a different rung, not a
    section head measured badly: in book 567 that band holds the example-chart labels
    and the note face, and promoting it would put 50 chart labels in the heading
    ladder. Set here at 0.78 of the body, where those labels measure.
    """
    page = add_page(doc)
    add_running_head(page, "94", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    _put(page, LEFT, BODY_TOP, "Serapio of Alexandria (First Century CE?)",
         size=round(BODY_SIZE * 0.78, 2), font=_BOLD)
    add_body_lines(page, [
        "Serapio of Alexandria was an astrologer who wrote on inceptional",
        "astrology and possibly other topics, although only fragments of",
        "his work survive in the later compilations.",
    ], top=BODY_TOP + BODY_LEADING)
    return page


def _drifted_line_page(doc, drifted, following, folio):
    """A page whose first line of a block the scan measured up to heading size.

    The size reaches the book's own section-head rung, so nothing about how the line
    is set says it is prose. Only what it says does.
    """
    page = add_page(doc)
    add_running_head(page, folio, "CHAPTER 12: THE PLANETS")
    y = add_body_lines(page, PROSE_LINES[:4])
    _put(page, LEFT, y + BODY_LEADING, drifted, size=HEAD_SIZE)
    add_body_lines(page, following, top=y + 2 * BODY_LEADING)
    return page


def heading_that_starts_mid_sentence_page(doc):
    """MEASURED, book 567 PDF page 493: ``bonify Mercury. Conversely, if`` came back
    at 12.30pt in the body's roman face and became an ``<h2>`` -- which splits the
    chapter there and puts the fragment in the reader's table of contents."""
    return _drifted_line_page(
        doc, "bonify Mercury. Conversely, if",
        ["Saturn is in aversion to the sign it rules then the same",
         "configuration is read the other way about."],
        folio="404")


def heading_that_stops_on_a_function_word_page(doc):
    """MEASURED, book 567 PDF page 527: ``Capricorn, Mars and Saturn in`` came back at
    12.50pt in the body's roman face. It ends on a word no title ends on."""
    return _drifted_line_page(
        doc, "Capricorn, Mars and Saturn in",
        ["Aquarius are both said to be in signs of their own sect,",
         "which is the condition the older authorities ask for."],
        folio="438")


def line_that_ends_in_sentence_punctuation_page(doc):
    """MEASURED, book 567 PDF page 486 (index 485): ``The native was the son of U.S.``
    came back at heading size in the body's roman face. It begins like a title, ends
    on a content word, and is short -- every shape rule but one says heading. The one
    that does not is that it ends where a sentence ends."""
    return _drifted_line_page(
        doc, "The native was the son of U.S.",
        ["Senator William Fulbright, and the chart was cast for a",
         "birth in Arkansas in the spring of 1944."],
        folio="401")


def long_heading_with_function_words_page(doc, folio="440"):
    """The control: a real section head that uses those same words inside it."""
    page = add_page(doc)
    add_running_head(page, folio, "CHAPTER 12: THE PLANETS")
    _put(page, LEFT, BODY_TOP, "The Midheaven and the Three Forms of House Division",
         size=HEAD_SIZE)
    add_body_lines(page, PROSE_LINES[:6], top=BODY_TOP + BODY_LEADING)
    return page


def run_in_heading_with_scan_jitter_page(doc):
    """The same run-in head as :func:`defect_a_page`, set the way a scan sets it.

    MEASURED on book 567 page 121 (index 120): the heading line starts at x=53.94
    and the body lines under it start anywhere between 53.65 and 54.25, so the text
    block's own left edge is *further left* than the heading that opens it. Both
    share the block's top, because the heading is the block's first line.
    """
    page = add_page(doc)
    add_running_head(page, "94", "CHAPTER 4: THE HELLENISTIC ASTROLOGERS")
    _put(page, LEFT + 0.29, BODY_TOP, "Serapio of Alexandria (First Century CE?)",
         size=HEAD_SIZE, font=_BOLD)
    y = BODY_TOP + BODY_LEADING
    for drift, line in zip((0.0, -0.35, 0.24),
                           ["Serapio of Alexandria was an astrologer who wrote on",
                            "inceptional astrology and possibly other topics, although",
                            "only fragments of his work survive."]):
        _put(page, LEFT + drift, y, line, size=BODY_SIZE)
        y += BODY_LEADING
    return page
