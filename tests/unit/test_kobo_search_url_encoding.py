# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Kobo search URLs are percent-encoded.

Household log 2026-09-10: "Kobo search failed for
https://www.kobo.com/us/en/search?query=Wuthering+Heights+Emily+Brontë&fcmedia=Book:
HTTP Error 400" on both the primary and the fallback URL. The query tokens
were joined raw, so any non-ASCII author or title produced an invalid URL and
the source answered nothing for exactly the books that need it most.
"""
import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("build", ["_build_search_url", "_build_fallback_url"])
def test_non_ascii_query_is_percent_encoded(build):
    from cps.metadata_provider.kobo import Kobo

    provider = Kobo()
    if build == "_build_search_url":
        url = provider._build_search_url("Wuthering Heights Emily Brontë", "en")
    else:
        url = provider._build_fallback_url("Wuthering Heights Emily Brontë")
    assert url.isascii(), url
    assert "Bront%C3%AB" in url
    assert "query=Wuthering+Heights+Emily+Bront%C3%AB" in url
