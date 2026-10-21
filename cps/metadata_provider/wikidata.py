# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# Wikidata metadata provider, for series and series order.
#
# API docs: https://www.wikidata.org/w/api.php (action=query&list=search,
# action=wbgetentities). Free, no key; the data is CC0
# (https://www.wikidata.org/wiki/Wikidata:Licensing). Wikimedia asks every
# client to send a User-Agent with contact details
# (https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy).
#
# A literary work carries its series as P179 ("part of the series") with the
# position as the P1545 ("series ordinal") qualifier. The ordinal is a string,
# so positions such as 24.5 come through as they are. Title, authors (P50) and
# first publication date (P577) are returned so the usual match gate can tell
# whether a result is the book being fetched; Wikidata has no covers or
# blurbs to offer, so none are returned.

from typing import Dict, List, Optional

import requests

from cps import constants, logger
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata, ProviderRefused

log = logger.create()


class Wikidata(Metadata):
    __name__ = "Wikidata"
    __id__ = "wikidata"
    DESCRIPTION = "Wikidata"
    META_URL = "https://www.wikidata.org/"
    # Asked for series when the record applied first has none (metadata_helper).
    FILLS_SERIES = True

    API_URL = "https://www.wikidata.org/w/api.php"
    ITEM_URL = "https://www.wikidata.org/wiki/{qid}"
    SEARCH_LIMIT = 5
    REQUEST_TIMEOUT = 10
    HEADERS = {
        "User-Agent": f"{constants.USER_AGENT} (https://github.com/new-usemame/Calibre-Web-NextGen)",
    }

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        if not self.active:
            return []
        text = " ".join(self.get_title_tokens(query, strip_joiners=False)).strip()
        if not text:
            return []
        language = (locale or "en").split("_")[0].split("-")[0].lower() or "en"

        # Only items with an author (P50): works, not people, places or podcasts.
        hits = self._call({
            "action": "query", "list": "search", "srsearch": f"{text} haswbstatement:P50",
            "srlimit": self.SEARCH_LIMIT, "srnamespace": 0, "format": "json",
        })
        ids = [hit.get("title") for hit in (hits.get("query", {}).get("search") or [])
               if str(hit.get("title", "")).startswith("Q")]
        if not ids:
            return []
        works = self._entities(ids, "labels|claims", language)

        # One more call resolves every author and series name at once.
        linked = []
        for qid in ids:
            claims = works.get(qid, {}).get("claims", {})
            linked += self._item_values(claims, "P50") + self._item_values(claims, "P179")
        names = self._entities(sorted(set(linked)), "labels", language) if linked else {}

        records = []
        for qid in ids:
            entity = works.get(qid)
            if entity:
                record = self._record(qid, entity, names, language, generic_cover)
                if record:
                    records.append(record)
        return records

    def _call(self, params: Dict) -> Dict:
        try:
            response = requests.get(self.API_URL, params=params, headers=self.HEADERS,
                                    timeout=self.REQUEST_TIMEOUT)
        except requests.RequestException as error:
            log.warning("Wikidata request failed: %s", error)
            return {}
        if response.status_code == 429:
            raise ProviderRefused("rate_limited", "Wikidata is limiting requests from this server; try again later.")
        if response.status_code != 200:
            log.warning("Wikidata answered HTTP %s", response.status_code)
            return {}
        try:
            return response.json()
        except ValueError:
            log.warning("Wikidata returned invalid JSON")
            return {}

    def _entities(self, ids: List[str], props: str, language: str) -> Dict:
        found = {}
        for start in range(0, len(ids), 50):   # wbgetentities takes 50 ids a call
            payload = self._call({
                "action": "wbgetentities", "ids": "|".join(ids[start:start + 50]),
                "props": props, "languages": f"{language}|en", "format": "json",
            })
            found.update(payload.get("entities") or {})
        return found

    @staticmethod
    def _label(entity: Optional[Dict], language: str) -> str:
        labels = (entity or {}).get("labels") or {}
        for code in (language, "en"):
            value = (labels.get(code) or {}).get("value")
            if value:
                return value
        return ""

    @staticmethod
    def _item_values(claims: Dict, prop: str) -> List[str]:
        out = []
        for statement in claims.get(prop) or []:
            value = ((statement.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            if isinstance(value, dict) and value.get("id"):
                out.append(value["id"])
        return out

    @staticmethod
    def _series(claims: Dict):
        """(series item id, position) of the work's main series, or (None, None).

        A work can sit in more than one series item (a saga and a trilogy that
        overlap). A preferred-rank statement wins, then one that has a
        position, then the first listed.
        """
        best, best_key = (None, None), None
        for order, statement in enumerate(claims.get("P179") or []):
            if statement.get("rank") == "deprecated":
                continue
            value = ((statement.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            if not isinstance(value, dict) or not value.get("id"):
                continue
            position = None
            for qualifier in (statement.get("qualifiers") or {}).get("P1545") or []:
                raw = ((qualifier.get("datavalue") or {}).get("value"))
                try:
                    number = float(str(raw).strip())
                except (TypeError, ValueError):
                    continue
                position = int(number) if number.is_integer() else number
                break
            key = (statement.get("rank") != "preferred", position is None, order)
            if best_key is None or key < best_key:
                best, best_key = (value["id"], position), key
        return best

    @staticmethod
    def _first_date(claims: Dict) -> str:
        for statement in claims.get("P577") or []:
            value = ((statement.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
            stamp, precision = str(value.get("time", "")), value.get("precision", 0)
            if len(stamp) >= 11 and stamp[0] == "+":
                day = stamp[1:11]
                return day if precision >= 11 else day[:7] if precision == 10 else day[:4]
        return ""

    def _record(self, qid, entity, names, language, generic_cover) -> Optional[MetaRecord]:
        title = self._label(entity, language)
        claims = entity.get("claims") or {}
        if not title:
            return None
        authors = [name for name in (self._label(names.get(a), language)
                                     for a in self._item_values(claims, "P50")) if name]
        series_id, position = self._series(claims)
        record = MetaRecord(
            id=qid,
            title=title,
            authors=authors,
            url=self.ITEM_URL.format(qid=qid),
            source=MetaSourceInfo(id=self.__id__, description=self.DESCRIPTION, link=self.META_URL),
            cover=generic_cover,
        )
        record.series = self._label(names.get(series_id), language) if series_id else None
        record.series_index = position if record.series and position is not None else 0
        record.publishedDate = self._first_date(claims)
        record.identifiers = {"wikidata": qid}
        return record
