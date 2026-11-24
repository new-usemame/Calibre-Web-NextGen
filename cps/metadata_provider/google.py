# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# Google Books api document: https://developers.google.com/books/docs/v1/using
import re
from typing import Dict, List, Optional
from urllib.parse import quote
from datetime import datetime

import requests

from cps import config
from cps.isoLanguages import get_lang3, get_language_name
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata, ProviderRefused

class Google(Metadata):
    __name__ = "Google"
    __id__ = "google"
    DESCRIPTION = "Google Books"
    META_URL = "https://books.google.com/"
    BOOK_URL = "https://books.google.com/books?id="
    SEARCH_URL = "https://www.googleapis.com/books/v1/volumes?q="
    ISBN_TYPE = "ISBN_13"

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        val = list()    
        if self.active:

            title_tokens = list(self.get_title_tokens(query, strip_joiners=False))
            if title_tokens:
                tokens = [quote(t.encode("utf-8")) for t in title_tokens]
                query = "+".join(tokens)
            url = Google.SEARCH_URL + query
            api_key = (getattr(config, "config_google_books_api_key", None) or "").strip()
            headers = {"X-Goog-Api-Key": api_key} if api_key else {}
            try:
                # Requests forwards custom headers when following redirects.
                results = requests.get(url, headers=headers, timeout=15, allow_redirects=False)
                results.raise_for_status()
            except requests.HTTPError as exc:
                raise self._http_refusal(exc.response, bool(api_key)) from None
            except requests.RequestException:
                raise ProviderRefused("error", "Could not connect to Google Books. Try again shortly.") from None
            if 300 <= results.status_code < 400:
                raise ProviderRefused("error", "Google Books returned an unexpected redirect.")
            try:
                payload = results.json()
            except ValueError:
                raise ProviderRefused("error", "Google Books returned an invalid response.") from None
            for result in payload.get("items", []):
                mr =self._parse_search_result(
                    result=result, generic_cover=generic_cover, locale=locale
                )
                if mr:
                    val.append(mr)
        return val

    @staticmethod
    def _http_refusal(response, has_key):
        status = response.status_code if response is not None else None
        if status == 429:
            remedy = "Google Books quota exceeded (HTTP 429)."
            if not has_key:
                remedy += " Set a Google Books API key in Configuration to get a separate quota."
            return ProviderRefused("rate_limited", remedy)
        if status in (401, 403) or (has_key and status == 400 and Google._reports_invalid_key(response)):
            if has_key:
                return ProviderRefused(
                    "missing_key", "Google Books refused the request. Check the configured API key and its restrictions."
                )
            return ProviderRefused("blocked", "Google Books refused the request. Try again shortly.")
        if status == 503:
            return ProviderRefused("blocked", "Google Books is temporarily unavailable. Try again shortly.")
        return ProviderRefused("error", f"Google Books returned HTTP {status}." if status
                               else "Google Books did not return an HTTP status.")

    @staticmethod
    def _reports_invalid_key(response):
        try:
            details = response.json()["error"]["details"]
            return any(isinstance(detail, dict) and detail.get("reason") == "API_KEY_INVALID"
                       for detail in details)
        except (KeyError, TypeError, ValueError):
            return False

    def _parse_search_result(
        self, result: Dict, generic_cover: str, locale: str
    ) -> MetaRecord|None:
        volume_info = result.get("volumeInfo", {})
        if "title" not in volume_info:
            return None

        match = MetaRecord(
            id=result["id"],
            title=volume_info["title"],
            authors=volume_info.get("authors", []),
            url=Google.BOOK_URL + result["id"],
            source=MetaSourceInfo(
                id=self.__id__,
                description=Google.DESCRIPTION,
                link=Google.META_URL,
            ),
        )

        match.cover = self._parse_cover(result=result, generic_cover=generic_cover)
        match.description = volume_info.get("description", "")
        match.languages = self._parse_languages(result=result, locale=locale)
        match.publisher = volume_info.get("publisher", "")
        try:
            datetime.strptime(volume_info.get("publishedDate", ""), "%Y-%m-%d")
            match.publishedDate = volume_info.get("publishedDate", "")
        except ValueError:
            match.publishedDate = ""
        match.rating = volume_info.get("averageRating", 0)
        match.series, match.series_index = "", 1
        match.tags = volume_info.get("categories", [])

        match.identifiers = {"google": match.id}
        match = self._parse_isbn(result=result, match=match)
        return match

    @staticmethod
    def _parse_isbn(result: Dict, match: MetaRecord) -> MetaRecord:
        identifiers = result["volumeInfo"].get("industryIdentifiers", [])
        for identifier in identifiers:
            if identifier.get("type") == Google.ISBN_TYPE:
                match.identifiers["isbn"] = identifier.get("identifier")
                break
        return match

    @staticmethod
    def _parse_cover(result: Dict, generic_cover: str) -> str:
        image_links = result["volumeInfo"].get("imageLinks") or {}
        if not image_links:
            return generic_cover

        # Prefer the largest source URL Google supplies; thumbnail is the
        # smallest. extraLarge is rarely present but worth checking when it is.
        cover_url = (
            image_links.get("extraLarge")
            or image_links.get("large")
            or image_links.get("medium")
            or image_links.get("small")
            or image_links.get("thumbnail")
            or image_links.get("smallThumbnail")
        )
        if not cover_url:
            return generic_cover

        # Strip the small-thumbnail decorations Google injects by default.
        cover_url = cover_url.replace("&edge=curl", "")
        cover_url = re.sub(r"&zoom=\d+", "", cover_url)

        # fife=w<W>-h<H> asks the Google Books image proxy for that size.
        # Google returns the source image when the requested dimensions
        # exceed what's available, so a generous request is a strict win.
        # 1600x2400 covers the Kobo Libra Color (1264x1680) plus 2x retina
        # phone/tablet displays without forcing a browser upscale.
        if "fife=" in cover_url:
            cover_url = re.sub(
                r"fife=w\d+(?:-h\d+)?", "fife=w1600-h2400", cover_url
            )
        else:
            sep = "&" if "?" in cover_url else "?"
            cover_url = f"{cover_url}{sep}fife=w1600-h2400"

        return cover_url.replace("http://", "https://")

    @staticmethod
    def _parse_languages(result: Dict, locale: str) -> List[str]:
        language_iso2 = result["volumeInfo"].get("language", "")
        languages = (
            [get_language_name(locale, get_lang3(language_iso2))]
            if language_iso2
            else []
        )
        return languages
