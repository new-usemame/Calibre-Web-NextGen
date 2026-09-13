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
