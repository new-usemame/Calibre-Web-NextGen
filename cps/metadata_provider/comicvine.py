# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# ComicVine api document: https://comicvine.gamespot.com/api/documentation
import re
from typing import Dict, List, Optional
from urllib.parse import quote

import requests
from cps import config, logger
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata

log = logger.create()

# The key travels in the query string, and requests puts the full URL into its
# exception messages. Once an install can configure its OWN key that turns every
# logged failure into a secret leak, and admins paste logs into bug reports.
_API_KEY_IN_URL = re.compile(r"(api_key=)[^&\s]*")


def _scrub(text) -> str:
    """Redact any API key in a string bound for the log."""
    return _API_KEY_IN_URL.sub(r"\1***", str(text))


class ComicVine(Metadata):
    __name__ = "ComicVine"
    __id__ = "comicvine"
    DESCRIPTION = "ComicVine Books"
    META_URL = "https://comicvine.gamespot.com/"
    # The key every install sends when the admin has not supplied one. It has
    # been public in calibre-web's source for years, so it is a shared client
    # credential, not a secret: replacing it here would not un-publish it from
    # git history, released images, upstream, or any fork. It stays as the
    # zero-configuration default — the provider must keep working on a fresh
    # install — and an admin who wants their own quota sets a key in the
    # metadata-search Keys panel, or via COMICVINE_API_KEY /
    # COMICVINE_API_KEY_FILE. Fork #1242, credit @tomaioo.
    SHARED_API_KEY = "57558043c53943d5d1e96a9ad425b0eb85532ee6"
    # Trailing slash is the canonical form: without it ComicVine answers a
    # 301 to this URL, so every search paid an extra round trip.
    BASE_URL = "https://comicvine.gamespot.com/api/search/"
    QUERY_PARAMS = "&sort=name:desc&format=json"
    HEADERS = {"User-Agent": "Not Evil Browser"}

    @staticmethod
    def _resolve_api_key() -> str:
        """This install's own key when it has one, else the shared key.

        Resolved per request, not interpolated into a class attribute at
        import time: a key set through the admin Keys panel arrives long after
        this module loads, and a cached URL would never see it.
        """
        return config.resolved_comicvine_api_key() or ComicVine.SHARED_API_KEY

    @staticmethod
    def _log_refusal(detail: str, api_key: str) -> None:
        """Explain a refused search, and name the remedy that fits the install.

        ComicVine refuses on two different channels — an HTTP 401/420 for a
        rejected key or an exhausted quota, and a ``status_code`` inside an
        otherwise-200 body — so both callers route through here to keep one
        message per situation.
        """
        if api_key == ComicVine.SHARED_API_KEY:
            log.warning(
                "ComicVine search refused (%s). This install is using the "
                "shared ComicVine key, which every install sends and which "
                "can hit the rate limit. Add your own free key in the "
                "metadata-search Keys panel to get a separate quota.", detail
            )
        else:
            log.warning(
                "ComicVine search refused (%s). Check the ComicVine API key "
                "configured for this install.", detail
            )

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        val = list()
        if self.active:
            title_tokens = list(self.get_title_tokens(query, strip_joiners=False))
            if title_tokens:
                tokens = [quote(t.encode("utf-8")) for t in title_tokens]
                query = "%20".join(tokens)
            api_key = self._resolve_api_key()
            url = (
                f"{ComicVine.BASE_URL}"
                f"?api_key={quote(api_key.encode('utf-8'))}"
                f"&resources=issue&query={query}{ComicVine.QUERY_PARAMS}"
            )
            try:
                result = requests.get(
                    url,
                    headers=ComicVine.HEADERS,
                    timeout=15,
                )
                result.raise_for_status()
            except requests.HTTPError as e:
                # Observed on the wire: a rejected key is an HTTP 401, not the
                # in-body status_code the API documents. 420 "Enhance Your
                # Calm" and 429 are the throttling variants. Anything else is
                # an ordinary transport failure and stays a bare warning.
                if getattr(e.response, "status_code", None) in (401, 403, 420, 429):
                    self._log_refusal(_scrub(e), api_key)
                else:
                    log.warning(_scrub(e))
                return []
            except Exception as e:
                log.warning(_scrub(e))
                return []
            payload = result.json()
            # The other refusal channel: ComicVine also reports failures in an
            # otherwise-200 body, where an exhausted quota is indistinguishable
            # from "no matches" unless the envelope is read. status_code 1 is
            # OK; 100 is a rejected key, 107 an exhausted rate limit.
            status_code = payload.get("status_code")
            if status_code is not None and status_code != 1:
                self._log_refusal(
                    _scrub(payload.get("error") or f"status_code {status_code}"),
                    api_key,
                )
                return []
            for result in payload.get("results", []):
                match = self._parse_search_result(
                    result=result, generic_cover=generic_cover, locale=locale
                )
                val.append(match)
        return val

    def _parse_search_result(
        self, result: Dict, generic_cover: str, locale: str
    ) -> MetaRecord:
        series = result["volume"].get("name", "")
        series_index = result.get("issue_number", 0)
        issue_name = result.get("name", "")
        match = MetaRecord(
            id=result["id"],
            title=f"{series}#{series_index} - {issue_name}",
            authors=result.get("authors", []),
            url=result.get("site_detail_url", ""),
            source=MetaSourceInfo(
                id=self.__id__,
                description=ComicVine.DESCRIPTION,
                link=ComicVine.META_URL,
            ),
            series=series,
        )
        match.cover = result["image"].get("original_url", generic_cover)
        match.description = result.get("description", "")
        match.publishedDate = result.get("store_date", result.get("date_added"))
        match.series_index = series_index
        match.tags = ["Comics", series]
        match.identifiers = {"comicvine": match.id}
        return match
