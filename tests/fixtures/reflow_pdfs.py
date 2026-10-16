# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

'Procedural PDF layouts. All printed prose, citations and labels are written for these tests. Font sizes, line breaks, marker damage and page geometry exercise the named layout cases.'

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


#: Dense synthetic prose supplies an ordinary page rather than a thin sample.
#: A seven-line stand-in is a thin page and would be classified as one, correctly —
#: so a fixture that stands in for an ordinary page has to be an ordinary page.
PROSE_LINES = [
    'Every shelf in this imaginary workshop holds a labeled wooden',
    'box, and each box contains the small tools for a different task.',
    'The labels are written in dark ink so that a reader can find the',
    'right box without opening all the others on a crowded morning.',
    'A broad table holds a plain drawing where a new task begins.',
    'A new exercise begins beside the wooden bench and cotton cloth.',
    'There are no valuable objects here; this is an invented setting.',
    'At the start of each exercise we place the empty tray on the desk',
    'and count the wooden pieces before sorting them into even rows.',
    'One row contains short blocks, another holds round disks, and a',
    'third is reserved for the narrow strips used to mark the edges.',
    'When a piece belongs in two groups, we write a separate label on',
    'a fresh card and put that card beside it. The piece stays still',
    'until the next exercise calls for a different arrangement of it.',
    'The instructions on the wall explain how the tables are cleared',
    'after each ordinary daily workshop exercise. First the loose paper goes into a folder,',
    'then the cloth is folded again and the tools return to the shelf.',
    'A small brush removes the dust from the corners of the table and',
    'a clean sheet is placed under the tray for the following morning.',
    'Nothing in this scene describes an actual workshop or its owner.',
    'Its only purpose is to fill a page with ordinary connected words',
    'while the test measures the shape of the lines that contain them.',
    'Some lines run farther across the sample paper, resembling those inside printed manuals',
    'manual, and a sentence may continue after the edge of the page.',
    'The next page can begin in the middle of that sentence, allowing',
    'a reader to follow the same thought across the paper boundary.',
    'At last the tray is empty and the exercise is ready to repeat',
    'with all the labels, tools, cards, and wooden pieces in place.',
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
    """Synthetic case: A bold heading set at body leading, immediately followed by its paragraph.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
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
    """Synthetic case: Footnotes whose raised number the OCR could not keep apart from the text.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
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
    add_running_head(page, folio, "CHAPTER 2: ORIGINS OF SYNTHETIC TYPOGRAPHY")
    _put(page, LEFT, BODY_TOP, title, size=size, font=_BOLD)
    add_body_lines(page, PROSE_LINES[:12], top=BODY_TOP + 24.0)
    return page


def section_heading_page(doc, title, folio="32", size=13.0):
    """A section heading: the next step down the ladder, used far more often."""
    page = add_page(doc)
    add_running_head(page, folio, "CHAPTER 2: ORIGINS OF SYNTHETIC TYPOGRAPHY")
    _put(page, LEFT, BODY_TOP, title, size=size, font=_BOLD)
    add_body_lines(page, PROSE_LINES[:12], top=BODY_TOP + 20.0)
    return page


def title_page(doc):
    """The one page in a book set in display type, and the one that must not be
    allowed to define the heading ladder: nothing on it is a heading level."""
    page = add_page(doc)
    add_display_line(page, "SYNTHETIC TYPOGRAPHY", y=200.0, size=22.7)
    add_display_line(page, "A Study of Printed Page Shapes", y=240.0, size=21.0)
    add_display_line(page, "TEST AUTHOR", y=320.0, size=13.8)
    return page


def merged_note_number_page(doc):
    """Synthetic case: three footnotes, one of whose numbers merged into text.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "20", "CHAPTER 2: ORIGINS OF SYNTHETIC TYPOGRAPHY")
    y = add_body_lines(page, PROSE_LINES[:8])
    y = add_line_with_marker(page, y, "the tray is assembled by Register", "24",
                             ' and placed near', superscript=True)
    add_merged_notes(page, [
        (24, "Workshop Register, Index of Repairs, 17: 112, trans. Editor."),
        (25, "Manual, Labeling in Wooden Boxes, p. 58."),
    ])
    return page


def broken_note_number_page(doc):
    """Synthetic case: the note numbered 10 came back from the OCR as ``1``.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "3", "CHAPTER 1: LETTERING ON WOODEN BOXES AND TRAYS")
    y = add_body_lines(page, PROSE_LINES[:6])
    y = add_line_with_marker(page, y, "the drawers were repainted", "10",
                             " beside the window", superscript=True)
    add_notes(page, [(9, "Notebook, The Painted Lettering, p. 44."),
                     (1, "Record, A Wooden Sample Cabinet, p. 12."),
                     (11, "Ledger, From Loose Cards to Ledgers, p. 26.")])
    return page


def ambiguous_note_number_page(doc):
    """The same damage with a wider gap: 9, then ``1``, then 13. The broken number
    could be 10, 11 or 12 and nothing on the page decides between them."""
    page = add_page(doc)
    add_running_head(page, "4", "CHAPTER 1: LETTERING ON WOODEN BOXES AND TRAYS")
    add_body_lines(page, PROSE_LINES[:6])
    add_notes(page, [(9, "Notebook, The Painted Lettering, p. 44."),
                     (1, "Record, A Wooden Sample Cabinet, p. 12."),
                     (13, "Ledger, From Loose Cards to Ledgers, p. 26.")])
    return page


def defect_a_page(doc):
    """Synthetic case: the ``Workshop of Birchwood`` run-in heading.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "94", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    add_run_in_heading(
        page,
        "Workshop of Birchwood (First Edition AB?)",
        ["Workshop of Birchwood was a workshop that kept notes on",
         "the labeling of wooden boxes and their lids, although",
         "only drawings of the first cabinet remain."],
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
    add_running_head(page, "94", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    y = add_run_in_heading(
        page,
        "Workshop of Birchwood (First Edition AB?)",
        ["Workshop of Birchwood was a workshop that kept notes on",
         "the labeling of wooden boxes and their lids, although"],
        top=BODY_TOP,
    )
    _put(page, LEFT, y, "only drawings of the first cabinet remain.\" Ledger agrees.")
    y += BODY_LEADING
    _put(page, LEFT, y, 'Archive describes a small wooden cupboard." Binder agrees.')
    add_notes(page, [(88, "Ledger, Fixturebook, vol. 2, pp. 440-441.")])
    return page


def defect_b_pages(doc):
    """Synthetic case: A paragraph broken mid-sentence at a page turn, with footnotes between.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    tail = add_page(doc)
    add_running_head(tail, "95", "FOLDER (FIRST EDITION AB?)")
    y = add_body_lines(tail, [
        'inside Toolbook, every sample carries a painted label beside',
        'its smooth wooden handle, resting upon fresh folded cotton covers.',
    ])
    add_line_with_marker(tail, y, 'Before storage, this blue cabinet needs', "166",
                         " sanded lids", superscript=True)
    add_notes(tail, [(160, "Measured together with Ledger, Fixturebook, vol. 2, p. 441."),
                     (161, "Catalogue, Repairbook, 2, 2: 11-18."),
                     (166, 'Folder, Workshop Verse, ed. Layout, pp. 23-37.')])

    head = add_page(doc)
    add_running_head(head, "96", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    add_body_lines(head, [
        'are packed neatly beside clean narrow drawers, then carefully checked',
        'with every freshly folded cotton cover inside that sample cabinet.',
    ])
    return tail, head


def defect_c_page(doc):
    """Synthetic case: The three OCR marker-damage modes, as measured, on one page.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "95", "FOLDER (FIRST EDITION AB?)")
    y = BODY_TOP
    y = add_line_with_marker(page, y, "before samples reached their blue cabinet AB.'",
                             "56", " Ledger notes that", superscript=True)
    x = LEFT
    x += _put(page, x, y, "the blue drawer must be kept beneath canvas.")
    x += add_marker(page, x, y, "16", superscript=False)
    _put(page, x, y, "° Every blue sample cabinet has a fresh paper card describing")
    y += BODY_LEADING
    _put(page, LEFT, y, "the wooden lids of the sample boxes.'\" Checklist cited him")
    y += BODY_LEADING
    add_notes(page, [(156, "Binder stored these measured drawings in LOGS 8, 4, p. 225."),
                     (160, "Measured together with Ledger, Fixturebook, vol. 2, p. 441."),
                     (161, "Catalogue, Repairbook, 2, 2: 11-18.")])
    return page


def numbered_bibliography_page(doc):
    """Synthetic case: Bold-numbered endnote entries remain a bibliography.

    Text is invented for this regression; geometry exercises the stated layout."""
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "15.", size=BODY_SIZE, font=_BOLD)
    y += BODY_LEADING
    for line in ['Fixture Manual, Synthetic Typography, p. 15. Then inspect our',
                 "drawings inside Archive, Notes, pp. 44-48."]:
        _put(page, LEFT, y, line, size=BODY_SIZE)
        y += BODY_LEADING
    y += 6

    x = LEFT
    x += _put(page, x, y, "70. ", size=BODY_SIZE, font=_BOLD)
    _put(page, x, y, "Ledger, Fixturebook, vol. 2, p. 441, noted the same.")
    y += BODY_LEADING
    _put(page, LEFT, y, "Compare Inventory, Repair Catalog, pp. 613-618.")
    return page


def lead_in_page(doc):
    """Synthetic case: A bold small-caps lead-in whose sentence carries on in lowercase.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "THIS IS A SIMPLE WORKSHOP EXERCISE", size=BODY_SIZE, font=_BOLD)
    y += BODY_LEADING
    for line in ['to explain how wooden trays can be placed together',
                 'on a broad bench with a little room between them.']:
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
    _put(page, LEFT, y, "THIS IS A SIMPLE WORKSHOP EXERCISE", size=BODY_SIZE, font=_BOLD)
    y += BODY_LEADING
    _put(page, LEFT, y, "to explain how these wooden trays are arranged.'ts")
    y += BODY_LEADING
    _put(page, LEFT, y, "The sample uses only words invented for this exercise.")
    add_notes(page, [(135, "Folderbook, Handbook, 4, 11, ed. Ledger, p. 165.")])
    return page


def degree_control_page(doc):
    """Synthetic case: Genuine ``N°`` degree tokens, and a note that is properly marked.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, 'the blue dial reads 16° on its rim and the red dial reads 45°,')
    y += BODY_LEADING
    y = add_line_with_marker(page, y, 'which the sample card describes as a two-dial setting.', "160",
                             " Checklist agrees.", superscript=True)
    add_notes(page, [(160, "Folderbook, Handbook, 2, 17, ed. Ledger, p. 71.")])
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
    y = add_line_with_marker(page, y, "we labeled it the carpenters'", "61",
                             ' and put it away.', superscript=True)
    add_line_with_marker(page, y, 'the sample card reads "keep this lid closed.\'', "62",
                         ' and packed.', superscript=True)
    add_notes(page, [(61, "Archive, Notes, p. 12."),
                     (62, "Ledger, From Loose Cards, p. 40.")])
    return page


def ambiguous_residue_page(doc):
    """Two quotation residues and one unmarked note: the counts disagree.

    Pairing them off anyway binds the note to whichever quotation came first, which
    is a coin flip printed into the reader's book. The page goes to the model
    instead.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, 'Folderbook labels it \'the blue wooden tray.\'" Checklist agrees.')
    y += BODY_LEADING
    _put(page, LEFT, y, 'Workbook calls it \'the tall empty shelf.\'" Directory agrees.')
    add_notes(page, [(88, "Folderbook, Handbook, 4, 4, ed. Ledger, p. 155.")])
    return page


def wrecked_marker_page(doc):
    """Synthetic case: A superscript the scanner read as digits, and the wrong digits at that.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "The sketch shows the lower drawer beside the bench.'ts")
    y += BODY_LEADING
    _put(page, LEFT, y, 'While Folderbook includes a red tray, its drawing omits the lid.')
    add_notes(page, [(135, "Folderbook, Handbook, 4, 11, ed. Ledger, p. 165.")])
    return page


def swept_note_page(doc):
    """Synthetic case: A note whose own printed number the scanner read as punctuation.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, 'Folderbook labels the narrow drawer." Checklist repeats it.')
    y += BODY_LEADING
    # Constructed damage: 59's body marker remains fused onto the word in
    # front of it, and only the note's own number under the rule was lost.
    _put(page, LEFT, y, 'Workbook puts the matching tray beside the empty shelf.59')
    add_notes(page, [
        (58, 'Binder, Woodcraft, p. 76. " Traybook, Workshop History, 2, 6: 38.'),
        (60, "Tablebook, Workshop Labels, p. 12."),
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
    _put(page, LEFT, y, 'Folderbook labels the narrow drawer." Checklist repeats it.')
    add_notes(page, [
        (58, 'Binder, Woodcraft, p. 76. " Traybook, Workshop History, 2, 6: 38. '
             '" Workbook, Summary, p. 116: 3-12.'),
        (61, "Tablebook, Workshop Labels, p. 12."),
    ])
    return page


def shortened_note_number_page(doc):
    """Synthetic case: The other control: the missing number is printed, one digit short.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    y = add_line_with_marker(page, BODY_TOP, 'Folderbook labels the narrow drawer.', "29")
    _put(page, LEFT, y, 'Checklist puts another tray on the shelf above it.')
    add_notes(page, [
        (29, "Ledger, From Loose Cards, p. 41."),
        (3, "Ledger, \"The Workshop's (?) Revised Drawing of the Cabinet.\""),
        (31, 'Fixture Guide, "A Diagram of Wooden Trays," p. 667.'),
    ])
    return page


def quietly_swept_note_page(doc):
    """Synthetic case: The same damage on a page where nothing else is wrong.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    y = add_line_with_marker(
        page, BODY_TOP, 'Workbook contains a diagram of the narrow shelves.', "124")
    add_line_with_marker(
        page, y, 'Repairlog shows a semi-finished cupboard with three drawers.', "126")
    add_notes(page, [
        (124, 'Ledger, "Workbook and Checklist," p. 207. Its LOGS 8, 3, p. 116: 3-12.'),
        (126, "Traybook, Workshop History, 2, 6: 38."),
    ])
    return page


def chart_label_page(doc):
    """Synthetic case: Sparse diagram legend glyphs are not headings.

    Text is invented for this regression; geometry exercises the stated layout."""
    page = add_page(doc)
    _put(page, LEFT, BODY_TOP, "BOX PANEL o 7 -3 z", size=16.0, font=_BOLD)
    y = BODY_TOP + BODY_LEADING
    for line in ["The panel above is an invented example for describing",
                 "the arrangement of labeled blocks inside a wooden box."]:
        _put(page, LEFT, y, line, size=BODY_SIZE)
        y += BODY_LEADING
    return page


def table_column_heads_page(doc):
    """Synthetic case: A table's column heads, set as large as a chapter title, over a real section.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "298", "CHAPTER 8: THE ASSEMBLY OF TRAYS")
    _put(page, LEFT, BODY_TOP, "Box Shelf  L' K/ /  2", size=16.0, font=_BOLD)
    y = add_body_lines(page, PROSE_LINES[:4], top=BODY_TOP + 24.0)
    _put(page, LEFT, y + 10.0, "Workshop's Alternative Three-Shelf Drawer Scheme",
         size=13.0, font=_BOLD)
    add_body_lines(page, PROSE_LINES[4:10], top=y + 32.0)
    return page


def balanced_quotation_page(doc):
    """A closing quotation ``.'"`` on a page whose notes are all marked already.

    The C3 repair pairs leftover quote residue with unmarked notes. With nothing
    unmarked there is nothing to pair, and this punctuation must stay punctuation.
    """
    page = add_page(doc)
    y = BODY_TOP
    y = add_line_with_marker(page, y, 'Folderbook labels the tray \'the blue wooden tray.\'"',
                             "77", " Checklist repeats it.", superscript=True)
    _put(page, LEFT, y, 'The same label appears on a card inside the drawer.')
    add_notes(page, [(77, "Folderbook, Handbook, 4, 4, ed. Ledger, p. 155.")])
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
        "In the sample workshop, a wooden sign advertised a fictional politi-",
        "cal exhibit, and its painted lettering was checked beside the door.",
        "The sign had no owner and named no person who ever existed.",
    ]:
        _put(page, LEFT, y, line, font=_BOLD)
        y += BODY_LEADING
    add_body_lines(page, ["The political label remains only an example string."]
                   + PROSE_LINES[:4], top=y + BODY_LEADING)
    return page


def hyphenated_page(doc):
    """A line-break hyphen that must be repaired, and a compound that must not.

    ``workbench`` is printed whole later in the same paragraph, which is how
    the book's own vocabulary earns the heal: the wrapped form and the compound
    cannot be told apart by shape, only by what this book says.
    """
    page = add_page(doc)
    add_body_lines(page, [
        "the spare drawers remain on the work-",
        "bench beside the painted cabinet, and the Bench-",
        "Top label is written below them. A workbench",
        "is the word used for this invented arrangement.",
    ])
    return page


def hyphenated_multispan_page(doc):
    """Synthetic case: A line-end hyphen with separate word and space spans.

    Text is invented for this regression; geometry exercises the stated layout."""
    page = add_page(doc)
    y = BODY_TOP
    x = LEFT
    for index, word in enumerate(['worked', 'on', 'our', "bolts", 'by',
                                  'our', "workpa-"]):
        x += _put(page, x, y, word, size=BODY_SIZE + (index % 2) * 0.01)
        x += _put(page, x, y, " ", size=BODY_SIZE + 0.03)
    y += BODY_LEADING
    _put(page, LEFT, y, "cket of this drawer, its small wooden label is blue,")
    y += BODY_LEADING
    _put(page, LEFT, y, "and the three-shelf label appears again beside it.")
    return page


def hyphen_heal_needs_a_real_word_pages(doc):
    """A constructed ambiguous hyphen requires an attested complete word.

    ``eighth-`` + ``is`` is a sentence break, not a wrapped word (``eighthis``
    is not a word of this book); ``under-`` + ``standing`` wraps
    ``understanding``, which the book prints whole three lines later. A
    dictionary nobody ships cannot tell these apart; the book's own vocabulary
    can: only the second heals.
    """
    page = add_page(doc)
    add_body_lines(page, [
        "the eighth drawer is where the spare cloth",
        "rests, and every label placed there looks",
        "different. This is the basic understanding",
        "of the example. The eighth-",
        "is empty in the sample. A full under-",
        "standing of the layout follows from it.",
    ])
    return page


def page_turn_compound_hyphen_pages(doc):
    """Synthetic case: An attested compound split across a page break keeps its hyphen.

    Text is invented for this regression; geometry exercises the stated layout."""
    tail = add_page(doc)
    add_body_lines(tail, PROSE_LINES[:4] + [
        "the sample card was labeled bench-"])
    head = add_page(doc)
    add_body_lines(head, ["built as a compound word used in this exercise, and"] + PROSE_LINES[:6])
    return tail, head


def hyphenated_note_page(doc):
    """Synthetic case: A footnote whose own text breaks a word across its two lines.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "478", "CHAPTER 12: THE HANDBOOK OF SAMPLE FOLDERS")
    y = add_body_lines(page, PROSE_LINES[:8])
    add_line_with_marker(page, y, "these labels identify painted timber samples", "31",
                         " for later stacking", superscript=True)
    y = NOTE_TOP
    x = LEFT + 4
    x += _put(page, x, y, "31", size=NOTE_NUM_SIZE)
    _put(page, x + 2, y, "The repair log calls this painted finish a non-",
         size=NOTE_TEXT_SIZE)
    _put(page, LEFT + 4, y + 12.0,
         "standard finish. The label is an invented example for this test.",
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
    """Synthetic case: two logical pages side by side, and behind the     left one the facsimile photograph itself, whose box crosses the gutter.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
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
         'The Arrangement of Wooden Trays Across the Whole Workshop Floor',
         size=HEAD_SIZE, font=_BOLD)
    y = _column_rows(page, [
        ('Blue trays are stacked first.', 'Red trays are stacked second.'),
        ('The small tray holds blue blocks.', 'The large tray holds red blocks.'),
    ], top=BODY_TOP + 24.0)
    _put(page, LEFT, y + 6.0,
         'How the Remaining Drawers Fit Across the Entire Wooden Workshop Wall',
         size=HEAD_SIZE, font=_BOLD)
    _column_rows(page, [
        ('Blue blocks fit in this tray.', 'Red blocks fit in that tray.'),
        ("Maple supports the shelf.", "Beech supports the tray."),
    ], top=y + 30.0)
    return page


def column_continuity_page(doc):
    """A paragraph that crosses the column break mid-sentence is one paragraph."""
    page = add_page(doc)
    _column_rows(page, [
        ('The workshop account runs across the', 'can read both halves of the sample at'),
        ('column boundary beside the blue tray,', 'once. The second column lists'),
        ('and our workshop helper', 'our workshop list.'),
    ])
    return page


CONTINUITY_TEXT = ('The workshop account runs across the column boundary beside the blue tray, and our workshop helper can read both halves of the sample at once. The second column lists our workshop list.')


def below_band_running_head_page(doc):
    """The same below-band head over one plain column: still furniture."""
    page = add_page(doc)
    _put(page, COL_RIGHT, 62.0, "CEDAR - CEDAR 1257", size=9.4)
    add_body_lines(page, PROSE_LINES[:6])
    return page


def below_band_caps_line_without_folio_page(doc):
    """A caps line below the band that carries no folio is content, however
    small and however high on the page."""
    page = add_page(doc)
    _put(page, COL_RIGHT, 62.0, "CEDAR SUPPORTS THE BLUE TRAY", size=9.4)
    add_body_lines(page, PROSE_LINES[:6])
    return page


def below_band_folio_page(doc):
    """Synthetic case: A topmost bare folio below the usual margin band.

    Text is invented for this regression; geometry exercises the stated layout."""
    page = add_page(doc)
    _put(page, COL_RIGHT, 62.0, "516", size=9.4)
    add_body_lines(page, PROSE_LINES[:6])
    return page


def scan_panel_diagram_ocr_leading_page(doc, png_bytes):
    """Synthetic case: A diagram beside a prose panel with loose OCR leading.

    Text is invented for this regression; geometry exercises the stated layout."""
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    add_running_head(page, "516", "CHAPTER 53")
    add_body_lines(page, PROSE_LINES[:3])
    y = BODY_TOP + 5 * BODY_LEADING
    for line in ['The stored drawers support one an-',
                 'other beside the wooden bench. A re-',
                 'lationship between their different sizes',
                 'is shown on a sample card beside the',
                 'door, where the painted labels can be',
                 'read without opening the wooden lids.',
                 'A blue tray holds small blocks and',
                 'a red tray holds longer strips. The',
                 'next shelf has three empty boxes in',
                 'a row below the hanging cotton cloth.',
                 'The boxes have matching lids, which',
                 'can be stacked on the same flat tray.']:
        _put(page, 300.0, y, line, size=7.0)
        y += 12.0
    add_body_lines(page, PROSE_LINES[5:8], top=y + 30.0)
    return page


def column_notes_page(doc):
    """Two prose columns over a footnote zone: the notes stay a side channel."""
    page = add_page(doc)
    x = COL_LEFT
    x += _put(page, x, BODY_TOP, "the tray is assembled by Register")
    add_marker(page, x, BODY_TOP, "24")
    _column_rows(page, [
        (None, "A different argument starts here."),
        ("and painted blue beside the bench.", "This evidence belongs with the next."),
        ("Its second step follows the first.", "The right column adds its point."),
        ("We finish this column before moving.", "Finally the second column concludes."),
    ], top=BODY_TOP + BODY_LEADING)
    add_notes(page, [(24, "Workshop Register, Index of Repairs, 17: 112.")])
    return page


def color_pair_table_page(doc):
    """Synthetic case: a mirror table of color pairs with its finish     column -- five rows, each 'A looks at B' beside 'b faces a' beside the     finish the row belongs to. The row is the unit: column-major prints every     'looks at' away from its 'faces', and a lowercase-driven paragraph join     glues every finish to the NEXT row's colors.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_body_lines(page, PROSE_LINES[:5])
    y = BODY_TOP + 6 * BODY_LEADING
    for left, mid, finish in [
            ("ORANGE looks at RED", 'b l u e faces g r e e n', "Painted"),
            ("YELLOW looks at PURPLE", 'w h i t e faces y e l l o w', "Plain"),
            ("BLACK looks at OLIVE", 'b r o w n faces o r a n g e', "Unpainted"),
            ("MAGENTA looks at INDIGO", 'p u r p l e faces s i l v e r', "Plain"),
            ("VERMILION looks at CHARCOAL",
             'c r i m s o n faces c o p p e r', "Painted")]:
        _put(page, 99.0, y, left, size=6.0)
        _put(page, 215.0, y, mid, size=6.0)
        _put(page, 334.0, y, finish, size=6.5)
        y += 10.0
    add_body_lines(page, PROSE_LINES[5:8], top=y + 22.0)
    return page


def paired_entity_table_page(doc):
    """Held-out fixture A's shape: two columns whose rows all name the same
    project -- 'Opened 2011 · Cedar Archive' beside 'Closed 2014 · Cedar
    Archive'. Both columns carry ascending years, so a labelled-sequence proof
    reads them as two independent lists and every opening prints away from its
    closing. The row is the object: its cells share their entity, and that is
    what proves it."""
    page = add_page(doc)
    add_body_lines(page, PROSE_LINES[:3])
    y = BODY_TOP + 4 * BODY_LEADING
    _put(page, 99.0, y, "OPENED · YEAR / PROJECT", size=9.0, font=_BOLD)
    _put(page, 320.0, y, "CLOSED · YEAR / PROJECT", size=9.0, font=_BOLD)
    y += 18.0
    for left, right in [
            ("Opened 2011 · Cedar Archive", "Closed 2014 · Cedar Archive"),
            ("Opened 2013 · Harbor Archive", "Closed 2017 · Harbor Archive"),
            ("Opened 2016 · Lantern Archive", "Closed 2020 · Lantern Archive"),
            ("Opened 2019 · Orchard Archive", "Closed 2025 · Orchard Archive")]:
        _put(page, 99.0, y, left, size=9.0)
        _put(page, 320.0, y, right, size=9.0)
        y += 14.0
    add_body_lines(page, PROSE_LINES[5:7], top=y + 16.0)
    return page


def ruled_table_page(doc):
    """A ruled two-column table: cell rows must not become prose column traversal.

    The ruling is the signal a reader's eye uses: this is a grid, not two columns
    of prose. A converter that reads it column-major prints every first cell and
    then every second cell, and no row survives.
    """
    page = add_page(doc)
    rows = [('Red', 'White'), ("Pine", "Top"), ("Beech", "Birch"),
            ("Maple", "Cedar")]
    top = BODY_TOP
    _column_rows(page, rows)
    bottom = top + BODY_LEADING * len(rows)
    for x in (48.0, 280.0, 460.0):
        page.draw_line((x, top - 12.0), (x, bottom), color=(0, 0, 0), width=0.7)
    for y in (top - 12.0, top + 2.0, bottom):
        page.draw_line((48.0, y), (460.0, y), color=(0, 0, 0), width=0.7)
    return page


def unruled_label_table_page(doc):
    """Synthetic case: An unruled table with short labels beside a content column.

    Text is invented for this regression; geometry exercises the stated layout."""
    page = add_page(doc)
    _column_rows(page, [
        ("Cabinet features", 'Northern, Painted, Short Shelving, Curved'),
        ('Configurations', "Wooden, Adjustable, Portable, Unpainted"),
        ('Tooling/Parts', "Workshop, South Wall"),
        ('Measurements', 'Wooden, Narrow, Solid, Spring, Brass'),
        ("Attachments", 'drawer Blue, shelf Red, cabinet Yellow'),
        ("Assemblies", 'Blue for lids, Red for bases, spare Yellow'),
    ], left=97.0, right=167.0)
    return page


# ------------------------------------------------------------- ruled scan register

def _scan_raster(rule_ys=(), divider_x=None, divider_ys=(0.0, 0.0)):
    """A page raster carrying only ruling: the horizontal rules (and optionally
    one vertical divider) rendered to pixels at scan scale, no text at all --
    what a scan of a ruled page actually is once the text layer is separated
    from it."""
    art = new_doc()
    page = add_page(art)
    for y in rule_ys:
        page.draw_line((48.0, y), (460.0, y), color=(0.3, 0.3, 0.32), width=0.7)
    if divider_x is not None:
        page.draw_line((divider_x, divider_ys[0]), (divider_x, divider_ys[1]),
                       color=(0.45, 0.45, 0.48), width=0.5)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    data = pix.tobytes("png")
    art.close()
    return data


#: The register's rows: (record lines, grant lines). Fresh wording on purpose --
#: the behaviour under test is the measured row relationship, not these labels.
REGISTER_ROWS = [
    (["Hollow Lane bridge: rebuild the",
      "cracked southern abutment before the",
      "winter fair, keeping the sound arches."],
     ["Granted 12 April 1998. The mason",
      "may start once the warden signs the",
      "traffic plan; inspect in September."]),
    (["Fen Causeway path: raise the sunken",
      "boardwalk between the alder gate and",
      "the landing, proof against floods."],
     ["Granted with conditions. Funds are",
      "held through 2001, provided the ferry",
      "landing stays open on fair days."]),
    (["Old Quarry sluice: gauge the north",
      "gate and log the oak timbers before",
      "any works raise the water upstream."],
     ["Survey allowed for one season. Send",
      "the measured drawings to the manor",
      "court by 3 March; no rebuild follows."]),
    (["Chapel Green lamps: keep the three",
      "original iron posts, then fit lanterns",
      "where the night path is still dark."],
     ["Granted for a five-year term. The",
      "village trust holds the inspection",
      "book and may ask for taller posts."]),
]

#: Each record's opening words beside its grant's opening words, in row order.
REGISTER_PAIR_ANCHORS = [
    ("Hollow Lane bridge", "Granted 12 April 1998"),
    ("Fen Causeway path", "Granted with conditions"),
    ("Old Quarry sluice", "Survey allowed for one season"),
    ("Chapel Green lamps", "Granted for a five-year term"),
]

_REGISTER_TOP = 186.0
_REGISTER_ROW_H = 78.0
_REGISTER_LEADING = 13.0


def _register_scan_page(doc, rule_ys):
    """A ruled paired register as a full-page scan with an OCR text layer over it.

    Held-out case A's image-only shape: two headed columns of prose cells whose
    rows share baselines, every cell ending in sentence punctuation, the cells
    handed back in column order (every left cell before every right cell). The
    ruling that pairs each record with its grant survives only in the page
    raster, where the born-digital veto's vector path count cannot see it."""
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H),
                      stream=_scan_raster(rule_ys, divider_x=264.0,
                                          divider_ys=(145.0, 500.0)))
    _put(page, 48.0, 90.0, "Parish Bridge Register: Grants and Conditions",
         size=13.0, font=_BOLD)
    _put(page, 48.0, 116.0,
         "Each right-hand grant belongs to the record aligned with it on the same ruled line.",
         size=9.6)
    _put(page, 53.0, 150.0, "Bridge record and scope", size=10.4, font=_BOLD)
    _put(page, 300.0, 150.0, "Grant, term, and next inspection", size=10.4,
         font=_BOLD)
    y = _REGISTER_TOP
    for record, grant in REGISTER_ROWS:
        for index, line in enumerate(record):
            _put(page, 53.0, y + index * _REGISTER_LEADING, line, size=9.6)
        for index, line in enumerate(grant):
            _put(page, 300.0, y + index * _REGISTER_LEADING, line, size=9.6)
        y += _REGISTER_ROW_H
    _put(page, 48.0, 530.0,
         "Register note: bridge names appear in the left column only; "
         "the ruling defines each grant.",
         size=8.8)
    return page


def scan_ruled_register_page(doc):
    """The register scan WITH its ruling: six rules bound the headed cells."""
    rules = [134.0, 168.0]
    rules += [_REGISTER_TOP + index * _REGISTER_ROW_H + 64.0
              for index in range(len(REGISTER_ROWS))]
    return _register_scan_page(doc, rules)


def scan_unruled_register_page(doc):
    """The same register wording on a raster WITHOUT ruling: the text shape is
    identical, so whatever the reading does here is decided by the text alone."""
    return _register_scan_page(doc, [])


def scan_independent_columns_page(doc):
    """Held-out case B's image-only shape: two headed, independent prose
    accounts over a raster that carries one deck rule and a vertical divider --
    no row ruling at all. Mixed paragraph lengths, baselines unshared; nothing
    here pairs, and the columns must keep reading down, never interleaved."""
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H),
                      stream=_scan_raster([134.0], divider_x=264.0,
                                          divider_ys=(145.0, 700.0)))
    _put(page, 48.0, 90.0, 'Workshop Ledger: Two Separate Invented Records of Timber',
         size=13.0, font=_BOLD)
    _put(page, 48.0, 116.0,
         'These invented records use the same years but describe two unrelated kinds of wooden tools.',
         size=9.6)
    _put(page, 53.0, 150.0, 'Workshop stock: Invented timber', size=10.4, font=_BOLD)
    _put(page, 300.0, 150.0, 'Repair notes: Invented trays', size=10.4,
         font=_BOLD)
    left = [
        (186.0, ['In 1911 the timber crew boxed the',
                 'spare blue pegs beside our longest workbench.',
                 'Each narrow drawer held three brass handles',
                 'and two cotton covers.']),
        (276.0, ['The 1913 ledger lists new hand tools',
                 'beside the unfinished wooden frame with smooth',
                 'edges; its measured width fits our',
                 'largest crate.']),
        (366.0, ['By 1917 the storeroom held another large',
                 'bundle of clamps. Each wooden peg',
                 'rested beside fresh chalk.']),
    ]
    right = [
        (186.0, ['A tray maker packed several covers',
                 'neatly in 1911, when the fresh paint',
                 'dried beside the bench. These invented notes',
                 'list colours, folds, and cotton covers.']),
        (290.0, ['In 1913 our fabric was neatly stacked',
                 'along the narrow rack beside the tools.']),
        (382.0, ["The 1917 repair slip says: 'Fold cloth",
                 "inside the spare crate.' These cards identify",
                 'cotton samples and several small pins.']),
    ]
    for top, lines in left:
        for index, line in enumerate(lines):
            _put(page, 53.0, top + index * _REGISTER_LEADING, line, size=9.6)
    for top, lines in right:
        for index, line in enumerate(lines):
            _put(page, 300.0, top + index * _REGISTER_LEADING, line, size=9.6)
    _put(page, 48.0, 505.0,
         'Control note: recurring years describe two independent stores of timber and cloth, never paired rows.',
         size=8.8)
    return page


#: The two columns' own opening anchors, in each column's print order.
INDEPENDENT_LEFT_ANCHORS = ['In 1911 the timber crew',
                            'The 1913 ledger lists',
                            'By 1917 the storeroom held']
INDEPENDENT_RIGHT_ANCHORS = ['A tray maker packed',
                             'In 1913 our fabric',
                             'The 1917 repair slip says']


def ragged_single_column_page(doc):
    """Synthetic case: an ordinary page of prose whose ragged short     lines open fake gutters between their right edges.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    y = add_body_lines(page, PROSE_LINES[:9])
    _put(page, LEFT, y, "The old shelving was removed.")
    y += BODY_LEADING
    y = add_body_lines(page, PROSE_LINES[9:15], top=y)
    _put(page, LEFT, y, "The sample (wooden blocks and trays) was placed beside the wooden-")
    y += BODY_LEADING
    _put(page, LEFT + 70, y, "so there would be several labels waiting beside the tray")
    y += BODY_LEADING
    add_body_lines(page, PROSE_LINES[15:22], top=y)
    return page


# ------------------------------------------------------- artwork inside a page scan

def scan_chart_band_page(doc, png_bytes):
    """Synthetic case: An interior diagram inside a full-page scan carrier.

    Text is invented for this regression; geometry exercises the stated layout."""
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    add_running_head(page, "194", "CHAPTER 7: THE CABINETS")
    _put(page, 179.6, 108.0, "BOX PANEL", size=20.0, font=_BOLD)
    _put(page, 120.1, 155.0, "c", size=36.0)
    _put(page, 187.3, 156.0, "7", size=36.0)
    _put(page, 254.0, 156.0, "-3", size=36.0)
    _put(page, 325.3, 155.0, "z", size=36.0)
    _put(page, 96.8, 200.0, "+ 2 L", size=30.0)
    _put(page, 165.5, 250.0, "TRAY PANEL", size=20.0, font=_BOLD)
    _put(page, 122.3, 300.0, "7", size=36.0)
    _put(page, 96.8, 335.0, "+ 2", size=30.0)
    _put(page, 168.9, 388.0, "Figure 7.4 - Cabinet Color Samples")
    add_body_lines(page, PROSE_LINES[:8], top=416.0)
    return page


def scan_sidebar_figure_page(doc, png_bytes):
    """Synthetic case: A circular diagram beside narrow prose, with a caption below.

    Text is invented for this regression; geometry exercises the stated layout."""
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    add_running_head(page, "459", "THE ASSEMBLY OF THE UPPER SHELF")
    y = add_body_lines(page, PROSE_LINES[:3])
    _put(page, LEFT, y + 8.0, "EXAMPLE WORKSHOP A", size=HEAD_SIZE, font=_BOLD)
    top = y + 8.0 + BODY_LEADING * 1.8
    column = [
        "The cabinet was made for A.B.",
        "Unpainted Drawers, and",
        "it was packed beside the wall",
        "after the new drawer was sanded",
        "for storage in the sample room.",
        "It had a small rack with",
        "one shelf empty, one holding",
        "the twelve blocks, and the",
        "tray beside the cloth in the",
        "corner, close to the door.",
    ]
    y = top
    for line in column:
        _put(page, LEFT, y, line)
        y += BODY_LEADING
    _put(page, 303.0, top + 6 * BODY_LEADING, "Plate 45 - Example Workshop A")
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
    add_running_head(page, "301", "CHAPTER 8: THE ASSEMBLY OF TRAYS")
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
    _put(page, 168.9, 556.0, "Figure 8.1 - The Twelve Drawers")
    add_body_lines(page, PROSE_LINES[8:12], top=580.0)
    return page


def garbage_scan_page(doc, png_bytes):
    """Synthetic case: A photographed page whose OCR layer is noise, over a real scan.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    y = BODY_TOP
    for line in GARBAGE_LINES * 3:
        _put(page, LEFT, y, line)
        y += BODY_LEADING
    return page


def scan_chapter_opening_page(doc, png_bytes):
    """Synthetic case: one line of display type over white space.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    add_running_head(page, "93", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    _put(page, 144.0, 200.0, "CHAPTER 4", size=19.0, font=_BOLD)
    _put(page, 144.0, 240.0, "The Workshop Instructions", size=16.0, font=_BOLD)
    add_body_lines(page, PROSE_LINES[:8], top=306.0)
    return page


def scan_date_tail_page(doc, png_bytes):
    """Synthetic case: a paragraph, air, and a body-sized date line.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    add_running_head(page, "18", "ACKNOWLEDGMENTS")
    add_body_lines(page, PROSE_LINES[:4])
    _put(page, 340.0, BODY_TOP + 9 * BODY_LEADING, 'February 2042')
    return page


def illustrated_page(doc, png_bytes):
    """Prose with a plate set into it, and no caption under the plate.

    An illustration leaves no words in the text layer, so nothing that counts words
    can tell whether it survived the conversion. This is the page that proves a
    picture is not quietly dropped.
    """
    page = add_page(doc)
    add_running_head(page, "117", "FOLDER (FIRST EDITION AB?)")
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
COVER_GARBAGE_LINES = [', jtt .is ? 4.1 I I -j.p.,, j, 1 4', "„,.... 4..• *,..... -,, D jr j” X' I ;7 4 D'P,4 o qfjf ZQS LL", "*•.,,j`JZ.I. 4, ' -7 ' -1 4. xjm jfn riD-'", '"O.,,,.„-jr,-,41 \'\'\'• 1., i ‘-\',,,, • sp,, —zi.41! I \' ji 2', '., "Ds4;*: rr" ..f.j 3 e • 1"w 7(f.--,i,„{c Z 0 f']


def cover_garbage_page(doc, png_bytes):
    """Synthetic case: Full-bleed artwork misread as short punctuation-heavy tokens.

    Text is invented for this regression; geometry exercises the stated layout."""
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    y = 108.0
    for line in COVER_GARBAGE_LINES * 3:
        _put(page, LEFT + 40, y, line)
        y += 24.0
    return page


def cover_plate_page(doc, png_bytes):
    """Synthetic case: The same cover with its text read: a title, an author, and the whole     page is the design. A full-bleed cover/plate with a little text is one     figure, the page itself -- never strips measured around the title.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    _put(page, 150.0, 200.0, "SYNTHETIC TYPOGRAPHY", size=HEAD_SIZE, font=_BOLD)
    _put(page, 196.0, 420.0, "Test Author", size=HEAD_SIZE)
    _put(page, 170.0, 600.0, "Synthetic Fixture Press", size=BODY_SIZE)
    return page


GARBAGE_LINES = [
    'czotm fipjv egkzs ohjpt ficzm vustj qopzw miekj fxczt',
    'sgtnv ohicz fmpjd qktes zxicm ofsju gpthz iwkec qmfzj',
    'tiopc esgvz fmjhk qwict zosxp dgfjn mevuc stizq focjx',
    'hipzm fstoe cgqvj kwizm pxtsc fodji uegzs mvqtp fijcw',
    'ztsof ipkec qmjug vhxzt fosci detpj wiqmz cgsfv otphk',
    'efizm qtwvc shgpj oxizc ftdcm uejsk qpiwz votgf hmszc',
    'ifopt csxje qgvzm dhkwc ftszp iuojm egqcx wivzt fohdc',
    'mpjsk feiqc zthox wgsvc fitzm depuj oqkzs cixvh fmpgt',
    'sweic qfozj vtmhp kgdcf xitsz ueojq cmizp fwvhg tkose',
    'djifz mqtcp exovg hwskc fipzt mudjo cgtvf qeizx sojhp',
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


def ocr_scan_page(doc, png_bytes, head="FOLDER (FIRST EDITION AB?)"):
    """Synthetic case: A photograph of a page with a readable OCR text layer over it.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    add_running_head(page, "97", head)
    add_body_lines(page, PROSE_LINES)
    return page


def prose_page(doc, marker=None):
    """An ordinary page of prose: enough common words to read as real text."""
    page = add_page(doc)
    add_running_head(page, "97", "FOLDER (FIRST EDITION AB?)")
    add_body_lines(page, PROSE_LINES)
    if marker is not None:
        add_notes(page, [(marker, "Ledger, From Loose Cards to Ledgers, p. 26.")])
    return page


def thin_page(doc):
    """Seven lines on a page: a sparse scan, or two printed pages photographed as one.

    Not a defect — but the user has to be told, because a book of these converts to
    an EPUB with a paragraph per page and the fault is in the PDF, not in Reflow.
    """
    page = add_page(doc)
    add_running_head(page, "97", "FOLDER (FIRST EDITION AB?)")
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
    add_running_head(tail, "101", "FOLDER (FIRST EDITION AB?)")
    add_body_lines(tail, PROSE_LINES[:12] + [
        "one workshop ledger ends at this point and the leaf after it is blank",
    ])

    head = add_page(doc)
    add_running_head(head, "102", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    add_body_lines(head, [
        "The register gives a repair recipe in a fuller form, and the",
        "version shown here is the one used to label the sample tray.",
    ] + PROSE_LINES[:10])
    return tail, head


def orphan_marker_page(doc):
    """A marker printed on a page whose note is set somewhere else.

    A long note can overflow onto a following page. The marker here is
    genuine; the note is simply not here. A converter that emits an EPUB footnote
    link anyway ships a button that goes nowhere.
    """
    page = add_page(doc)
    add_running_head(page, "98", "FOLDER (FIRST EDITION AB?)")
    y = add_body_lines(page, PROSE_LINES[:10])
    add_line_with_marker(page, y, "and the tray for this cabinet is painted blue",
                         "204", ".", superscript=True)
    return page


def typographers_page(doc):
    """Characters that are text on the page and markup in a file.

    ``&``, ``<`` and ``>`` are set as type in plenty of books — an ampersand in a
    publisher's name, angle brackets in an editorial insertion. Written into XHTML
    unescaped they make a file no EPUB reader will open.
    """
    page = add_page(doc)
    add_running_head(page, "99", "FOLDER (FIRST EDITION AB?)")
    add_body_lines(page, [
        'printed at the fictional Paper & Board beside the broad window,',
        'with a sample instruction <leave this drawer empty> set in brackets',
        'and the note "cf. Workbook & Folderbook" on the small card below.',
    ] + PROSE_LINES[:10])
    return page


def mid_page_heading_page(doc, title="Workshop of Birchwood", folio="103"):
    """A section heading that starts part way down a page which also sets notes.

    The chapter split happens at the heading, so this one page's text lands in two
    files while its footnotes stay in one of them. That is the case where a footnote
    link has to name the file it points into and not only the fragment.
    """
    page = add_page(doc)
    add_running_head(page, folio, "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    y = add_body_lines(page, PROSE_LINES[:6])
    y = add_line_with_marker(page, y, 'as Ledger recorded beside the blue tray',
                             "212", ' on our tray.')
    # A section heading is set with air above and below it, and the paragraph under
    # it opens a new sentence. Both are what tell a converter it is a heading and
    # not a bold lead-in to the sentence that follows.
    head_y = y + BODY_LEADING * 1.8
    _put(page, LEFT, head_y, title, size=HEAD_SIZE, font=_BOLD)
    add_body_lines(page,
                   ['The workshop has three sample cupboards, each fitted with an']
                   + PROSE_LINES[6:14],
                   top=head_y + BODY_LEADING * 1.8)
    add_notes(page, [(212, "Ledger, Fixturebook, vol. 2, p. 441.")])
    return page


def glyph_marker_page(doc):
    """Synthetic case: A superscript note number the scanner read as letters, not digits.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, 'Workbook, Samples, Catalogue, Folderbook, and Checklist.s° Other')
    y += BODY_LEADING
    _put(page, LEFT, y, 'plans such as Worksheet show a different set of wooden trays.')
    add_notes(page, [(50, "Catalogue, Repairbook, 2, 21: 26.")])
    return page


def glyph_marker_without_its_note_page(doc):
    """The control: the same damaged shape on a page that prints no note it fits.

    Nothing on this page says the letters are a number, so they stay letters and
    the page is routed instead.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, 'Workbook, Samples, Catalogue, Folderbook, and Checklist.s° Other')
    y += BODY_LEADING
    _put(page, LEFT, y, 'plans such as Worksheet show a different set of wooden trays.')
    add_notes(page, [(7, "Ledger, From Loose Cards, p. 40.")])
    return page


def split_glyph_marker_page(doc):
    """Synthetic case: Half the marker survived as a digit and half came back as a letter.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    y = BODY_TOP
    x = LEFT
    x += _put(page, x, y, "the depth of this tray measured today.")
    x += add_marker(page, x, y, "4", superscript=False)
    _put(page, x, y, "s The measured spacing between these assembled shelves")
    y += BODY_LEADING
    _put(page, LEFT, y, "was also described in another invented repair log.")
    add_notes(page, [(45, "Folderbook, Handbook, 3, 9: 3.")])
    return page


def glyph_prefix_marker_page(doc):
    """Synthetic case: The marker's leading digits came back as punctuation and its last as itself.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    y = BODY_TOP
    x = LEFT
    x += _put(page, x, y, "Blue wooden lids rest beside that drawer and red cotton covers lie nearby.'°")
    x += add_marker(page, x, y, "3", superscript=False)
    _put(page, x, y, " Inspect this clean red lid beside the narrow wooden")
    y += BODY_LEADING
    _put(page, LEFT, y, "cupboard, then place its folded cloth upon the clean workbench.")
    add_notes(page, [(103, "Planbook, Cabinetry, 2: 433-452.")])
    return page


def ambiguous_glyph_marker_page(doc):
    """The control: the damaged run is one edit away from two of the page's notes.

    ``"°`` reads as 110, and both 100 and 111 are printed here and unreferenced.
    Picking one prints a citation the page does not make.
    """
    page = add_page(doc)
    add_running_head(page, "110", "SAMPLE PLANBOOK")
    y = BODY_TOP
    _put(page, LEFT, y, "every four drawers until at least the middle of the cabinet.\"° Sample")
    y += BODY_LEADING
    _put(page, LEFT, y, "The plan describes a wooden cabinet in five sections.")
    add_notes(page, [(100, 'Cardbook, "The Drawing of the First Cabinet."'),
                     (111, 'Cratebook, Planbook and the Workshop Shelves.')])
    return page


def _runover_pages(doc, note_finishes):
    """Synthetic case: A note continues onto the next page before that page numbered notes.

    Text is invented for this regression; geometry exercises the stated layout."""
    first = add_page(doc)
    add_running_head(first, "112", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    y = add_body_lines(first, PROSE_LINES[:8])
    add_line_with_marker(first, y, 'Sometime during the next exercise Register added a drawer.',
                         "258", after=' It')
    # Short enough to be set on one line: this page's note zone is the evidence the
    # next page's unnumbered line is weighed against, so it has to read back whole.
    tail = "On Register's drawer see The Sketch, trans. Office, pp. 7-8."
    if not note_finishes:
        tail = "On Register's drawer see The Sketch, p. 7. The sample label reads"
    add_notes(first, [(257, "Journal, \"Workshop Register,\" p. 468."), (258, tail)])

    second = add_page(doc)
    add_running_head(second, "113", "WORKSHOP REGISTER")
    y = add_body_lines(second, PROSE_LINES[8:14])
    y = add_body_lines(second, [
        "One example is Register's placement of the blue wooden tray between",
        'the two empty shelves; the diagram shows how much room the',
        'large wooden cupboard needs during'], top=y + BODY_LEADING)
    _put(second, LEFT, NOTE_TOP - 40, 'filed under Workshop Plans, 5: 7, with a brass hinge sketched beside it.', size=NOTE_TEXT_SIZE)
    add_notes(second, [(259, 'See Directory, Plans for Wooden Cupboards, trans. Bookcase.')])

    third = add_page(doc)
    add_running_head(third, "114", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    add_body_lines(third, [
        "the height of the shelves beside that drawer, and the freshly painted lids",
        "were left drying, safely beside the folded cotton covers."] + PROSE_LINES[:6])
    return [first, second, third]


def runover_footnote_pages(doc):
    """The note stops mid-sentence on its own page and finishes on the next."""
    return _runover_pages(doc, note_finishes=False)


def finished_footnote_pages(doc):
    """The control: the note finishes its sentence, so nothing ran over."""
    return _runover_pages(doc, note_finishes=True)


def out_of_order_glyph_marker_page(doc):
    """Synthetic case: one glyph reading fits the page's order and one does not.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, "of the lower drawer in sketch 4 of the Handbook.''s The label does")
    y += BODY_LEADING
    _put(page, LEFT, y, 'mention Repairlog beside a blue tray, but the drawing does not')
    y += BODY_LEADING
    _put(page, LEFT, y, 'show the small cotton cloth resting underneath the wooden lid,')
    y += BODY_LEADING
    _put(page, LEFT, y, 'which instead appears on a card beside the window."\' Index')
    y += BODY_LEADING
    _put(page, LEFT, y, 'shows that Folderbook places the cloth inside another empty box.')
    y += BODY_LEADING
    y = add_line_with_marker(page, y, 'Blueprint is another sample drawing stored beside Register.', "138",
                             after=' That lid')
    _put(page, LEFT, y, 'is drawn beside the narrow wooden tray from Repairlog, which sits')
    y += BODY_LEADING
    y = add_line_with_marker(page, y, 'on a separate shelf between two empty boxes and a folded cloth.', "140")
    add_notes(page, [(135, "Folderbook, Handbook, 4, 11: 8."),
                     (136, 'On the Folderbook diagram of the cloth beside the window.'),
                     (137, "Index, A Survey of Sample Folderbook, p. 12."),
                     (138, 'Register, Workshop Drawings, 4, proem: 5.'),
                     (139, "Ledger, From Loose Cards, p. 26."),
                     (140, "Folderbook, Handbook, 4, 12: 1."),
                     (141, 'Inventory and Booklet, Workshop Diagrams, p. 176.')])
    return page


def two_damaged_note_numbers_page(doc):
    """Synthetic case: two note numbers in a row lost a digit.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "115", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    y = add_body_lines(page, PROSE_LINES[:5])
    y = add_line_with_marker(page, y, "the finished cupboard", "118", ' is blue')
    y = add_line_with_marker(page, y, "as Ledger notes", "119", ' nearby')
    add_notes(page, [(117, 'Inventory, A Guide to Wooden Shelves and Drawers.'),
                     (18, 'Ledger, The Fixturebook of Sample Drawings, p. 195.'),
                     (1, "Boxbook, Instruction Sheets from the Workshop, p. 12."),
                     (120, "Toolbook, Vintage Lettering, p. 31.")])
    return page


def note_number_read_too_high_page(doc):
    """Synthetic case: the damaged numbers read HIGHER than the true ones.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "117", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    y = add_body_lines(page, PROSE_LINES[:4])
    y = add_line_with_marker(page, y, "the blue cabinet", "30", ' is painted')
    y = add_line_with_marker(page, y, 'a spare drawer', "31", ' supports it')
    add_notes(page, [(27, "Folderbook, Handbooks, 2: 21."),
                     (28, "Folderbook, Handbooks, 3: 11."),
                     (29, "Index, A Survey of Sample Folderbook, p. 4."),
                     (38, 'Inventory and Booklet, Workshop Diagrams, p. 110.'),
                     (39, "Ledger, From Loose Cards to Ledgers, p. 26."),
                     (32, "Toolbook, Vintage Lettering, p. 33."),
                     (33, "Manual, Labeling in Wooden Boxes, p. 58."),
                     (34, "Notebook, The Painted Lettering, p. 44.")])
    return page


def damaged_first_note_pages(doc):
    """Two facing pages: the damage is the FIRST note of the second one.

    ``115`` came back as ``1`` at the top of its own zone, so on that page alone
    there is no earlier number for it to fail to ascend from. The page before is
    what makes it damage.
    """
    first = add_page(doc)
    add_running_head(first, "113", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    y = add_body_lines(first, PROSE_LINES[:4])
    y = add_line_with_marker(first, y, "the Handbooks", "113", ' remain')
    y = add_line_with_marker(first, y, "on several sample cards", "114", ' on it')
    add_notes(first, [(113, "Index, A Survey of Sample Folderbook, p. 2."),
                      (114, "Index, A Survey of Sample Folderbook, p. 3.")])

    second = add_page(doc)
    add_running_head(second, "114", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    y = add_body_lines(second, PROSE_LINES[:4])
    y = add_line_with_marker(second, y, "the next repair sheet", "115", ' holds lids')
    y = add_line_with_marker(second, y, "and Checklist", "116", ' labels trays')
    y = add_line_with_marker(second, y, "as does Storebook", "117", ' below it')
    add_notes(second, [(1, "Ledger, ed., Entries for Cabinet Assembly, p. 8."),
                       (116, "Checklist, Compendium, 5: 57."),
                       (117, "Storebook, Commentary, p. 41.")])
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
        add_running_head(page, folio, "CHAPTER %s: ORIGINS OF SYNTHETIC TYPOGRAPHY"
                         % chapter)
        y = add_body_lines(page, PROSE_LINES[:4])
        for digits in ("1", "2", "3"):
            y = add_line_with_marker(page, y, 'our tray is painted', digits, ' nearby')
        add_notes(page, [(1, "Notebook, The Painted Lettering, p. 44."),
                         (2, "Manual, Labeling in Wooden Boxes, p. 58."),
                         (3, "Toolbook, Vintage Lettering, p. 31.")])
        pages.append(page)
    return pages


def chapter_restart_glyph_marker_page(doc):
    """Synthetic case: the notes restart, and one marker came back damaged.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    y = BODY_TOP
    _put(page, LEFT, y, 'Folderbook places this tray beside the narrow cupboard."\' At')
    y += BODY_LEADING
    _put(page, LEFT, y, 'one end the drawing shows a further pair of empty shelves,')
    y += BODY_LEADING
    _put(page, LEFT, y, 'and a separate label lists the contents of each wooden box.')
    y += BODY_LEADING
    y = add_line_with_marker(page, y, 'The label describes the arrangement of the wooden trays.', "1",
                             after=' In')
    _put(page, LEFT, y, 'extension it can also mean shelves, cupboards, or drawers.')
    add_notes(page, [(110, "Folderbook, Handbook, 4, 12: 1."),
                     (111, "Folderbook, Handbook, 4, 25: 9."),
                     (1, "Archive, Definitions and Foundations, p. 180."),
                     (2, "Index, A Survey of Sample Folderbook, p. 12.")])
    return page


def marker_for_a_missing_note_page(doc):
    """Synthetic case: the body marks 59 and the note zone has no 59.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "104", 'CHAPTER 3: THE EARLY SYNTHETIC SOURCES')
    y = add_body_lines(page, PROSE_LINES[:4])
    add_body_lines(page, ["the measured height of these shelves beside that drawer.59",
                          "had wooden lids and small brass hinges"], top=y)
    add_notes(page, [(58, 'Toolbook, Vintage Lettering, p. 31. Manual, Labeling in Wooden Boxes, p. 58.'),
                     (60, "Ledger, From Loose Cards to Ledgers, p. 26."),
                     (61, "Boxbook, Instruction Sheets from the Workshop, p. 12.")])
    return page


def glyph_numbered_note_page(doc):
    """Synthetic case: the footnote's OWN number came back as letters.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "82", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    y = add_body_lines(page, PROSE_LINES[:5])
    add_body_lines(page, ['choosing to leave the blue tray beside the empty cupboard.9°',
                          'I prefer the drawing that shows Partsbook beside one'],
                   top=y)
    add_notes(page, [("9°", "Shelfbook, Shelving Instructions, p. 10."),
                     (91, 'Manual shows the wooden shelf below the small red tray.'),
                     (92, "Manual, Labeling in Wooden Boxes and Cabinets, p. 108.")])
    return page


def lowercase_word_in_the_note_zone_page(doc):
    """The control. A note that ran over from the page before opens the zone with an
    ordinary word, and ``so`` is two glyphs a digit is mistaken for -- 5 and 0. A
    reader that takes it for note 50 has invented a footnote out of a sentence."""
    page = add_page(doc)
    add_running_head(page, "83", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    y = add_body_lines(page, PROSE_LINES[:5])
    add_notes(page, [('so', 'Shelfbook shows a narrow wooden tray beside the window.'),
                     (91, 'Manual shows the wooden shelf below the small red tray.')])
    return page


def degree_in_the_note_zone_page(doc):
    """Synthetic case: A degree in body prose is not a superscript note number.

    Text is invented for this regression; geometry exercises the stated layout."""
    page = add_page(doc)
    add_running_head(page, "324", "CHAPTER 28")
    y = add_body_lines(page, PROSE_LINES[:4])
    y = add_body_lines(page, [
        'The dial reads 24° Clockwise. Another wooden pointer rests',
    ], top=max(y, NOTE_TOP - 40.0), size=NOTE_TEXT_SIZE)
    add_body_lines(page, [
        '24° Clockwise. After the first short turn, the pointer has until',
        '2° Counterclockwise to reach the small mark beside the blue dial.',
    ], top=y + 8.0, size=NOTE_TEXT_SIZE)
    add_notes(page, [(10, "Rackbook, The Fifty Shelves 6.")])
    return page


def scan_note_identity_page(doc, png_bytes):
    """Synthetic case: Conflicting text-layer and raster digits preserve note uncertainty.

    Text is invented for this regression; geometry exercises the stated layout."""
    page = add_page(doc)
    page.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=png_bytes)
    y = add_body_lines(page, PROSE_LINES[:4])
    _put(page, LEFT, y, 'Register shows the red tray beside the window.\'"')
    add_notes(page, [(1, 'Register, Workshop Drawings 2.24, trans. Drawerbook.')])
    return page


def born_digital_note_identity_page(doc):
    """The same shape with no scan under it: a repair on a clean layer is just
    a repair, with nothing damaged to be uncertain about."""
    page = add_page(doc)
    y = add_body_lines(page, PROSE_LINES[:4])
    _put(page, LEFT, y, 'Register shows the red tray beside the window.\'"')
    add_notes(page, [(1, 'Register, Workshop Drawings 2.24, trans. Drawerbook.')])
    return page


def own_line_note_number_page(doc):
    """Synthetic case: A note-size opening number on its own source line.

    Text is invented for this regression; geometry exercises the stated layout."""
    page = add_page(doc)
    add_running_head(page, "324", "CHAPTER 28")
    y = add_body_lines(page, PROSE_LINES[:5])
    y = add_line_with_marker(page, y, 'the arrangement of the narrow wooden shelves.',
                             "10", "", superscript=True)
    add_body_lines(page, PROSE_LINES[5:7], top=y)
    _put(page, LEFT + 4, NOTE_TOP, "10", size=NOTE_TEXT_SIZE)
    _put(page, LEFT + 4, NOTE_TOP + 12.0, "Rackbook, The Fifty Shelves 6.",
         size=NOTE_TEXT_SIZE)
    return page


def bare_number_is_not_an_own_line_note_page(doc):
    """The control: a bare number at the bottom of the page with no sentence
    after it is a folio, not a note opening."""
    page = add_page(doc)
    add_running_head(page, "325", "CHAPTER 28")
    add_body_lines(page, PROSE_LINES[:8])
    _put(page, LEFT + 4, NOTE_TOP + 48.0, "391", size=NOTE_TEXT_SIZE)
    return page


def glyph_note_number_page(doc):
    """Synthetic case: a note whose own printed number the scan read as letters.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "102", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    y = add_body_lines(page, PROSE_LINES[:5])
    y = add_line_with_marker(page, y, 'Casebook and Binder show two wooden drawers', "150",
                             " that the repair crew", superscript=True)
    add_body_lines(page, ['were placed beside the folded cloth at the edge of the'], top=y)
    y = add_notes(page, [(150, "Les Plans Imaginaires, ed. Casebook and Binder, pp. 56-84.")])
    _put(page, LEFT + 4, y, "Is' Ledger, Fixturebook, vol. 2, p. 445.",
         size=NOTE_TEXT_SIZE)
    return page


def split_glyph_note_number_page(doc):
    """Synthetic case: the scan split the note's number and mangled the rest.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "93", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    y = add_body_lines(page, PROSE_LINES[:5])
    y = add_line_with_marker(page, y, "the cabinet assembly order is", "105",
                             " drawn by Cratebook", superscript=True)
    add_body_lines(page, ['in a drawing of the cupboard beside the wooden bench.'], top=y)
    y = add_notes(page, [(104, "Planbook, Cabinetry, 4: 294-407.")])
    x = LEFT + 4
    x += _put(page, x, y, "1", size=NOTE_NUM_SIZE)
    _put(page, x + 2, y, '" Cratebook, Planbook and Assembly Steps, p. 48f.',
         size=NOTE_TEXT_SIZE)
    add_notes(page, [(106, "Ledger, Review of Planbook, p. 263.")], top=y + 12.0)
    return page


def note_opening_with_a_letter_word_page(doc):
    """Synthetic case: a note that opens with the word ``I``.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "46", 'CHAPTER 3: THE EARLY SYNTHETIC TRADITION')
    y = add_body_lines(page, PROSE_LINES[:5])
    y = add_line_with_marker(page, y, 'the sketch for this empty cupboard', "78",
                             ' is placed near it', superscript=True)
    add_body_lines(page, ['and shows a wooden tray resting on the lower shelf.'],
                   top=y)
    add_merged_notes(page, [
        (78, 'I can place another blue tray on the lower shelf in this drawing.'),
        (79, "Manual, Labeling in Wooden Boxes and Cabinets, p. 108."),
    ])
    return page


def ocr_size_drift_page(doc):
    """Synthetic case: A paragraph of ordinary prose whose scanner set one line a hair larger.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "86", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    y = add_body_lines(page, PROSE_LINES[:4])
    _put(page, LEFT, y + BODY_LEADING,
         "The cabinet maker arranged the colored wooden trays",
         size=BODY_SIZE * 1.062)
    add_body_lines(page, [
        "Workbook labels from the drawings kept in the sample",
        "folders, and that reconstruction is what is displayed here.",
    ], top=y + 2 * BODY_LEADING)
    return page


def roman_heading_on_the_ladder_page(doc, title="The Workshop Period", folio="88"):
    """The control for :func:`ocr_size_drift_page`: a heading in the body's own roman
    face, set at the size the book uses for its section heads, over a new sentence."""
    page = add_page(doc)
    add_running_head(page, folio, "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    _put(page, LEFT, BODY_TOP, title, size=HEAD_SIZE)
    add_body_lines(page, PROSE_LINES[:6], top=BODY_TOP + BODY_LEADING)
    return page


def flat_run_in_heading_page(doc):
    """Synthetic case: A run-in heading with no size signal at all: bold, at the body's own size.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "94", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    _put(page, LEFT, BODY_TOP, "Workshop of Birchwood (First Edition AB?)",
         size=BODY_SIZE, font=_BOLD)
    add_body_lines(page, [
        "Workshop of Birchwood was a workshop that kept notes on the labeling of",
        "wooden boxes and their lids, although only drawings of",
        "the first cabinet remain in the current manual.",
    ], top=BODY_TOP + BODY_LEADING)
    return page


def run_in_heading_the_scanner_measured_small_page(doc):
    """Synthetic case: The same head as :func:`flat_run_in_heading_page`, measured a little *under*     the body rather than at it.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "94", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    _put(page, LEFT, BODY_TOP, "Workshop of Birchwood (First Edition AB?)",
         size=round(BODY_SIZE * 0.97, 2), font=_BOLD)
    add_body_lines(page, [
        "Workshop of Birchwood was a workshop that kept notes on the labeling of",
        "wooden boxes and their lids, although only drawings of",
        "the first cabinet remain in the current manual.",
    ], top=BODY_TOP + BODY_LEADING)
    return page


def bold_line_far_below_the_body_page(doc):
    """Synthetic case: Bold diagram labels far below body size are not headings.

    Text is invented for this regression; geometry exercises the stated layout."""
    page = add_page(doc)
    add_running_head(page, "94", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    _put(page, LEFT, BODY_TOP, "Workshop of Birchwood (First Edition AB?)",
         size=round(BODY_SIZE * 0.78, 2), font=_BOLD)
    add_body_lines(page, [
        "Workshop of Birchwood was a workshop that kept notes on the labeling of",
        "wooden boxes and their lids, although only drawings of",
        "the first cabinet remain in the current manual.",
    ], top=BODY_TOP + BODY_LEADING)
    return page


def _drifted_line_page(doc, drifted, following, folio):
    """Synthetic case: A page whose first line of a block the scan measured up to heading size.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, folio, "CHAPTER 12: THE CABINETS")
    y = add_body_lines(page, PROSE_LINES[:4])
    _put(page, LEFT, y + BODY_LEADING, drifted, size=HEAD_SIZE)
    add_body_lines(page, following, top=y + 2 * BODY_LEADING)
    return page


def heading_that_starts_mid_sentence_page(doc):
    """Synthetic case: ``polish brassware. Conversely, if`` came back     at 12.30pt in the body's roman face and became an ``<h2>`` -- which splits the     chapter there and puts the fragment in the reader's table of contents.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    return _drifted_line_page(
        doc, "polish brassware. Conversely, if",
        ["Copper is placed beside the tools on the shelf and the same",
         "arrangement is checked again the following morning."],
        folio="404")


def heading_that_stops_on_a_function_word_page(doc):
    """Synthetic case: ``Woodwork, brass and copper in`` came back at     12.50pt in the body's roman face. It ends on a word no title ends on.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    return _drifted_line_page(
        doc, "Woodwork, brass and copper in",
        ["storage are both listed among the tools for this project,",
         "which is the arrangement the example instructions call for."],
        folio="438")


def line_that_ends_in_sentence_punctuation_page(doc):
    """Synthetic case: An oversized prose line still ends in sentence punctuation.

    Text is invented for this regression; geometry exercises the stated layout."""
    return _drifted_line_page(
        doc, "The cabinet was made for A.B.",
        ["Unpainted Drawers, and the plan was drawn for a",
         "shelf in a fictional workshop beside the back door."],
        folio="401")


def long_heading_with_function_words_page(doc, folio="440"):
    """The control: a real section head that uses those same words inside it."""
    page = add_page(doc)
    add_running_head(page, folio, "CHAPTER 12: THE CABINETS")
    _put(page, LEFT, BODY_TOP, "Wooden Workbench Frames Beside Three Clean Painted Shelving Units",
         size=HEAD_SIZE)
    add_body_lines(page, PROSE_LINES[:6], top=BODY_TOP + BODY_LEADING)
    return page


def run_in_heading_with_scan_jitter_page(doc):
    """Synthetic case: The same run-in head as :func:`defect_a_page`, set the way a scan sets it.

    Printed prose is invented test data. Font sizes, marker damage and geometry
    exercise this layout with text written for these tests.
    """
    page = add_page(doc)
    add_running_head(page, "94", "CHAPTER 4: THE WORKSHOP INSTRUCTIONS")
    _put(page, LEFT + 0.29, BODY_TOP, "Workshop of Birchwood (First Edition AB?)",
         size=HEAD_SIZE, font=_BOLD)
    y = BODY_TOP + BODY_LEADING
    for drift, line in zip((0.0, -0.35, 0.24),
                           ["Workshop of Birchwood was a workshop that kept notes on",
                            "the labeling of wooden boxes and their lids, although",
                            "only drawings of the first cabinet remain."]):
        _put(page, LEFT + drift, y, line, size=BODY_SIZE)
        y += BODY_LEADING
    return page
