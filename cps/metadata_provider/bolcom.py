# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Best-effort bol.com book metadata scraper (#315).

Maintenance note: bol.com can reject automated traffic and changes page markup.
Embedded JSON-LD product data is expected to break first, followed by product-link
selectors. Recorded fixtures detect parser drift; live opt-in searches detect an
anti-bot change. Every upstream or parsing failure intentionally returns no results.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from cps import constants, logger
from cps.services.Metadata import Metadata, MetaRecord, MetaSourceInfo

log = logger.create()


class BolCom(Metadata):
    __name__ = "bol.com (best effort)"
    __id__ = "bolcom"
    DESCRIPTION = __name__
    META_URL = "https://www.bol.com/"
    SEARCH_URL = "https://www.bol.com/nl/nl/s/?searchtext={}"
    TIMEOUT = (4, 8)
    MAX_RESULTS = 3
    HEADERS = {
        "User-Agent": constants.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.7",
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
            log.warning("bol.com search failed: %s", exc)
            return []
        records = []
        for link in links[:self.MAX_RESULTS]:
            try:
                response = session.get(link, timeout=self.TIMEOUT)
                response.raise_for_status()
                record = self._parse_book(response.text, link, generic_cover)
                if record:
                    records.append(record)
            except (requests.RequestException, ValueError, TypeError) as exc:
                log.warning("bol.com book fetch failed: %s", exc)
        return records

    @classmethod
    def _parse_search(cls, html: object) -> List[str]:
        if not isinstance(html, str) or not html.strip():
            return []
        soup = BeautifulSoup(html, "html.parser")
        found = []
        for anchor in soup.select('a[href*="/nl/nl/p/"]'):
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
        data = cls._product_json_ld(soup)
        title = data.get("name") if data else None
        if not isinstance(title, str) or not title.strip():
            return None
        product_id = cls._product_id(url, data)
        authors = cls._authors(data)
        record = MetaRecord(
            id=product_id,
            title=title.strip(),
            authors=authors,
            url=url,
            source=MetaSourceInfo(cls.__id__, cls.DESCRIPTION, cls.META_URL),
            cover=cls._image(data.get("image")) or generic_cover,
        )
        description = data.get("description")
        record.description = description if isinstance(description, str) else ""
        record.publisher = cls._name(data.get("publisher") or data.get("brand"))
        record.publishedDate = cls._date(data.get("datePublished"))
        isbn = data.get("isbn") or data.get("gtin13")
        record.identifiers = {"bol": product_id}
        if isinstance(isbn, str) and isbn.strip():
            record.identifiers["isbn"] = re.sub(r"\D", "", isbn)
        record.tags = cls._tags(data.get("genre") or data.get("category"))
        rating = data.get("aggregateRating")
        if isinstance(rating, dict):
            record.rating = cls._rating(rating.get("ratingValue"))
        series = data.get("isPartOf")
        if isinstance(series, dict):
            record.series = cls._name(series)
            try:
                record.series_index = float(series.get("position", 0))
            except (TypeError, ValueError):
                pass
        language = data.get("inLanguage")
        if isinstance(language, str) and language.strip():
            record.languages = [language.strip()]
        return record

    @staticmethod
    def _product_json_ld(soup: BeautifulSoup) -> dict:
        for node in soup.select('script[type="application/ld+json"]'):
            try:
                payload = json.loads(node.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if not isinstance(item, dict):
                    continue
                candidates = item.get("@graph") if isinstance(item.get("@graph"), list) else [item]
                for candidate in candidates:
                    kind = candidate.get("@type") if isinstance(candidate, dict) else None
                    kinds = kind if isinstance(kind, list) else [kind]
                    if "Book" in kinds or "Product" in kinds:
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
    def _authors(cls, data: dict) -> List[str]:
        value = data.get("author")
        values = value if isinstance(value, list) else [value]
        return [name for item in values if (name := cls._name(item))]

    @staticmethod
    def _image(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return next((item for item in value if isinstance(item, str)), None)
        if isinstance(value, dict) and isinstance(value.get("url"), str):
            return value["url"]
        return None

    @staticmethod
    def _date(value):
        return value[:10] if isinstance(value, str) and re.match(r"^\d{4}", value) else None

    @staticmethod
    def _rating(value):
        try:
            return max(0, min(5, round(float(value))))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _tags(value):
        values = value if isinstance(value, list) else [value]
        return [item.strip() for item in values if isinstance(item, str) and item.strip()][:8]

    @staticmethod
    def _product_id(url: str, data: dict):
        match = re.search(r"/(\d{8,})/?$", url)
        return match.group(1) if match else str(data.get("sku") or data.get("gtin13") or url)
