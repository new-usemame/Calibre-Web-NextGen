"""A sparse bibliographic title composition is one source reading unit."""
import re
import zipfile

import pymupdf
import pytest

from cps.services.reflow import build_epub, pipeline


def _center(page, y, value, size):
    width = pymupdf.get_text_length(value, fontsize=size)
    page.insert_text(((page.rect.width - width) / 2, y), value, fontsize=size)


@pytest.mark.parametrize('authored_outline', [False, True])
def test_title_after_blank_cover_keeps_styled_parts_in_one_chapter(tmp_path, authored_outline):
    doc = pymupdf.open()
    doc.new_page(width=450, height=650)
    title = doc.new_page(width=450, height=650)
    for y, value, size in ((126, 'A STUDY OF THE SKY:', 27),
                           (160, 'AND ITS HISTORY', 27),
                           (264, 'A Practical Guide', 19),
                           (405, 'A. Writer', 18),
                           (485, 'Printed By Example', 14),
                           (535, 'City 2026', 14)):
        _center(title, y, value, size)
    following = doc.new_page(width=450, height=650)
    following.insert_text((65, 105), 'CONTENTS', fontsize=20)
    for i in range(14):
        following.insert_text((65, 155 + 24 * i), 'Chapter %d and its subject' % i, fontsize=11)
    if authored_outline:
        doc.set_toc([[1, 'Printed title destination', 2], [1, 'Contents destination', 3]])
    result = pipeline.run(doc, client=None, recovery_opts={'mode': 'off'})
    assert list(result.book.title_pages) == [1]
    target = tmp_path / 'title.epub'
    build_epub.build(result.book, str(target), page_html=result.page_html, doc=doc)
    assert build_epub.validate(str(target)) == []
    with zipfile.ZipFile(target) as archive:
        bodies = [archive.read(name).decode() for name in sorted(archive.namelist())
                  if re.fullmatch(r'OEBPS/ch\d+\.xhtml', name)]
        title_bodies = [body for body in bodies if 'id="pg_0001"' in body]
        assert len(title_bodies) == 1
        assert all(value in title_bodies[0] for value in (
            'A STUDY OF THE SKY:', 'AND ITS HISTORY', 'A Practical Guide',
            'A. Writer', 'Printed By Example', 'City 2026'))
        assert 'id="pg_0002"' not in title_bodies[0]
        assert sum('id="pg_0002"' in body for body in bodies) == 1
        nav = archive.read('OEBPS/nav.xhtml').decode()
        assert 'A. Writer' not in nav and 'Printed By Example' not in nav
        assert ('Printed title destination' if authored_outline else 'A STUDY OF THE SKY:') in nav
    doc.close()


def test_prose_and_two_competing_display_sections_are_not_title_units():
    doc = pymupdf.open()
    prose = doc.new_page(width=450, height=650)
    _center(prose, 120, 'AN OPENING ESSAY', 25)
    prose.insert_text((45, 215), 'This introductory chapter begins with actual continuous prose.', fontsize=11)
    prose.insert_text((45, 242), 'Its content should remain a normal chapter opening.', fontsize=11)
    sections = doc.new_page(width=450, height=650)
    _center(sections, 100, 'FIRST SECTION', 25)
    _center(sections, 340, 'SECOND SECTION', 25)
    _center(sections, 400, 'A second separate subject', 18)
    result = pipeline.run(doc, client=None, recovery_opts={'mode': 'off'})
    assert not result.book.title_pages
    doc.close()


def test_late_standalone_display_does_not_become_front_matter_title():
    doc = pymupdf.open()
    for _ in range(7):
        doc.new_page(width=450, height=650)
    late = doc.new_page(width=450, height=650)
    for y, value, size in ((120, 'A NEW PART', 27), (170, 'ANOTHER TOPIC', 27),
                           (350, 'By a Contributor', 18), (490, 'Series detail', 14)):
        _center(late, y, value, size)
    result = pipeline.run(doc, client=None, recovery_opts={'mode': 'off'})
    assert not result.book.title_pages
    doc.close()
