# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A tiny EPUB that stands in for a book that is not on the device yet.

The KOReader library shows every book in the user's e-reader scope as a cover
in KOReader's own grid. A book that has not been downloaded is this file: the
cover (with a small cloud badge so it reads as "in the cloud"), the metadata
KOReader lists and sorts by, and one page explaining that opening the book
downloads it. The plugin recognises it by ``META-INF/cwng-placeholder.json``.

The bytes depend only on their inputs (fixed zip timestamps), so an unchanged
book always yields an identical placeholder, and :func:`cached` keeps recent
ones so the same placeholder is not built twice.
"""

import io
import json
import threading
import zipfile
from collections import OrderedDict
from xml.sax.saxutils import escape, quoteattr

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .. import logger

log = logger.create()

MIMETYPE = "application/epub+zip"
MARKER_PATH = "META-INF/cwng-placeholder.json"
# A device downloads one placeholder per book in the library, so the cover is
# kept to what an e-ink grid shows: about 600 pixels tall at JPEG quality 75,
# typically 20-40 KB.
COVER_MAX = (400, 600)
COVER_QUALITY = 75
# The most pixels a cover may decode to. A JPEG decodes straight to a fraction
# of its size (draft), so this only turns away covers stored in other formats
# at sizes no cover needs: a 12000x12000 PNG named cover.jpg took 1.7 s and
# 760 MB of memory for one placeholder. Such a book gets the plain cover.
MAX_DECODED_PIXELS = 4096 * 4096
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _load_cover(cover_path):
    if not cover_path:
        return None
    try:
        with Image.open(cover_path) as source:
            source.draft("RGB", COVER_MAX)  # JPEG: decode at a reduced scale
            width, height = source.size     # what decoding will now produce
            if width * height > MAX_DECODED_PIXELS:
                log.warning("KOReader placeholder: %s is %dx%d %s, too large to "
                            "decode for a cover; using a plain cover", cover_path,
                            width, height, source.format)
                return None
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    image.thumbnail(COVER_MAX, Image.LANCZOS)
    return image


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError, ValueError):  # Pillow built without FreeType
        return ImageFont.load_default()


def _wrap(draw, text, font, width, max_lines):
    words = (text or "").split()
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if draw.textlength(candidate, font=font) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and " ".join(lines) != " ".join(words):
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _text_cover(title, authors):
    """A plain typographic cover for a book without one."""
    width, height = COVER_MAX
    image = Image.new("RGB", (width, height), (236, 232, 224))
    draw = ImageDraw.Draw(image)
    margin = 32
    draw.rectangle((margin // 2, margin // 2, width - margin // 2, height - margin // 2),
                   outline=(90, 84, 76), width=3)
    title_font = _font(38)
    y = 110
    for line in _wrap(draw, title or "", title_font, width - 2 * margin, 6):
        line_width = draw.textlength(line, font=title_font)
        draw.text(((width - line_width) / 2, y), line, fill=(40, 36, 32), font=title_font)
        y += 48
    author_font = _font(26)
    y = max(y + 36, 380)
    for line in _wrap(draw, ", ".join(authors or ()), author_font, width - 2 * margin, 3):
        line_width = draw.textlength(line, font=author_font)
        draw.text(((width - line_width) / 2, y), line, fill=(70, 64, 58), font=author_font)
        y += 34
    return image


def _badge(diameter):
    """A round dark badge with a white cloud and a download arrow."""
    scale = 4
    size = diameter * scale
    badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    draw.ellipse((0, 0, size - 1, size - 1), fill=(34, 34, 34, 235),
                 outline=(255, 255, 255, 255), width=max(2, size // 22))
    white = (255, 255, 255, 255)
    s = size / 100.0
    # Cloud: three puffs over a flat base.
    draw.ellipse((22 * s, 38 * s, 50 * s, 66 * s), fill=white)
    draw.ellipse((38 * s, 26 * s, 70 * s, 58 * s), fill=white)
    draw.ellipse((56 * s, 40 * s, 80 * s, 64 * s), fill=white)
    draw.rounded_rectangle((28 * s, 48 * s, 74 * s, 66 * s), radius=9 * s, fill=white)
    # Arrow pointing down, cut out of the cloud in the badge colour.
    dark = (34, 34, 34, 255)
    draw.rectangle((46 * s, 38 * s, 54 * s, 60 * s), fill=dark)
    draw.polygon([(38 * s, 56 * s), (62 * s, 56 * s), (50 * s, 70 * s)], fill=dark)
    return badge.resize((diameter, diameter), Image.LANCZOS)


def cover_jpeg(cover_path, title, authors):
    """The placeholder cover: the book's cover (or a plain one) with the badge."""
    image = _load_cover(cover_path) or _text_cover(title, authors)
    width, height = image.size
    diameter = max(24, round(min(width, height) * 0.2))
    margin = max(6, round(width * 0.035))
    badge = _badge(diameter)
    image.paste(badge, (width - diameter - margin, height - diameter - margin), badge)
    output = io.BytesIO()
    image.save(output, "JPEG", quality=COVER_QUALITY, optimize=True, progressive=False)
    return output.getvalue()


def _opf(*, book_id, title, authors, series, series_index, language, modified):
    creators = "\n".join(
        '    <dc:creator id="creator%d">%s</dc:creator>' % (index, escape(name))
        for index, name in enumerate(authors or (), start=1))
    series_meta = ""
    if series:
        index_text = ("%g" % series_index) if series_index is not None else "1"
        series_meta = (
            "\n    <meta name=\"calibre:series\" content=%s/>"
            "\n    <meta name=\"calibre:series_index\" content=%s/>"
            "\n    <meta property=\"belongs-to-collection\" id=\"series\">%s</meta>"
            "\n    <meta refines=\"#series\" property=\"collection-type\">series</meta>"
            "\n    <meta refines=\"#series\" property=\"group-position\">%s</meta>"
        ) % (quoteattr(series), quoteattr(index_text), escape(series), escape(index_text))
    return """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang=%(lang)s>
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:cwng:book:%(id)d</dc:identifier>
    <dc:title>%(title)s</dc:title>
%(creators)s
    <dc:language>%(lang_text)s</dc:language>
    <meta property="dcterms:modified">%(modified)s</meta>
    <meta name="cover" content="cover-image"/>
    <meta name="cwng:placeholder" content="%(id)d"/>%(series)s
  </metadata>
  <manifest>
    <item id="cover-image" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="page" href="placeholder.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="page"/>
  </spine>
</package>
""" % {
        "id": book_id, "title": escape(title or ""), "creators": creators,
        "lang": quoteattr(language), "lang_text": escape(language),
        "modified": escape(modified), "series": series_meta,
    }


def _nav(title, language):
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang=%(lang)s lang=%(lang)s>
<head><title>%(title)s</title></head>
<body>
  <nav epub:type="toc" id="toc"><ol><li><a href="placeholder.xhtml">%(title)s</a></li></ol></nav>
</body>
</html>
""" % {"lang": quoteattr(language), "title": escape(title or "")}


def _ncx(book_id, title):
    return """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:cwng:book:%(id)d"/></head>
  <docTitle><text>%(title)s</text></docTitle>
  <navMap>
    <navPoint id="page" playOrder="1">
      <navLabel><text>%(title)s</text></navLabel>
      <content src="placeholder.xhtml"/>
    </navPoint>
  </navMap>
</ncx>
""" % {"id": book_id, "title": escape(title or "")}


def _page(*, title, authors, language, heading, lines):
    paragraphs = "\n".join("  <p>%s</p>" % escape(line) for line in lines)
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang=%(lang)s lang=%(lang)s>
<head>
  <title>%(title)s</title>
  <style>body{margin:8%% 6%%;font-family:serif}h1{font-size:1.4em;margin:0 0 .2em}p.by{margin:0 0 2em;font-style:italic}h2{font-size:1.1em;margin:0 0 .6em}</style>
</head>
<body>
  <h1>%(title)s</h1>
  <p class="by">%(authors)s</p>
  <h2>%(heading)s</h2>
%(paragraphs)s
</body>
</html>
""" % {
        "lang": quoteattr(language), "title": escape(title or ""),
        "authors": escape(", ".join(authors or ())), "heading": escape(heading),
        "paragraphs": paragraphs,
    }


def build(*, book_id, rev, title, authors, series, series_index, cover_path,
          language, modified, heading, lines):
    """Return the placeholder EPUB for one book as bytes."""
    book_id = int(book_id)
    language = language or "en"
    files = [
        ("META-INF/container.xml", _CONTAINER.encode("utf-8"), zipfile.ZIP_DEFLATED),
        (MARKER_PATH, json.dumps({"book_id": book_id, "rev": rev},
                                 separators=(",", ":")).encode("utf-8"),
         zipfile.ZIP_DEFLATED),
        ("OEBPS/content.opf", _opf(
            book_id=book_id, title=title, authors=authors, series=series,
            series_index=series_index, language=language,
            modified=modified).encode("utf-8"), zipfile.ZIP_DEFLATED),
        ("OEBPS/nav.xhtml", _nav(title, language).encode("utf-8"), zipfile.ZIP_DEFLATED),
        ("OEBPS/toc.ncx", _ncx(book_id, title).encode("utf-8"), zipfile.ZIP_DEFLATED),
        ("OEBPS/placeholder.xhtml", _page(
            title=title, authors=authors, language=language, heading=heading,
            lines=lines).encode("utf-8"), zipfile.ZIP_DEFLATED),
        ("OEBPS/cover.jpg", cover_jpeg(cover_path, title, authors), zipfile.ZIP_STORED),
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        # The OCF container requires "mimetype" first, stored, no extra field.
        mimetype = zipfile.ZipInfo("mimetype", date_time=_ZIP_TIME)
        mimetype.compress_type = zipfile.ZIP_STORED
        archive.writestr(mimetype, MIMETYPE)
        for name, data, compression in files:
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.compress_type = compression
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


class _RecentBytes:
    """The most recently used byte strings by key, within a byte budget."""

    def __init__(self, max_bytes):
        self.max_bytes = max_bytes
        self._items = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            data = self._items.get(key)
            if data is not None:
                self._items.move_to_end(key)
            return data

    def put(self, key, data):
        if len(data) > self.max_bytes:
            return
        with self._lock:
            previous = self._items.pop(key, None)
            if previous is not None:
                self._size -= len(previous)
            self._items[key] = data
            self._size += len(data)
            while self._size > self.max_bytes:
                _key, dropped = self._items.popitem(last=False)
                self._size -= len(dropped)


# A device fetches each placeholder once and then asks with If-None-Match, but
# a second device, a device that lost its copy, or a client that never sends
# the header would have the same placeholder built again: its cover decoded,
# scaled and compressed. Placeholders are 10-90 KB; 32 MB holds a few hundred.
_CACHE = _RecentBytes(32 * 1024 * 1024)


def cached(key, make):
    """The placeholder stored under ``key``, made with ``make()`` if not held.

    ``key`` must name everything the bytes depend on (the book, its revision
    and the language), as the placeholder ETag does.
    """
    data = _CACHE.get(key)
    if data is None:
        data = make()
        _CACHE.put(key, data)
    return data
