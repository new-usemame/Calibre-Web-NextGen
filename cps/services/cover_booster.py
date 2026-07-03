# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Cover-resolution booster for metadata-search results.

Many providers serve thumbnail-sized cover URLs (Hardcover ~290x475, Open
Library "-L" ~500x..., Google Books default ~128 wide). High-DPI e-readers
like the Kobo Libra Color (1264x1680) need ~1500px-wide covers to render
crisply. This module takes the aggregated MetaRecord list from
search_metadata.metadata_search() and, in parallel, looks up a higher-res
alternative for each record, replacing record.cover when one is found.

Sources tried, in order, per record:

  1. Amazon image CDN by ISBN-10 - public CloudFront-fronted host with
     CORS open, no auth, no scraping. Edition-keyed by ISBN-10/ASIN, so
     when the record has an ISBN we get the correct-edition cover at up
     to ~2000px tall. Validated via HEAD against an image/jpeg
     content-type and a minimum byte count (Amazon serves a 43-byte GIF
     placeholder for unknown ASINs).
  2. iTunes lookup by ISBN - exact-edition match against Apple Books.
  3. iTunes search by title+author - fuzzy match. Only run when the
     record has no ISBN, or as a secondary signal that's gated to within
     a few years of the record's publish year, to avoid swapping a
     correct-edition cover for an unrelated edition Apple happens to
     stock.
  4. Amazon URL rewrite - if the record cover is already an Amazon
     m.media-amazon.com asset with a sizing token, swap to _SL2000_ to
     pull the largest variant Amazon's master serves.

Disable globally with env CWA_COVER_BOOST=0. Tuning knobs:

  CWA_COVER_BOOST_TIMEOUT  - per-lookup HTTP timeout, seconds (default 4)
  CWA_COVER_BOOST_WORKERS  - thread pool size for parallel lookups (default 8)
  CWA_COVER_BOOST_MAX      - cap on records boosted per request (default 30)
  CWA_COVER_BOOST_AMAZON_CDN  - set to 0/false to disable the Amazon
                                image-CDN path (default on)
"""
from __future__ import annotations

import concurrent.futures
import os
import re
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote_plus

import requests

from .. import constants, logger


log = logger.create()


_DEFAULT_TIMEOUT = float(os.environ.get("CWA_COVER_BOOST_TIMEOUT", "4"))
_DEFAULT_WORKERS = int(os.environ.get("CWA_COVER_BOOST_WORKERS", "8"))
_DEFAULT_MAX = int(os.environ.get("CWA_COVER_BOOST_MAX", "30"))
_AMAZON_CDN_ENABLED = os.environ.get("CWA_COVER_BOOST_AMAZON_CDN", "1").lower() not in ("0", "false", "no", "off")

# Patterns that indicate the cover URL is already high-res - skip work.
_HIGHRES_HINTS = (
    "_SL1500_", "_SL2000_", "1500x1500bb", "2400x2400bb", "fife=w1600",
    "fife=w2000", "fife=w2400",
)

# Amazon dynamic-image sizing token: ._SX475_., ._SY450_., ._UL320_., etc.
_AMAZON_SIZE_TOKEN = re.compile(r"\._(?:S[XLY]|UL|UY|UX|CR|AC|FM)\d+(?:_,\d+,\d+,\d+,\d+)?_\.")

# Amazon image CDN: public CloudFront-fronted host, CORS open, ISBN-10 keyed.
_AMAZON_CDN_URL = "https://m.media-amazon.com/images/P/{isbn10}.01._SCRM_SL2000_.jpg"
# For unknown ASINs Amazon serves a 43-byte image/gif placeholder; real covers
# are JPEGs measured in tens-to-hundreds of kilobytes. Anything below this
# threshold is almost certainly the placeholder, not a cover.
_AMAZON_CDN_MIN_BYTES = 5_000


def boost_covers(records: List[Dict]) -> List[Dict]:
    """Mutate each MetaRecord-as-dict in place, upgrading record["cover"] when a
    higher-resolution variant can be found. Returns the same list for chaining.

    Inputs are dicts (post-asdict()) keyed like MetaRecord: title, authors,
    identifiers (with optional 'isbn'), cover, source, etc. Records with no
    title or no cover are skipped.
    """
    if os.environ.get("CWA_COVER_BOOST", "1").lower() in ("0", "false", "no", "off"):
        return records
    if not records:
        return records

    candidates: List[Dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        cover = rec.get("cover") or ""
        if not rec.get("title") or not cover:
            continue
        if any(h in cover for h in _HIGHRES_HINTS):
            continue
        candidates.append(rec)
        if len(candidates) >= _DEFAULT_MAX:
            break

    if not candidates:
        return records

    workers = max(1, min(_DEFAULT_WORKERS, len(candidates)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_boosted_cover_for, rec): rec for rec in candidates}
        for future in concurrent.futures.as_completed(futures):
            rec = futures[future]
            try:
                upgraded = future.result()
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("cover boost: lookup failed for %r: %s", rec.get("title"), exc)
                continue
            if upgraded:
                log.debug(
                    "cover boost: %s -> %s (was %s)",
                    rec.get("title"), upgraded, rec.get("cover"),
                )
                rec["cover"] = upgraded
    return records


def _boosted_cover_for(record: Dict) -> Optional[str]:
    """Return a higher-resolution cover URL for ``record`` or None."""
    title = (record.get("title") or "").strip()
    authors = record.get("authors") or []
    primary_author = (authors[0] if authors else "") or ""
    isbn = _isbn_from(record.get("identifiers") or {})
    record_year = _year_from(record.get("publishedDate"))

    # Path A: Amazon image CDN by ISBN-10. Edition-keyed and authoritative
    # (the Wordsworth Classics "Flaming June" Wuthering Heights cover, etc.
    # only show up here for many trade paperbacks). Skipped when no ISBN or
    # when the operator turned the path off.
    if _AMAZON_CDN_ENABLED and isbn:
        isbn10 = _to_isbn10(isbn)
        if isbn10:
            url = _amazon_cdn_cover_for_isbn10(isbn10)
            if url:
                return url

    # Path B: iTunes lookup by ISBN. Exact-edition match against Apple Books.
    # Apple's catalog occasionally cross-references unrelated books to the
    # same ISBN (collections, omnibuses, mis-tagged anthologies), so we still
    # verify the returned trackName matches the record's title.
    if isbn and title:
        result = _itunes_lookup_isbn(isbn)
        if result and _itunes_result_matches(title, primary_author, result, record_year):
            url = _itunes_artwork(result)
            if url:
                return url

    # Path C: iTunes search by title + first author. Fuzzy-match: only run
    # when we have *no* ISBN (an ISBN that didn't match path B means Apple
    # doesn't stock this edition - falling back to search will surface a
    # different edition's cover and silently overwrite a correct one).
    if title and not isbn:
        result = _itunes_search(title, primary_author)
        if result and _itunes_result_matches(title, primary_author, result, record_year):
            url = _itunes_artwork(result)
            if url:
                return url

    # Path D: Amazon URL rewrite (works only if the current cover is already
    # an Amazon image but at a small sizing token).
    current = record.get("cover") or ""
    if "m.media-amazon.com/images/" in current or "ssl-images-amazon.com/images/" in current:
        rewritten = _AMAZON_SIZE_TOKEN.sub("._SL2000_.", current)
        if rewritten != current:
            return rewritten

    return None


def _isbn_from(identifiers: Dict) -> Optional[str]:
    for key in ("isbn", "isbn_13", "isbn13", "isbn_10", "isbn10"):
        val = identifiers.get(key)
        if val:
            digits = re.sub(r"[^0-9Xx]", "", str(val))
            if len(digits) in (10, 13):
                return digits
    return None


def _to_isbn10(isbn: str) -> Optional[str]:
    """Return the ISBN-10 form of ``isbn`` (already-10 stays as-is, 13 with
    978 prefix is converted). 979-prefixed ISBN-13s have no ISBN-10 form;
    return None for those.
    """
    cleaned = re.sub(r"[^0-9Xx]", "", isbn or "")
    if len(cleaned) == 10:
        return cleaned.upper()
    if len(cleaned) == 13 and cleaned.startswith("978"):
        core = cleaned[3:12]  # 9 digits between the 978 prefix and the check
        total = sum((10 - i) * int(d) for i, d in enumerate(core))
        check = (11 - total % 11) % 11
        return core + ("X" if check == 10 else str(check))
    return None


def _year_from(published_date: object) -> Optional[int]:
    """Pull a 4-digit year out of a record's publishedDate. Records ship
    this as either an empty string, a "YYYY", or a "YYYY-MM-DD"."""
    if not published_date:
        return None
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b", str(published_date))
    return int(match.group(1)) if match else None


def _amazon_cdn_cover_for_isbn10(isbn10: str) -> Optional[str]:
    """HEAD-probe Amazon's image CDN for ``isbn10``. Returns the URL when
    Amazon serves a real cover (image/jpeg above the placeholder threshold);
    None for the 43-byte image/gif Amazon serves for unknown ASINs.
    """
    url = _AMAZON_CDN_URL.format(isbn10=isbn10)
    try:
        resp = requests.head(
            url,
            headers={"User-Agent": getattr(constants, "USER_AGENT", "Calibre-Web")},
            timeout=_DEFAULT_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        log.debug("amazon CDN HEAD %s failed: %s", url, exc)
        return None
    if resp.status_code != 200:
        return None
    ctype = (resp.headers.get("content-type") or "").lower()
    if not ctype.startswith("image/jpeg"):
        return None
    try:
        clen = int(resp.headers.get("content-length") or 0)
    except (TypeError, ValueError):
        clen = 0
    if clen and clen < _AMAZON_CDN_MIN_BYTES:
        return None
    return url


def _itunes_lookup_isbn(isbn: str) -> Optional[Dict]:
    try:
        resp = requests.get(
            "https://itunes.apple.com/lookup",
            params={"isbn": isbn, "country": "us", "media": "ebook"},
            headers={"User-Agent": getattr(constants, "USER_AGENT", "Calibre-Web")},
            timeout=_DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
    except (requests.RequestException, ValueError):
        return None
    results = payload.get("results") or []
    return results[0] if results else None


def _itunes_search(title: str, author: str) -> Optional[Dict]:
    term = f"{title} {author}".strip()
    if not term:
        return None
    try:
        resp = requests.get(
            "https://itunes.apple.com/search",
            params={
                "term": term,
                "country": "us",
                "media": "ebook",
                "entity": "ebook",
                "limit": 3,
            },
            headers={"User-Agent": getattr(constants, "USER_AGENT", "Calibre-Web")},
            timeout=_DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
    except (requests.RequestException, ValueError):
        return None
    # Apple occasionally returns audiobook/wrapper entries despite media=ebook.
    for result in payload.get("results") or []:
        kind = result.get("kind") or result.get("wrapperType")
        if kind in ("ebook", "audiobook"):
            return result
    return None


def _itunes_result_matches(
    query_title: str,
    query_author: str,
    result: Dict,
    record_year: Optional[int] = None,
) -> bool:
    """Conservative fuzzy check that the iTunes hit is for the same book.

    Avoids replacing covers with unrelated images when the search engine
    returns a loose match. Title token overlap >=70% with first 6 tokens.
    When ``record_year`` is set, the iTunes hit's release year must agree
    within ±20 years; Apple frequently lists republications of public-
    domain classics that, while titled the same, ship a wholly different
    cover (e.g. a 2008 Penguin ebook of Wuthering Heights vs. a 1992
    Wordsworth print edition).
    """
    if not result:
        return False
    track = (result.get("trackName") or result.get("collectionName") or "").lower()
    if not track:
        return False
    # Volume guard (#638): the token overlap below compares only the first
    # 6 tokens and _tokenize drops tokens <=2 chars, so "Vol.1" vs "Vol.3"
    # of a long-titled series are indistinguishable to it - Apple returns
    # the same first hit for every volume's search and the whole series
    # collapses onto one cover. A volume designator, when present on
    # either side, must agree exactly; a missed boost keeps the record's
    # original (correct) cover, which is the cheaper failure.
    if _volume_number(query_title) != _volume_number(track):
        return False
    qtokens = _tokenize(query_title)[:6]
    if not qtokens:
        return False
    rtokens = set(_tokenize(track))
    overlap = sum(1 for t in qtokens if t in rtokens) / float(len(qtokens))
    if overlap < 0.7:
        return False
    if query_author:
        artist = (result.get("artistName") or "").lower()
        atokens = _tokenize(query_author)
        if atokens and not any(t in artist for t in atokens):
            return False
    if record_year is not None:
        result_year = _year_from(result.get("releaseDate"))
        if result_year is not None and abs(result_year - record_year) > 20:
            return False
    return True


def _tokenize(text: str) -> List[str]:
    return [tok for tok in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(tok) > 2]


_VOLUME_MARKER_RE = re.compile(
    r"\b(?:vol(?:ume)?|tome|book|part)\.?\s*0*(\d{1,4})\b|#\s*0*(\d{1,4})\b",
    re.IGNORECASE,
)
_NO_MARKER_RE = re.compile(r"\bno\.?\s*0*(\d{1,4})\b", re.IGNORECASE)
_TRAILING_INT_RE = re.compile(r"(?:^|[\s:,\-(\[])0*(\d{1,4})\s*[)\]]?\s*$")


def _volume_number(title: str) -> Optional[int]:
    """Best-effort volume/issue number from a book title, None when absent.

    Recognizes explicit markers ("Vol. 3", "Volume 3", "Tome 3", "Book 3",
    "Part 3", "#3"), then "No. 3", then a bare trailing integer ("Foo 3").
    "No" is checked only after the strong markers because it doubles as a
    series name ("No. 6 Vol. 3" must extract 3, not the leftmost 6 -
    otherwise every volume of such a series extracts the series number on
    both sides and the guard is neutralized). Titles that ARE numbers
    ("1984") yield that number on both sides of a comparison, so
    equal-title matches are unaffected.
    """
    text = title or ""
    m = _VOLUME_MARKER_RE.search(text)
    if m:
        return int(m.group(1) or m.group(2))
    m = _NO_MARKER_RE.search(text)
    if m:
        return int(m.group(1))
    m = _TRAILING_INT_RE.search(text)
    if m:
        return int(m.group(1))
    return None


def _itunes_artwork(result: Optional[Dict]) -> Optional[str]:
    """Extract artwork URL from an iTunes result and upgrade to 1500px.

    iTunes returns artworkUrl100 / artworkUrl60 with a path segment like
    ``100x100bb.jpg``; their CDN serves arbitrary sizes when you replace
    that segment. 1500x1500bb is the sweet spot for Kobo Libra Color.
    """
    if not result:
        return None
    art = (
        result.get("artworkUrl100")
        or result.get("artworkUrl512")
        or result.get("artworkUrl60")
        or result.get("artworkUrl30")
    )
    if not art:
        return None
    upgraded = re.sub(r"/\d+x\d+bb\.jpg$", "/1500x1500bb.jpg", art)
    upgraded = re.sub(r"/\d+x\d+bb\.png$", "/1500x1500bb.png", upgraded)
    return upgraded if upgraded.startswith("http") else None
