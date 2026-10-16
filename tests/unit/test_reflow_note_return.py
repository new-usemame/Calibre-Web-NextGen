"""Note navigation preserves the synthetic source's printed word inventory."""

import collections
from xml.etree import ElementTree as ET

import pytest

from cps.services.reflow import assemble, build_epub, gate

pytestmark = pytest.mark.unit


def test_number_only_backlink_returns_to_its_reference_without_adding_words():
    paragraph = assemble.Element(
        kind="p", runs=[["t", "A synthetic statement."], ["n", "7"]], pno=0)
    note = assemble.Note(num=7, text="A synthetic explanation.", pno=0, marked=True)
    book = assemble.Book(elements=[paragraph], pages={0: [paragraph]}, notes=[note])

    fragment = build_epub.page_fragment(book, 0)
    source = assemble.page_source_text(book, 0)
    assert collections.Counter(gate.normalise(fragment, markup=True)) == \
        collections.Counter(gate.normalise(source))
    verdict = gate.check_word_preservation(source, fragment)
    assert verdict.verdict == "PASS", verdict.as_dict()

    root = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">' + fragment + '</root>')
    reference = root.find('.//a[@class="noteref"]')
    aside = root.find('.//aside[@class="footnote"]')
    back = aside.find('.//a[@class="note-return"]')
    assert "".join(back.itertext()) == "7"
    assert back.get("aria-label") == "Return to note reference"
    assert back.get("href") == "#" + reference.get("id")
    assert reference.get("href") == "#" + aside.get("id")
    structure = gate.check_structure(fragment)
    assert structure.ok, structure.reasons


def test_uncertain_note_number_stays_inside_the_backlink():
    note = assemble.Note(num=7, text="A synthetic explanation.", pno=0, uncertain=True)
    fragment = build_epub._aside_html(note, {"7": "fnref_7"}, {"7"})
    root = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">' + fragment + '</root>')
    back = root.find('.//a[@class="note-return"]')
    assert "".join(back.itertext()) == "7 (?)"
    assert back.find('span[@class="reflow-uncertain"]').text == "7 (?)"
    assert not gate.check_markup_safety(fragment)


@pytest.mark.parametrize("fragment", [
    '<a class="note-return" href="#fnref_7" aria-label="Return to note reference">7</a>',
    '<aside><a class="note-return" href="#fnref_7" aria-label="Return to note reference">7</a></aside>',
    '<aside class="footnote"></aside><a class="note-return" href="#fnref_7" aria-label="Return to note reference">7</a>',
    '<aside class="footnote"/><a class="note-return" href="#fnref_7" aria-label="Return to note reference">7</a>',
    '<aside class="footnote"><a class="noteref" href="#fn_7" aria-label="Return to note reference">7</a></aside>',
    '<aside class="footnote"><p aria-label="Return to note reference">7</p></aside>',
    '<aside class="footnote"><a class="note-return" href="#fnref_7">7</a></aside>',
    '<aside class="footnote"><a class="note-return" href="#fnref_7" aria-label="Different label">7</a></aside>',
    '<aside class="footnote"><a class="note-return" href="https://example.invalid" aria-label="Return to note reference">7</a></aside>',
])
def test_backlink_accessible_label_is_allowed_only_for_the_footnote_contract(fragment):
    assert gate.check_markup_safety(fragment)
