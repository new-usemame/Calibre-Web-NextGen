# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Unit tests for the cover-resolution booster."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest


def _load_cover_booster_module():
    """Load cps/services/cover_booster.py without triggering the package init.

    The full `cps` package import has heavy side effects (CWA login, database
    bootstrap, etc.). We only need this one file, so we wire up shim parents
    in sys.modules and exec the file directly.
    """
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "cps" / "services" / "cover_booster.py"

    if "cps" not in sys.modules:
        cps_pkg = types.ModuleType("cps")
        cps_pkg.__path__ = [str(repo_root / "cps")]
        constants = types.ModuleType("cps.constants")
        constants.USER_AGENT = "Calibre-Web-NextGen-tests"
        logger_mod = types.ModuleType("cps.logger")
        logger_mod.create = lambda *_a, **_k: types.SimpleNamespace(
            debug=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
            info=lambda *_args, **_kwargs: None,
            error=lambda *_args, **_kwargs: None,
        )
        cps_pkg.constants = constants
        cps_pkg.logger = logger_mod
        sys.modules["cps"] = cps_pkg
        sys.modules["cps.constants"] = constants
        sys.modules["cps.logger"] = logger_mod
        services_pkg = types.ModuleType("cps.services")
        services_pkg.__path__ = [str(repo_root / "cps" / "services")]
        sys.modules["cps.services"] = services_pkg

    spec = importlib.util.spec_from_file_location(
        "cps.services.cover_booster", module_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["cps.services.cover_booster"] = module
    spec.loader.exec_module(module)
    return module


cover_booster = _load_cover_booster_module()


@pytest.mark.unit
class TestIsbn10Conversion:
    """ISBN-13 -> ISBN-10 conversion. Amazon's image CDN is keyed by
    ISBN-10/ASIN, so records carrying only an ISBN-13 still need a path
    to a 10-digit form."""

    def test_isbn10_passthrough(self):
        assert cover_booster._to_isbn10("1853260010") == "1853260010"

    def test_isbn10_with_x_check_digit(self):
        assert cover_booster._to_isbn10("097522980x") == "097522980X"

    def test_isbn13_with_978_prefix(self):
        # 978-1-85326-001-? -> ISBN-10 1853260010
        assert cover_booster._to_isbn10("9781853260018") == "1853260010"

    def test_isbn13_strips_separators(self):
        assert cover_booster._to_isbn10("978-1-85326-001-8") == "1853260010"

    def test_isbn13_with_979_prefix_returns_none(self):
        # 979-prefixed ISBN-13s have no ISBN-10 form. Amazon keys some books
        # by ASIN in this case, but the ISBN-10 path can't help us here.
        assert cover_booster._to_isbn10("9791234567896") is None

    def test_garbage_input_returns_none(self):
        assert cover_booster._to_isbn10("") is None
        assert cover_booster._to_isbn10("not-an-isbn") is None
        assert cover_booster._to_isbn10("12345") is None


@pytest.mark.unit
class TestYearFrom:
    def test_year_from_iso_date(self):
        assert cover_booster._year_from("2008-12-05") == 2008

    def test_year_from_year_only(self):
        assert cover_booster._year_from("1992") == 1992

    def test_year_from_full_iso_with_time(self):
        assert cover_booster._year_from("2008-12-05T08:00:00Z") == 2008

    def test_year_from_empty(self):
        assert cover_booster._year_from("") is None
        assert cover_booster._year_from(None) is None


@pytest.mark.unit
class TestAmazonCdnProbe:
    """The Amazon image CDN serves a 43-byte image/gif placeholder for
    unknown ASINs; real covers are image/jpeg measured in tens of KB.
    The probe must distinguish the two."""

    def _mock_head(self, status, content_type, content_length):
        response = types.SimpleNamespace(
            status_code=status,
            headers={"content-type": content_type, "content-length": str(content_length)},
        )
        return patch.object(cover_booster.requests, "head", return_value=response)

    def test_real_jpeg_cover_returns_url(self):
        with self._mock_head(200, "image/jpeg", 250000):
            url = cover_booster._amazon_cdn_cover_for_isbn10("1853260010")
        assert url == "https://m.media-amazon.com/images/P/1853260010.01._SCRM_SL2000_.jpg"

    def test_placeholder_gif_returns_none(self):
        with self._mock_head(200, "image/gif", 43):
            assert cover_booster._amazon_cdn_cover_for_isbn10("0000000000") is None

    def test_undersized_jpeg_returns_none(self):
        # Ranks below the placeholder threshold; treat as suspect.
        with self._mock_head(200, "image/jpeg", 1024):
            assert cover_booster._amazon_cdn_cover_for_isbn10("1234567890") is None

    def test_404_returns_none(self):
        with self._mock_head(404, "text/html", 0):
            assert cover_booster._amazon_cdn_cover_for_isbn10("1234567890") is None

    def test_request_exception_returns_none(self):
        with patch.object(
            cover_booster.requests,
            "head",
            side_effect=cover_booster.requests.RequestException("boom"),
        ):
            assert cover_booster._amazon_cdn_cover_for_isbn10("1234567890") is None


@pytest.mark.unit
class TestItunesYearGuard:
    """The fuzzy iTunes title-search match must reject hits whose release
    year disagrees with the record by more than ±20 years - otherwise a
    1992 Wordsworth print edition gets its cover replaced by a 2008
    Penguin ebook of the same title."""

    def _hit(self, track="Wuthering Heights", artist="Emily Bronte", year="2008-12-05"):
        return {
            "trackName": track,
            "artistName": artist,
            "releaseDate": year,
            "kind": "ebook",
        }

    def test_year_within_window_passes(self):
        assert cover_booster._itunes_result_matches(
            "Wuthering Heights", "Emily Bronte", self._hit(year="2008-12-05"),
            record_year=2010,
        )

    def test_year_far_outside_window_rejects(self):
        assert not cover_booster._itunes_result_matches(
            "Wuthering Heights", "Emily Bronte", self._hit(year="2008-12-05"),
            record_year=1850,
        )

    def test_no_record_year_means_no_year_check(self):
        assert cover_booster._itunes_result_matches(
            "Wuthering Heights", "Emily Bronte", self._hit(year="2008-12-05"),
            record_year=None,
        )

    def test_low_token_overlap_rejects_regardless(self):
        assert not cover_booster._itunes_result_matches(
            "Wuthering Heights", "Emily Bronte",
            self._hit(track="Pride and Prejudice", artist="Jane Austen"),
            record_year=2008,
        )


@pytest.mark.unit
class TestBoostedCoverPathOrder:
    """Path A (Amazon CDN by ISBN) runs before iTunes paths so a correct
    edition cover wins over an iTunes title-search result that finds a
    different edition."""

    def test_amazon_cdn_wins_when_record_has_isbn(self):
        record = {
            "title": "Wuthering Heights",
            "authors": ["Emily Bronte"],
            "identifiers": {"isbn": "1853260010"},
            "cover": "https://covers.openlibrary.org/b/isbn/1853260010-L.jpg",
            "publishedDate": "1992",
        }
        amazon_url = "https://m.media-amazon.com/images/P/1853260010.01._SCRM_SL2000_.jpg"
        with patch.object(
            cover_booster, "_amazon_cdn_cover_for_isbn10", return_value=amazon_url
        ) as cdn_mock, patch.object(
            cover_booster, "_itunes_lookup_isbn"
        ) as itunes_lookup, patch.object(
            cover_booster, "_itunes_search"
        ) as itunes_search:
            result = cover_booster._boosted_cover_for(record)
        assert result == amazon_url
        cdn_mock.assert_called_once_with("1853260010")
        itunes_lookup.assert_not_called()
        itunes_search.assert_not_called()

    def test_itunes_title_search_skipped_when_isbn_present(self):
        # No Amazon CDN hit + iTunes ISBN-lookup miss should NOT fall back
        # to title-search when the record has an ISBN. Title-search returns
        # different-edition covers and the wrong-edition substitution is
        # exactly what we're guarding against.
        record = {
            "title": "Wuthering Heights",
            "authors": ["Emily Bronte"],
            "identifiers": {"isbn": "1853260010"},
            "cover": "https://covers.openlibrary.org/b/isbn/1853260010-L.jpg",
            "publishedDate": "1992",
        }
        with patch.object(
            cover_booster, "_amazon_cdn_cover_for_isbn10", return_value=None
        ), patch.object(
            cover_booster, "_itunes_lookup_isbn", return_value=None
        ), patch.object(
            cover_booster, "_itunes_search"
        ) as itunes_search:
            result = cover_booster._boosted_cover_for(record)
        itunes_search.assert_not_called()
        assert result is None

    def test_itunes_title_search_runs_when_no_isbn(self):
        record = {
            "title": "Wuthering Heights",
            "authors": ["Emily Bronte"],
            "identifiers": {},
            "cover": "https://example.com/some-cover.jpg",
            "publishedDate": "",
        }
        itunes_hit = {
            "trackName": "Wuthering Heights",
            "artistName": "Emily Bronte",
            "releaseDate": "2008-12-05",
            "artworkUrl100": "https://is1-ssl.mzstatic.com/img/100x100bb.jpg",
            "kind": "ebook",
        }
        with patch.object(
            cover_booster, "_itunes_search", return_value=itunes_hit
        ) as itunes_search:
            result = cover_booster._boosted_cover_for(record)
        itunes_search.assert_called_once()
        assert result and "1500x1500bb" in result


@pytest.mark.unit
class TestVolumeAwareMatching:
    """Fork issue #638: iTunes title-search collapsed every volume of a
    series onto one cover. Apple returns the same first hit for
    "<long series title> Vol.1/2/3" queries because the first-6-token
    overlap can never see the volume number (_tokenize drops tokens
    <=2 chars). The match must reject a hit whose volume designator
    disagrees with the query's."""

    def _hit(self, track, artist="Toyozo Okamura", year="2024-03-01"):
        return {
            "trackName": track,
            "artistName": artist,
            "releaseDate": year,
            "kind": "ebook",
        }

    TITLE = (
        "Love & Magic Academy: Who Cares about the Heroine and Villainess? "
        "I Want to Be the Strongest in this Otome Game World"
    )

    def test_different_volume_numbers_reject(self):
        assert not cover_booster._itunes_result_matches(
            f"{self.TITLE} Vol.2", "Toyozo Okamura",
            self._hit(f"{self.TITLE} Vol. 3"),
        )

    def test_same_volume_number_passes(self):
        assert cover_booster._itunes_result_matches(
            f"{self.TITLE} Vol.2", "Toyozo Okamura",
            self._hit(f"{self.TITLE}, Vol. 2"),
        )

    def test_query_volume_but_hit_unnumbered_rejects(self):
        assert not cover_booster._itunes_result_matches(
            f"{self.TITLE} Vol.2", "Toyozo Okamura",
            self._hit(self.TITLE),
        )

    def test_hit_volume_but_query_unnumbered_rejects(self):
        assert not cover_booster._itunes_result_matches(
            self.TITLE, "Toyozo Okamura",
            self._hit(f"{self.TITLE} Vol. 3"),
        )

    def test_volume_marker_variants_are_equivalent(self):
        # "Vol. 2", "Volume 2", "Book 2", "#2" all designate the same volume
        for marker in ("Vol. 2", "Volume 2", "Book 2", "#2"):
            assert cover_booster._itunes_result_matches(
                f"{self.TITLE} Vol.2", "Toyozo Okamura",
                self._hit(f"{self.TITLE} {marker}"),
            ), marker

    def test_no_series_name_does_not_mask_volume_marker(self):
        # Greptile P2 on #642: "No. 6" is a series NAME (Atsuko Asano);
        # the leftmost "No. 6" must not win over the real "Vol. 3" marker,
        # or every volume of the series extracts 6 on both sides and the
        # guard is neutralized.
        assert cover_booster._volume_number("No. 6 Vol. 3") == 3
        assert cover_booster._volume_number("No. 6, Volume 1") == 1

    def test_no_series_different_volumes_reject(self):
        # Pre-fix: both sides extract the series number (6 == 6), the
        # guard passes, and the surviving token overlap is trivially 100%
        # ("no"/"6"/digits all drop in _tokenize) - the exact collapse
        # this guard exists to prevent.
        assert not cover_booster._itunes_result_matches(
            "No. 6 Vol. 3", "Atsuko Asano",
            self._hit("No. 6 Vol. 1", artist="Atsuko Asano"),
        )

    def test_no_marker_alone_still_recognized(self):
        # Without a strong marker, "No. N" still designates the volume
        # and equal values on both sides still match.
        assert cover_booster._volume_number("The No. 1 Ladies' Detective Agency") == 1
        assert cover_booster._itunes_result_matches(
            "The No. 1 Ladies' Detective Agency", "Alexander McCall Smith",
            self._hit("The No. 1 Ladies' Detective Agency",
                      artist="Alexander McCall Smith"),
        )

    def test_numeric_title_both_sides_equal_passes(self):
        # Titles that ARE numbers must not self-reject
        assert cover_booster._itunes_result_matches(
            "1984", "George Orwell",
            self._hit("1984", artist="George Orwell"),
        )

    def test_unnumbered_titles_unaffected(self):
        assert cover_booster._itunes_result_matches(
            "Wuthering Heights", "Emily Bronte",
            self._hit("Wuthering Heights", artist="Emily Bronte"),
        )


@pytest.mark.unit
class TestSeriesVolumeNoCollapse:
    """End-to-end through boost_covers: three ISBN-less volumes of one
    series must NOT all converge on the single cover Apple returns first
    (the user-visible symptom in #638 - identical thumbnails for
    Vol.1/2/3 in the fetch-metadata modal)."""

    def test_boost_covers_does_not_collapse_series_volumes(self):
        base = (
            "Love & Magic Academy: Who Cares about the Heroine and "
            "Villainess? I Want to Be the Strongest in this Otome Game World"
        )
        records = [
            {
                "title": f"{base} Vol.{n}",
                "authors": ["Toyozo Okamura"],
                "identifiers": {"kobo": f"slug-vol-{n}"},
                "cover": f"https://cdn.kobo.com/book-images/vol{n}/1200/1200/90/False/img.jpg",
                "publishedDate": "2024",
            }
            for n in (1, 2, 3)
        ]
        # Apple returns Vol.3 as the FIRST hit for all three queries
        # (observed live 2026-07-03 on the /search API for this series).
        vol3_hit = {
            "trackName": f"{base} Vol. 3",
            "artistName": "Toyozo Okamura",
            "releaseDate": "2024-09-01",
            "artworkUrl100": "https://is1-ssl.mzstatic.com/img/vol3/100x100bb.jpg",
            "kind": "ebook",
        }
        with patch.object(cover_booster, "_itunes_search", return_value=vol3_hit):
            boosted = cover_booster.boost_covers(records)
        covers = [r["cover"] for r in boosted]
        # Vol.3 may legitimately take the Apple artwork; Vol.1 and Vol.2
        # must keep their original (correct) Kobo covers.
        assert "vol1" in covers[0], f"Vol.1 cover was overwritten: {covers[0]}"
        assert "vol2" in covers[1], f"Vol.2 cover was overwritten: {covers[1]}"
        assert len(set(covers)) == 3, f"covers collapsed: {covers}"
