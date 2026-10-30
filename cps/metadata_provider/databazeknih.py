# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Metadata provider for databazeknih.cz (Databáze knih).

Databáze knih is the largest community book database for Czech *and* Slovak
editions — the catalogue the big English-centric sources (Google Books,
Hardcover, Open Library) barely cover. Each edition has its own page, so a
Slovak translation and a Czech translation of the same work are separate
records with their own ISBN, publisher, translator and cover.

How the site is read (verified against the live site, 2026-10):

* ``/search?q=<isbn>`` redirects straight to the edition page; a text query
  redirects to ``/vyhledavani/knihy?q=…`` whose hits are ``p.new`` rows with
  an ``a.new[type=book]`` link and a ``span.pozn`` reading "<year>, <author>".
* The edition page ``/prehled-knihy/<slug>-<id>`` carries a schema.org
  ``Book`` JSON-LD block (title, authors, ISBN, publisher, language, genres,
  rating) plus HTML for the series, year, tags and the full description (the
  JSON-LD description is cut at 300 characters).
* ``/book-detail-more-info/<id>`` returns the "Více info…" fragment:
  original title, translator, page count, other titles, language, ISBN.
* The slug part of the URL is cosmetic — any slug redirects to the right
  page — so the numeric id is what matters. The stored identifier keeps the
  ``<slug>-<id>`` form for compatibility with the calibre Databazeknih
  plugin and the existing ``databazeknih`` identifier link in cps/db.py.
"""

import json
import re
from html import escape
import unicodedata
from multiprocessing.pool import ThreadPool
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin

import requests
from lxml.html import HtmlElement, fromstring

from cps import logger
from cps.isoLanguages import get_language_name
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata

try:
    from cps.services.Metadata import ProviderRefused
except ImportError:  # pragma: no cover - older base class
    class ProviderRefused(Exception):  # type: ignore
        def __init__(self, status: str, message: str):
            super().__init__(message)
            self.status = status

log = logger.create()

BASE_URL = "https://www.databazeknih.cz"

# ISO 639-1 codes the site emits in JSON-LD ``inLanguage`` → ISO 639-2/3
# codes Calibre stores. Czech/Slovak dominate; the rest cover the usual
# originals that also get catalogued there.
LANG_MAP = {
    "cs": "ces", "cz": "ces", "sk": "slk", "en": "eng", "de": "deu",
    "pl": "pol", "hu": "hun", "fr": "fra", "es": "spa", "it": "ita",
    "ru": "rus", "uk": "ukr",
}
# The "Jazyk vydání" text in the more-info fragment, used when JSON-LD has
# no inLanguage.
LANG_NAME_MAP = {
    "český": "ces", "slovenský": "slk", "anglický": "eng", "německý": "deu",
    "polský": "pol", "maďarský": "hun",
}

YEAR_RE = re.compile(r"\b(1[4-9]\d\d|20\d\d)\b")
SERIES_INDEX_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*\.\s*díl")
BOOK_ID_RE = re.compile(r"/(?:prehled-knihy|knihy)/([\w-]*?-?(\d+))(?:[/?#]|$)")
EXPLICIT_ID_RE = re.compile(r"databazeknih:\s*([\w-]*\d)", re.IGNORECASE)
ISBN_CHARS_RE = re.compile(r"[^0-9Xx]")


def normalize(text: Optional[str]) -> str:
    """Lower-case and strip diacritics so 'Poštový úrad' == 'postovy urad'."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def extract_isbn(query: str) -> Optional[str]:
    """Return a bare ISBN-10/13 if the query is (or contains) one."""
    for candidate in re.findall(r"[0-9][0-9Xx\- ]{8,16}[0-9Xx]", query or ""):
        digits = ISBN_CHARS_RE.sub("", candidate)
        if len(digits) in (10, 13):
            return digits.upper()
    return None


def book_id_from_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """'/prehled-knihy/postovni-urad-1781' → ('postovni-urad-1781', '1781')."""
    match = BOOK_ID_RE.search(url or "")
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.text_content()).strip() if node is not None else ""


def _first(root: HtmlElement, xpath: str):
    found = root.xpath(xpath)
    return found[0] if found else None


def parse_search_results(html: str) -> List[Dict]:
    """Parse the /vyhledavani/knihy result list into plain dicts."""
    root = fromstring(html)
    results = []
    seen = set()
    for row in root.xpath("//p[contains(concat(' ', normalize-space(@class), ' '), ' new ')]"):
        link = _first(row, ".//a[@type='book']")
        if link is None:
            continue
        href = link.get("href", "")
        slug_id, num_id = book_id_from_url(href)
        if not num_id or num_id in seen:
            continue
        seen.add(num_id)
        note = _text(_first(row, ".//span[contains(@class,'pozn')]"))
        year, authors = None, []
        if note:
            parts = [p.strip() for p in note.split(",") if p.strip()]
            if parts and YEAR_RE.fullmatch(parts[0]):
                year = parts.pop(0)
            authors = parts
        results.append({
            "id": num_id,
            "slug_id": slug_id,
            "title": _text(link),
            "authors": authors,
            "year": year,
            "url": urljoin(BASE_URL, href),
        })
    return results


def parse_more_info(html: str) -> Dict[str, str]:
    """Parse the /book-detail-more-info/<id> fragment into {label: value}."""
    if not html or not html.strip():
        return {}
    root = fromstring(f"<div>{html}</div>")
    info = {}
    for row in root.xpath("//*[contains(@class,'book-details__row')]"):
        label = _text(_first(row, ".//dt"))
        value = _text(_first(row, ".//dd"))
        if label and value:
            info[label] = value
    return info


def parse_other_editions(html: str) -> List[Dict]:
    """Editions linked from the "Další název" row of the more-info fragment —
    typically the Slovak edition of a Czech record or vice versa. The site's
    own search often misses these, so they are offered as extra candidates."""
    if not html or not html.strip():
        return []
    root = fromstring(f"<div>{html}</div>")
    editions = []
    for row in root.xpath("//*[contains(@class,'book-details__row')]"):
        if _text(_first(row, ".//dt")) != "Další název":
            continue
        for link in row.xpath(".//dd//a[contains(@href,'/prehled-knihy/')]"):
            slug_id, num_id = book_id_from_url(link.get("href", ""))
            if num_id:
                editions.append({
                    "id": num_id,
                    "slug_id": slug_id,
                    "title": _text(link),
                    "url": urljoin(BASE_URL, link.get("href")),
                })
    return editions


def _load_json_ld(root: HtmlElement) -> Dict:
    for block in root.xpath("//script[@type='application/ld+json']/text()"):
        try:
            data = json.loads(block)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("@type") == "Book":
            return data
    return {}


def _names(value) -> List[str]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    return [v.get("name", "").strip() for v in value if isinstance(v, dict) and v.get("name")]


def _description_html(root: HtmlElement, fallback: str) -> str:
    node = _first(root, "//p[contains(@class,'new2')]")
    if node is None:
        text = fallback or ""
    else:
        for more_link in node.xpath(".//a[contains(@class,'show_hide_more')]"):
            more_link.drop_tree()
        text = node.text_content()
    paragraphs = [re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\r?\n", text)]
    return "".join(f"<p>{escape(p)}</p>" for p in paragraphs if p)


def parse_book_page(html: str, url: str, more_info: Optional[Dict[str, str]] = None) -> Dict:
    """Parse an edition page (plus its optional more-info fragment) into a dict
    of MetaRecord fields. Pure function — no network — so it is testable
    against captured fixtures."""
    root = fromstring(html)
    ld = _load_json_ld(root)
    more_info = more_info or {}

    og_url = _first(root, "//meta[@property='og:url']/@content")
    slug_id, num_id = book_id_from_url(og_url or ld.get("url") or url)

    title = (ld.get("name") or _text(_first(root, "//h1")) or "").strip()
    authors = _names(ld.get("author")) or [
        _text(a) for a in root.xpath("//span[@class='author']//a")
    ]

    # Series: "<a href='/serie/...'>Henry Chinaski</a> série … 1. díl"
    series, series_index = None, 0
    serie_box = _first(root, "//div[contains(@class,'book_detail_serie_info')]")
    if serie_box is not None:
        series = _text(_first(serie_box, ".//a[contains(@href,'/serie/')]")) or None
        index_match = SERIES_INDEX_RE.search(_text(serie_box))
        if index_match:
            raw = index_match.group(1).replace(",", ".")
            series_index = float(raw) if "." in raw else int(raw)

    # Year sits after the genre links: "Romány <br> 1996 , Pragma"
    published = None
    info_box = _first(root, "//div[contains(@class,'lineHeightMid')]")
    if info_box is not None:
        year_match = YEAR_RE.search(_text(info_box))
        if year_match:
            published = f"{year_match.group(1)}-01-01"

    publishers = _names(ld.get("publisher"))
    publisher = publishers[0] if publishers else None

    tags: List[str] = []
    genres = ld.get("genre") or []
    if isinstance(genres, str):
        genres = [genres]
    for tag in list(genres) + [_text(a) for a in root.xpath("//a[contains(@class,'tag') and contains(@href,'/stitky/')]")]:
        tag = tag.strip()
        if tag and tag not in tags:
            tags.append(tag)

    lang_code = LANG_MAP.get(str(ld.get("inLanguage", "")).lower())
    if not lang_code:
        lang_code = LANG_NAME_MAP.get(more_info.get("Jazyk vydání", "").lower())

    rating = 0
    try:
        rating = int(round(float(ld.get("aggregateRating", {}).get("ratingValue", 0))))
    except (TypeError, ValueError, AttributeError):
        rating = 0

    isbn = ld.get("isbn") or more_info.get("ISBN") or ""
    isbn = ISBN_CHARS_RE.sub("", isbn).upper()

    description = _description_html(root, ld.get("description", ""))
    extra = []
    for label in ("Originální název", "Překlad", "Počet stran"):
        if more_info.get(label):
            extra.append(f"{label}: {more_info[label]}")
    if extra:
        description += "<p>" + "<br>".join(escape(e) for e in extra) + "</p>"

    cover = _first(root, "//meta[@property='og:image']/@content") or ld.get("image") or ""

    identifiers = {}
    if slug_id:
        identifiers["databazeknih"] = slug_id
    if isbn:
        identifiers["isbn"] = isbn

    return {
        "id": num_id,
        "slug_id": slug_id,
        "title": title,
        "authors": authors,
        "url": f"{BASE_URL}/prehled-knihy/{slug_id}" if slug_id else url,
        "cover": cover,
        "description": description,
        "series": series,
        "series_index": series_index,
        "publisher": publisher,
        "publishedDate": published,
        "rating": rating,
        "language": lang_code,
        "tags": tags,
        "identifiers": identifiers,
    }


def big_cover_candidate(cover_url: str) -> Optional[str]:
    """Covers come in plain / bmid_ / big_ variants; big_ is the sharpest.
    Only some editions have it, so the caller must check it exists."""
    if not cover_url:
        return None
    head, _, name = cover_url.rpartition("/")
    if name.startswith("big_"):
        return None
    if name.startswith("bmid_"):
        name = name[len("bmid_"):]
    return f"{head}/big_{name.split('?')[0]}"


def score_match(query_tokens: List[str], result: Dict) -> int:
    haystack = normalize(" ".join([result.get("title", "")] + result.get("authors", [])))
    words = set(re.findall(r"\w+", haystack))
    return sum(1 for token in query_tokens if normalize(token) in words)


class DatabazeKnih(Metadata):
    __name__ = "Databáze knih"
    __id__ = "databazeknih"

    MAX_RESULTS = 8
    TIMEOUT = 15
    # search() resolves "isbn:<isbn>" through the site's ISBN redirect, so
    # the auto-fetch may retry this provider by the book's ISBN when the
    # title search misses (e.g. a title stored without diacritics).
    SUPPORTS_ISBN_QUERY = True
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "cs,sk;q=0.9,en;q=0.6",
    }

    def __init__(self):
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    # -- network -----------------------------------------------------------

    def _get(self, url: str, **kwargs) -> requests.Response:
        try:
            response = self.session.get(url, timeout=self.TIMEOUT, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (403, 429, 503):
                status = "rate_limited" if code == 429 else "blocked"
                raise ProviderRefused(status, (
                    "Databáze knih refused the request (HTTP %s); "
                    "try again later." % code
                )) from e
            raise

    def _source(self) -> MetaSourceInfo:
        return MetaSourceInfo(
            id=self.__id__, description=self.__name__, link=BASE_URL + "/"
        )

    # -- public API --------------------------------------------------------

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        if not self.active:
            return []
        query = (query or "").strip()
        if not query:
            return []
        try:
            explicit = EXPLICIT_ID_RE.search(query)
            if explicit:
                record, _ = self._fetch_book(
                    f"{BASE_URL}/prehled-knihy/{explicit.group(1)}", generic_cover, locale
                )
                return [record] if record else []

            isbn = extract_isbn(query)
            if isbn:
                response = self._get(f"{BASE_URL}/search?q={quote(isbn)}")
                # An ISBN redirects straight to the edition page.
                if "/prehled-knihy/" in response.url:
                    record, _ = self._record_from_page(
                        response.text, response.url, generic_cover, locale
                    )
                    if record:
                        return [record]

            tokens = list(self.get_title_tokens(query, strip_joiners=False))
            if not tokens:
                return []
            return self._search_text(tokens, generic_cover, locale)
        except ProviderRefused:
            raise
        except Exception as e:
            log.warning("Databáze knih search failed: %s", e)
            return []

    def _search_text(self, tokens: List[str], generic_cover: str, locale: str) -> List[MetaRecord]:
        # The site's search is title-only and fuzzy: "title author" may miss,
        # and a query that misses still returns unrelated near-spellings
        # ("Poštový úrad" → "Postavy a …"). So try the full query and then
        # the query with up to two trailing words (usually the author)
        # dropped, and keep the attempt whose best hit matches the most query
        # words. A page that redirects straight to an edition wins outright.
        best_hits: List[Dict] = []
        best_score = -1
        for size in range(len(tokens), max(len(tokens) - 3, 0), -1):
            text = " ".join(tokens[:size])
            response = self._get(f"{BASE_URL}/search?q={quote(text)}")
            if "/prehled-knihy/" in response.url:
                record, alternates = self._record_from_page(
                    response.text, response.url, generic_cover, locale
                )
                if not record:
                    continue
                return [record] + self._fetch_alternates(
                    [(record, alternates)], tokens, generic_cover, locale
                )
            hits = parse_search_results(response.text)
            if not hits:
                continue
            for hit in hits:
                hit["score"] = score_match(tokens, hit)
            # Stable sort: the site's own order breaks ties.
            hits.sort(key=lambda hit: hit["score"], reverse=True)
            if hits[0]["score"] > best_score:
                best_hits, best_score = hits, hits[0]["score"]
            if best_score >= len(tokens):
                break
        if not best_hits:
            return []

        hits = best_hits[: self.MAX_RESULTS]
        with ThreadPool(processes=min(len(hits), 5)) as pool:
            fetched = pool.starmap(
                self._fetch_book, [(hit["url"], generic_cover, locale) for hit in hits]
            )
        fetched = [(r, alt) for r, alt in fetched if r is not None]
        records = [r for r, _ in fetched]
        extra = self._fetch_alternates(fetched, tokens, generic_cover, locale)
        # Alternate editions that match the query better go to the front.
        return extra + records

    def _fetch_alternates(self, fetched, tokens: List[str], generic_cover: str, locale: str):
        """Fetch editions listed under "Další název" when their title matches
        the query better than the record that linked them (typically the
        Slovak edition of a Czech record)."""
        known = {
            r.identifiers.get("databazeknih", "").rsplit("-", 1)[-1] for r, _ in fetched
        }
        candidates = []
        for record, alternates in fetched:
            parent_score = score_match(tokens, {"title": record.title, "authors": record.authors})
            for alt in alternates:
                if alt["id"] in known:
                    continue
                score = score_match(tokens, {"title": alt["title"], "authors": record.authors})
                if score > parent_score:
                    known.add(alt["id"])
                    candidates.append((score, alt["url"]))
        candidates.sort(key=lambda c: c[0], reverse=True)
        records = []
        for _, url in candidates[:3]:
            record, _ = self._fetch_book(url, generic_cover, locale)
            if record:
                records.append(record)
        return records

    def _fetch_book(self, url: str, generic_cover: str, locale: str):
        try:
            response = self._get(url)
        except ProviderRefused:
            raise
        except Exception as e:
            log.warning("Databáze knih: cannot load %s: %s", url, e)
            return None, []
        return self._record_from_page(response.text, response.url, generic_cover, locale)

    def _record_from_page(self, html: str, url: str, generic_cover: str, locale: str):
        """Return (MetaRecord or None, alternate editions)."""
        _, num_id = book_id_from_url(url)
        more_info, alternates = {}, []
        if num_id:
            try:
                fragment = self._get(f"{BASE_URL}/book-detail-more-info/{num_id}").text
                more_info = parse_more_info(fragment)
                alternates = parse_other_editions(fragment)
            except ProviderRefused:
                raise
            except Exception as e:
                log.debug("Databáze knih: no extra info for %s: %s", num_id, e)
        try:
            data = parse_book_page(html, url, more_info)
        except Exception as e:
            log.warning("Databáze knih: cannot parse %s: %s", url, e)
            return None, []
        if not data["title"]:
            return None, []

        cover = data["cover"]
        big = big_cover_candidate(cover)
        if big:
            try:
                head = self.session.head(big, timeout=5, allow_redirects=True)
                if head.ok and head.headers.get("content-type", "").startswith("image/"):
                    cover = big
            except Exception:
                pass

        languages = []
        if data["language"]:
            languages = [get_language_name(locale, data["language"])]

        record = MetaRecord(
            id=data["slug_id"] or data["id"] or url,
            title=data["title"],
            authors=data["authors"],
            url=data["url"],
            source=self._source(),
            cover=cover or generic_cover,
            description=data["description"],
            series=data["series"],
            series_index=data["series_index"],
            identifiers=data["identifiers"],
            publisher=data["publisher"],
            publishedDate=data["publishedDate"],
            rating=data["rating"],
            languages=languages,
            tags=data["tags"],
        )
        return record, alternates
