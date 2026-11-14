# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# Apple Books / iTunes Search API metadata provider.
#
# API docs: https://performance-partners.apple.com/search-api
# - Lookup by ISBN:  https://itunes.apple.com/lookup?isbn=<ISBN>&country=us&media=ebook
# - Title search:    https://itunes.apple.com/search?term=<q>&country=us&media=ebook&entity=ebook
#
# No auth, no API key, ~20 req/min/IP soft limit. Apple's image CDN serves
# arbitrary sizes by rewriting the path segment ``100x100bb.jpg`` to e.g.
# ``1500x1500bb.jpg``. The 1500-px variant aligns with the cover_booster's
# _HIGHRES_HINTS pattern so the booster doesn't waste cycles re-upgrading
# this provider's results.

import re
from typing import Dict, List, Optional
from urllib.parse import quote

import requests

from cps import constants, logger
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata
from cps.services.isbn import is_valid as isbn_is_valid


log = logger.create()


class AppleBooks(Metadata):
    __name__ = "Apple Books"
    __id__ = "applebooks"
    DESCRIPTION = "Apple Books"
    META_URL = "https://books.apple.com/"

    LOOKUP_URL = "https://itunes.apple.com/lookup"
    SEARCH_URL = "https://itunes.apple.com/search"
    REQUEST_TIMEOUT = 10
    SEARCH_LIMIT = 10

    # iTunes returns artwork URLs ending in /<W>x<H>bb.jpg or .png. Rewrite
    # to 1500-px so we match cover_booster._HIGHRES_HINTS.
    _ARTWORK_SIZE_RE = re.compile(r"/\d+x\d+bb\.(jpg|png)$")
    _ARTWORK_SIZE_REPL = r"/1500x1500bb.\1"

    _ISBN_DIGITS_RE = re.compile(r"[^0-9Xx]")

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        if not self.active:
            return []

        query = (query or "").strip()
        if not query:
            return []

        items = self._lookup_isbn(query) if self._looks_like_isbn(query) else None
        if not items:
            items = self._search(query)
        if not items:
            return []

        records: List[MetaRecord] = []
        for item in items:
            record = self._parse_item(item, generic_cover=generic_cover)
            if record is not None:
                records.append(record)
        return records

    def _looks_like_isbn(self, query: str) -> bool:
        digits = self._ISBN_DIGITS_RE.sub("", query)
        return len(digits) in (10, 13) and isbn_is_valid(digits)

    def _lookup_isbn(self, query: str) -> List[Dict]:
        isbn = self._ISBN_DIGITS_RE.sub("", query)
        params = {"isbn": isbn, "country": "us", "media": "ebook"}
        return self._call(self.LOOKUP_URL, params)

    def _search(self, query: str) -> List[Dict]:
        params = {
            "term": query,
            "country": "us",
            "media": "ebook",
            "entity": "ebook",
            "limit": self.SEARCH_LIMIT,
        }
        return self._call(self.SEARCH_URL, params)

    def _call(self, url: str, params: Dict) -> List[Dict]:
        try:
            response = requests.get(
                url,
                params=params,
                headers={"User-Agent": getattr(constants, "USER_AGENT", "Calibre-Web")},
                timeout=AppleBooks.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json() or {}
        except requests.RequestException as exc:
            log.warning("Apple Books request to %s failed: %s", url, exc)
            return []
        except ValueError as exc:
            log.warning("Apple Books returned invalid JSON: %s", exc)
            return []
        return [r for r in payload.get("results", []) if isinstance(r, dict)]

    def _parse_item(self, item: Dict, generic_cover: str) -> Optional[MetaRecord]:
        kind = item.get("kind") or item.get("wrapperType")
        if kind not in ("ebook", "audiobook"):
            return None

        title = item.get("trackName") or item.get("trackCensoredName") or item.get("collectionName")
        track_id = item.get("trackId") or item.get("collectionId")
        if not title or not track_id:
            return None

        record = MetaRecord(
            id=str(track_id),
            title=title,
            authors=self._parse_authors(item),
            url=item.get("trackViewUrl") or item.get("collectionViewUrl") or AppleBooks.META_URL,
            source=MetaSourceInfo(
                id=self.__id__,
                description=AppleBooks.DESCRIPTION,
                link=AppleBooks.META_URL,
            ),
        )
        record.cover = self._parse_cover(item, generic_cover=generic_cover)
        record.description = item.get("description") or ""
        record.publishedDate = self._parse_published_date(item)
        record.tags = self._parse_genres(item)
        record.rating = 0
        record.series, record.series_index = "", 1
        record.identifiers = {"apple": str(track_id)}
        return record

    @staticmethod
    def _parse_authors(item: Dict) -> List[str]:
        artist = item.get("artistName") or ""
        if not artist:
            return []
        # Apple joins multi-author names with ", " or " & ". Splitting on those
        # separators is the convention the rest of the providers use.
        parts = re.split(r"\s*&\s*|\s*,\s*|\s+and\s+", artist)
        return [p.strip() for p in parts if p.strip()]

    @classmethod
    def _parse_cover(cls, item: Dict, generic_cover: str) -> str:
        artwork = (
            item.get("artworkUrl512")
            or item.get("artworkUrl100")
            or item.get("artworkUrl60")
            or item.get("artworkUrl30")
        )
        if not artwork:
            return generic_cover
        return cls._ARTWORK_SIZE_RE.sub(cls._ARTWORK_SIZE_REPL, artwork)

    @staticmethod
    def _parse_published_date(item: Dict) -> str:
        # Apple sends ISO-8601 with a Z suffix, e.g. "2008-12-05T08:00:00Z".
        # The rest of the metadata pipeline expects "YYYY-MM-DD".
        raw = item.get("releaseDate") or ""
        match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
        return match.group(1) if match else ""

    @staticmethod
    def _parse_genres(item: Dict) -> List[str]:
        genres = item.get("genres") or []
        if not isinstance(genres, list):
            return []
        # Apple includes a meta-tag "Books" on every result; drop it as a tag
        # since it's redundant.
        return [g for g in genres if isinstance(g, str) and g and g != "Books"][:8]
