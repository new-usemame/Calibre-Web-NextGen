"""#1902: the auto-resolve preview shows publisher and year so an admin can
tell different editions (e.g. two translations) from true duplicate copies."""
from datetime import datetime
from types import SimpleNamespace

from cps.duplicates import _preview_edition_info


def _book(publishers=(), pubdate=None):
    return SimpleNamespace(publishers=[SimpleNamespace(name=n) for n in publishers], pubdate=pubdate)


def test_publisher_and_year_are_reported():
    info = _preview_edition_info(_book(['Penguin Classics'], datetime(2003, 5, 1)))
    assert info == {'publisher': 'Penguin Classics', 'year': 2003}


def test_missing_values_are_none():
    assert _preview_edition_info(_book()) == {'publisher': None, 'year': None}


def test_calibre_default_pubdate_is_not_a_year():
    # Calibre stores 0101-01-01 for "no publication date".
    assert _preview_edition_info(_book(['OUP'], datetime(101, 1, 1)))['year'] is None


def test_never_raises_on_odd_objects():
    assert _preview_edition_info(object()) == {'publisher': None, 'year': None}
