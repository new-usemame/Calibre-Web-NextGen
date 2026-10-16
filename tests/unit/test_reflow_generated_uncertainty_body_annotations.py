"""Generated uncertainty markers belong in endnotes, not inside prose."""
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from cps.services.reflow.build_epub import Chapter, _runs_html
from cps.services.reflow.reading_flow import finish


pytestmark = pytest.mark.unit


def _parsed(fragment):
    return ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">' + fragment + "</root>")


def _book():
    return SimpleNamespace(figures=[], artwork=[], pages={})


def _flatten(node):
    return "".join(node.itertext())


@pytest.mark.parametrize(
    ("left", "number", "right", "expected_body"),
    [
        ('The dial at 25', "0", ' degrees marks its last notch', 'The dial at 250 degrees marks its last notch'),
        ("A source sentence", "21", " continues here", "A source sentence21 continues here"),
    ],
)
def test_generated_uncertainty_is_moved_out_of_source_sentence(left, number, right, expected_body):
    """Only generated uncertainty copy moves; the source marker stays in its lane."""

    marker = _runs_html([("t", left), ("ref", number, None, "uncertain"), ("t", right)], set(), {})
    chapter = Chapter(0, "Source passage", ["<p>" + marker + "</p>"])

    result = finish([chapter], _book())

    body = _parsed(chapter.blocks[0])[0]
    assert _flatten(body) == expected_body
    assert " (?)" not in _flatten(body)
    marker = next(node for node in body.iter() if node.tag.rsplit("}", 1)[-1] == "sup")
    assert _flatten(marker) == number  # the source marker keeps its superscript and exact digit
    notes = _parsed(chapter.blocks[-1])[0]
    assert notes.get("class") == "source-notes"
    annotation = next(node for node in notes.iter() if "source-reading-annotation" in node.get("class", "").split())
    annotation_text = _flatten(annotation) + annotation.get("title", "")
    assert "(?)" in annotation_text
    assert "damaged text layer" in annotation_text
    assert result["trailing_disclosures"] == 1


def test_authored_question_mark_literal_stays_in_body_while_generated_copy_moves():
    """The source's own '(?)' punctuation is body text, not generated disclosure."""

    generated = _runs_html([("ref", "21", None, "uncertain")], set(), {})
    chapter = Chapter(0, "Source passage", [
        "<p>It was " + generated + " afterward.</p>",
        "<p>In the source wording: carefully(?) painted.</p>",
    ])

    finish([chapter], _book())

    body_text = " ".join(_flatten(_parsed(block)[0]) for block in chapter.blocks[:-1])
    assert "It was 21 afterward." in body_text
    assert "carefully(?) painted" in body_text
    notes = _parsed(chapter.blocks[-1])[0]
    annotation_text = [
        _flatten(node) for node in notes.iter()
        if "source-reading-annotation" in node.get("class", "").split()
    ]
    assert len(annotation_text) == 1
    assert "(?)" in annotation_text[0]


def test_bound_uncertain_marker_keeps_its_existing_reference_link():
    """The separate bound-reference route stays a linked superscript, without generated '(?)'."""
    html = _runs_html([("ref", "21", None, "uncertain")], {"21"}, {})
    node = ET.fromstring('<div xmlns:epub="http://www.idpf.org/2007/ops">' + html + "</div>")
    link = next(child for child in node.iter() if child.tag == "a")
    assert link.get("href") == "#fn_21"
    assert _flatten(node) == "21"
    assert " (?)" not in html
