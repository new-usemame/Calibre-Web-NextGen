# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Fork issue #638: the Kobo provider parsed an ISBN out of both the
__NEXT_DATA__ book object and the LD-JSON block, then dropped it on the
floor - every Kobo record reached the cover booster ISBN-less, forcing
the fuzzy iTunes title-search path (Path C) instead of the edition-keyed
ISBN paths (A/B). These tests pin that a parsed ISBN survives into the
MetaRecord's identifiers dict.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest


def _load_kobo_module():
    """Load cps/metadata_provider/kobo.py without the heavy cps package init.

    Shims cps.constants / cps.logger / cps.isoLanguages, loads the real
    (lightweight) cps/services/Metadata.py for MetaRecord, then execs
    kobo.py directly.
    """
    repo_root = Path(__file__).resolve().parents[2]

    if "cps.isoLanguages" not in sys.modules:
        cps_pkg = sys.modules.get("cps") or types.ModuleType("cps")
        cps_pkg.__path__ = [str(repo_root / "cps")]
        constants = types.ModuleType("cps.constants")
        constants.USER_AGENT = "Calibre-Web-NextGen-tests"
        constants.STATIC_DIR = str(repo_root / "cps" / "static")
        logger_mod = types.ModuleType("cps.logger")
        logger_mod.create = lambda *_a, **_k: types.SimpleNamespace(
            debug=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
            info=lambda *_args, **_kwargs: None,
            error=lambda *_args, **_kwargs: None,
        )
        iso_mod = types.ModuleType("cps.isoLanguages")
        iso_mod.get_lang3 = lambda code: code
        iso_mod.get_language_name = lambda _locale, code: code
        cps_pkg.constants = constants
        cps_pkg.logger = logger_mod
        cps_pkg.isoLanguages = iso_mod
        sys.modules["cps"] = cps_pkg
        sys.modules["cps.constants"] = constants
        sys.modules["cps.logger"] = logger_mod
        sys.modules["cps.isoLanguages"] = iso_mod
        services_pkg = types.ModuleType("cps.services")
        services_pkg.__path__ = [str(repo_root / "cps" / "services")]
        sys.modules["cps.services"] = services_pkg

    if "cps.services.Metadata" not in sys.modules:
        meta_spec = importlib.util.spec_from_file_location(
            "cps.services.Metadata", repo_root / "cps" / "services" / "Metadata.py"
        )
        meta_mod = importlib.util.module_from_spec(meta_spec)
        sys.modules["cps.services.Metadata"] = meta_mod
        meta_spec.loader.exec_module(meta_mod)

    spec = importlib.util.spec_from_file_location(
        "cps.metadata_provider.kobo_under_test",
        repo_root / "cps" / "metadata_provider" / "kobo.py",
    )
    module = importlib.util.module_from_spec(spec)
    # Register before exec: cps.logger.create() resolves the caller via
    # inspect.getmodule, which needs the module present in sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


kobo_mod = _load_kobo_module()


def _provider():
    return kobo_mod.Kobo.__new__(kobo_mod.Kobo)


NEXT_DATA_DETAIL_HTML = """
<html><head>
<script id="__NEXT_DATA__" type="application/json">
{"props": {"pageProps": {"searchResultSSR": {"Items": [
  {"Book": {
    "Title": "Otome Game World Vol.2",
    "Slug": "otome-game-world-vol-2",
    "ISBN": "9781974736911",
    "ContributorRoles": [{"Role": "Author", "Name": "Toyozo Okamura"}],
    "PublisherName": "Seven Seas",
    "LongDescription": "Volume two of the series.",
    "SeriesName": "Otome Game World",
    "SeriesNumber": 2,
    "PublicationDate": "2024-03-01",
    "ImageUrl": "https://cdn.kobo.com/book-images/vol2/1200/1200/90/False/img.jpg"
  }}
]}}}}
</script>
</head><body></body></html>
"""

LD_JSON_HTML = """
<html><head>
<script type="application/ld+json">
{"@type": "Book",
 "name": "Otome Game World Vol.2",
 "author": {"name": "Toyozo Okamura"},
 "isbn": "978-1-9747-3691-1",
 "description": "Volume two of the series.",
 "inLanguage": "en",
 "image": "https://cdn.kobo.com/book-images/vol2/353/569/90/False/img.jpg",
 "publisher": {"name": "Seven Seas"},
 "datePublished": "2024-03-01"}
</script>
</head><body></body></html>
"""


@pytest.mark.unit
class TestNextDataIsbnRetention:
    def test_next_data_detail_extracts_isbn(self):
        provider = _provider()
        soup = kobo_mod.BS(NEXT_DATA_DETAIL_HTML, "lxml")
        out = provider._parse_next_data_detail(
            soup, "https://www.kobo.com/us/en/ebook/otome-game-world-vol-2"
        )
        assert out.get("isbn") == "9781974736911"

    def test_invalid_isbn_is_not_kept(self):
        provider = _provider()
        html = NEXT_DATA_DETAIL_HTML.replace("9781974736911", "not-an-isbn")
        soup = kobo_mod.BS(html, "lxml")
        out = provider._parse_next_data_detail(
            soup, "https://www.kobo.com/us/en/ebook/otome-game-world-vol-2"
        )
        assert not out.get("isbn")


@pytest.mark.unit
class TestLdJsonIsbnRetention:
    def test_ld_json_keeps_validated_isbn(self):
        provider = _provider()
        soup = kobo_mod.BS(LD_JSON_HTML, "lxml")
        out = provider._parse_ld_json(soup)
        assert out.get("isbn") == "9781974736911"


@pytest.mark.unit
class TestFetchDetailIdentifiers:
    """The parsed ISBN must land in MetaRecord.identifiers, which is the
    dict the cover booster keys on (post-asdict)."""

    def _fetch(self, html):
        provider = _provider()
        response = types.SimpleNamespace(
            text=html, raise_for_status=lambda: None
        )
        with patch.object(kobo_mod.Kobo, "_get", return_value=response), \
             patch.object(kobo_mod.Kobo, "_headers_for_locale", return_value={}), \
             patch.object(kobo_mod.Kobo, "_apply_cookies", side_effect=lambda h: h):
            return provider._fetch_detail(
                "https://www.kobo.com/us/en/ebook/otome-game-world-vol-2",
                "generic.svg",
                "en",
            )

    def test_identifiers_carry_isbn_from_next_data(self):
        record = self._fetch(NEXT_DATA_DETAIL_HTML)
        assert record is not None
        assert record.identifiers.get("isbn") == "9781974736911"
        assert record.identifiers.get("kobo") == "otome-game-world-vol-2"

    def test_no_isbn_means_no_isbn_key(self):
        html = NEXT_DATA_DETAIL_HTML.replace('"ISBN": "9781974736911",', "")
        record = self._fetch(html)
        assert record is not None
        assert "isbn" not in record.identifiers
