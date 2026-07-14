# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Best-effort Goodreads metadata scraper (#303).

Maintenance note: Goodreads has no supported public API. Embedded JSON-LD is
expected to break after Goodreads changes its page data shape; search-result
links/selectors are the next likely failure. Detect rot via the recorded parser
fixtures plus a live opt-in search returning an empty provider row. Network,
anti-bot, and parse failures deliberately degrade to no results.
"""

from __future__ import annotations

import json
import re
from typing import Iterable, List, Optional
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from cps import constants, logger
from cps.services.Metadata import Metadata, MetaRecord, MetaSourceInfo

log = logger.create()


class Goodreads(Metadata):
    __name__ = "Goodreads (best effort)"
    __id__ = "goodreads"
    DESCRIPTION = __name__
    META_URL = "https://www.goodreads.com/"
    SEARCH_URL = "https://www.goodreads.com/search?q={}"
    TIMEOUT = (4, 8)
    MAX_RESULTS = 3
    HEADERS = {
        "User-Agent": constants.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.8",
    }

    def search(self, query: str, generic_cover: str = "", locale: str = "en") -> List[MetaRecord]:
        if not self.active or not isinstance(query, str) or not query.strip():
            return []
        session = requests.Session()
        session.headers.update(self.HEADERS)
        try:
            response = session.get(self.SEARCH_URL.format(quote_plus(query.strip())), timeout=self.TIMEOUT)
            response.raise_for_status()
            links = self._parse_search(response.text)
        except (requests.RequestException, ValueError, TypeError) as exc:
            log.warning("Goodreads search failed: %s", exc)
            return []

        records = []
        # Sequential by design: politely avoid parallel page hammering.
        for link in links[:self.MAX_RESULTS]:
            try:
                response = session.get(link, timeout=self.TIMEOUT)
                response.raise_for_status()
                record = self._parse_book(response.text, link, generic_cover)
                if record:
                    records.append(record)
            except (requests.RequestException, ValueError, TypeError) as exc:
                log.warning("Goodreads book fetch failed: %s", exc)
        return records

    @classmethod
    def _parse_search(cls, html: object) -> List[str]:
        if not isinstance(html, str) or not html.strip():
            return []
        soup = BeautifulSoup(html, "html.parser")
        found = []
        for anchor in soup.select('a[href*="/book/show/"]'):
            href = anchor.get("href")
            if not isinstance(href, str):
                continue
            url = urljoin(cls.META_URL, href.split("?")[0])
            if url not in found:
                found.append(url)
        return found

    @classmethod
    def _parse_book(cls, html: object, url: str, generic_cover: str = "") -> Optional[MetaRecord]:
        if not isinstance(html, str) or not html.strip():
            return None
        soup = BeautifulSoup(html, "html.parser")
        data = cls._book_json_ld(soup)
        if not data:
            return None
        title = data.get("name")
        if not isinstance(title, str) or not title.strip():
            return None
        authors = cls._names(data.get("author"))
        book_id = cls._book_id(url, data)
        record = MetaRecord(
            id=book_id,
            title=title.strip(),
            authors=authors,
            url=url,
            source=MetaSourceInfo(cls.__id__, cls.DESCRIPTION, cls.META_URL),
            cover=data.get("image") if isinstance(data.get("image"), str) else generic_cover,
        )
        record.description = data.get("description", "") if isinstance(data.get("description"), str) else ""
        record.publisher = cls._name(data.get("publisher"))
        record.publishedDate = cls._date(data.get("datePublished"))
        record.identifiers = cls._identifiers(data, book_id)
        record.tags = cls._tags(data.get("genre"))
        aggregate = data.get("aggregateRating")
        if isinstance(aggregate, dict):
            record.rating = cls._rating(aggregate.get("ratingValue"))
        series = data.get("isPartOf")
        if isinstance(series, dict):
            record.series = cls._name(series)
            position = series.get("position")
            if isinstance(position, (int, float)):
                record.series_index = position
            elif isinstance(position, str):
                try:
                    record.series_index = float(position)
                except ValueError:
                    pass
        return record

    @staticmethod
    def _book_json_ld(soup: BeautifulSoup) -> dict:
        for node in soup.select('script[type="application/ld+json"]'):
            try:
                payload = json.loads(node.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            candidates: Iterable = payload if isinstance(payload, list) else [payload]
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                graph = item.get("@graph")
                nested = graph if isinstance(graph, list) else [item]
                for candidate in nested:
                    kinds = candidate.get("@type", []) if isinstance(candidate, dict) else []
                    if isinstance(kinds, str):
                        kinds = [kinds]
                    if "Book" in kinds:
                        return candidate
        return {}

    @staticmethod
    def _name(value):
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            return value["name"].strip() or None
        return None

    @classmethod
    def _names(cls, value) -> List[str]:
        values = value if isinstance(value, list) else [value]
        return [name for item in values if (name := cls._name(item))]

    @staticmethod
    def _date(value):
        return value[:10] if isinstance(value, str) and re.match(r"^\d{4}", value) else None

    @staticmethod
    def _rating(value) -> int:
        try:
            return max(0, min(5, round(float(value))))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _tags(value) -> List[str]:
        values = value if isinstance(value, list) else [value]
        return [v.strip() for v in values if isinstance(v, str) and v.strip()][:8]

    @staticmethod
    def _book_id(url: str, data: dict) -> str:
        match = re.search(r"/book/show/(\d+)", url)
        return match.group(1) if match else str(data.get("isbn") or url)

    @staticmethod
    def _identifiers(data: dict, book_id: str) -> dict:
        result = {"goodreads": book_id}
        for source, target in (("isbn", "isbn"), ("isbn13", "isbn")):
            value = data.get(source)
            if isinstance(value, str) and value.strip():
                result[target] = re.sub(r"[^0-9Xx]", "", value)
        return result
