# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# Open Library metadata provider.
#
# API docs: https://openlibrary.org/dev/docs/api/search
# - Search:  https://openlibrary.org/search.json?q=...&fields=...&limit=N
# - Covers:  https://covers.openlibrary.org/b/id/{cover_id}-L.jpg
#            https://covers.openlibrary.org/b/olid/{olid}-L.jpg
#
# Open Library is the Internet Archive's public book catalog. Free to query,
# no API key, generous rate limits (~100 req/min/IP). Returns ISBN, OLID,
# work key, edition keys, language, publisher, year, subjects.

from typing import Dict, List, Optional
from urllib.parse import quote

import requests

from cps import constants
from cps.isoLanguages import get_language_name
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata, ProviderRefused


class OpenLibrary(Metadata):
    __name__ = "Open Library"
    __id__ = "openlibrary"
    DESCRIPTION = "Open Library"
    META_URL = "https://openlibrary.org/"

    SEARCH_URL = "https://openlibrary.org/search.json"
    BOOK_URL_TEMPLATE = "https://openlibrary.org{key}"  # key starts with /works/...
    COVER_BY_ID_URL = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
    COVER_BY_OLID_URL = "https://covers.openlibrary.org/b/olid/{olid}-L.jpg"

    SEARCH_FIELDS = ",".join([
        "key", "title", "author_name", "first_publish_year",
        "language", "isbn", "publisher", "cover_i", "cover_edition_key",
        "edition_key", "number_of_pages_median", "subject",
    ])

    SEARCH_LIMIT = 10
    REQUEST_TIMEOUT = 12

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        if not self.active:
            return []

        title_tokens = list(self.get_title_tokens(query, strip_joiners=False))
        if not title_tokens:
            return []
        q = "+".join(quote(t.encode("utf-8")) for t in title_tokens)

        url = (
            f"{OpenLibrary.SEARCH_URL}?q={q}"
            f"&fields={OpenLibrary.SEARCH_FIELDS}"
            f"&limit={OpenLibrary.SEARCH_LIMIT}"
        )
        headers = {"User-Agent": constants.USER_AGENT}

        response = requests.get(url, headers=headers, timeout=OpenLibrary.REQUEST_TIMEOUT)
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError:
            raise ProviderRefused("error", "Open Library returned an invalid response.") from None

        results: List[MetaRecord] = []
        for doc in payload.get("docs", []) or []:
            record = self._parse_doc(doc, generic_cover=generic_cover, locale=locale)
            if record is not None:
                results.append(record)
        return results

    def _parse_doc(
        self, doc: Dict, generic_cover: str, locale: str
    ) -> Optional[MetaRecord]:
        title = doc.get("title")
        work_key = doc.get("key")
        if not title or not work_key:
            return None

        record = MetaRecord(
            id=work_key,
            title=title,
            authors=list(doc.get("author_name") or []),
            url=OpenLibrary.BOOK_URL_TEMPLATE.format(key=work_key),
            source=MetaSourceInfo(
                id=self.__id__,
                description=OpenLibrary.DESCRIPTION,
                link=OpenLibrary.META_URL,
            ),
        )
        record.cover = self._cover_url(doc, generic_cover=generic_cover)
        record.publisher = self._first_or_none(doc.get("publisher"))
        year = doc.get("first_publish_year")
        record.publishedDate = str(year) if year else ""
        record.languages = self._parse_languages(doc, locale=locale)
        record.tags = self._truncate_subjects(doc.get("subject"))
        record.identifiers = self._parse_identifiers(doc)
        return record

    @staticmethod
    def _cover_url(doc: Dict, generic_cover: str) -> str:
        cover_id = doc.get("cover_i")
        if cover_id:
            return OpenLibrary.COVER_BY_ID_URL.format(cover_id=cover_id)
        olid = doc.get("cover_edition_key")
        if olid:
            return OpenLibrary.COVER_BY_OLID_URL.format(olid=olid)
        return generic_cover

    @staticmethod
    def _first_or_none(value):
        if isinstance(value, list) and value:
            return value[0]
        return value or None

    @staticmethod
    def _parse_languages(doc: Dict, locale: str) -> List[str]:
        codes = doc.get("language") or []
        languages: List[str] = []
        for code3 in codes[:5]:
            try:
                name = get_language_name(locale, code3)
            except Exception:
                name = None
            if name and name not in languages:
                languages.append(name)
        return languages

    @staticmethod
    def _truncate_subjects(subjects) -> List[str]:
        if not subjects:
            return []
        return list(subjects[:8])

    @staticmethod
    def _parse_identifiers(doc: Dict) -> Dict[str, str]:
        identifiers: Dict[str, str] = {}
        # Pick a representative ISBN-13 first, fall back to ISBN-10.
        isbn = OpenLibrary._first_or_none(doc.get("isbn"))
        if isbn:
            identifiers["isbn"] = isbn
        # OLID (work key trimmed of the /works/ prefix) gives the Open Library
        # work URL in calibre conventions.
        work_key = doc.get("key") or ""
        if work_key.startswith("/works/"):
            identifiers["openlibrary"] = work_key.split("/", 2)[-1]
        return identifiers
