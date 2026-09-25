"""Turn crengine words into requests for browser_cfis.mjs.

usage: python browser_requests.py <book.epub> <engine-words.json> <every-nth> > requests.json

For every n-th engine word, name its range the way the browser script finds
it: the spine index, the index k of its text node among the body's text nodes
that are not whitespace-only (document order), and UTF-16 offsets. The browser
reports the text it found there, so a wrong k shows up as a different word.
Run from the repository root (it reads the XML side through the converter).
"""
import json
import sys

from cps.services import koreader_xpointer as kx

epub, words_path, every = sys.argv[1], sys.argv[2], int(sys.argv[3])
words = json.load(open(words_path, encoding="utf-8"))
out = []
for word in words[::every]:
    points = kx._resolve(epub, lambda book: (kx._point_from_xpointer(book, word["s"]),
                                             kx._point_from_xpointer(book, word["e"])))
    if not points or None in points:
        continue
    (_b, item, start), (_b2, _i2, end) = points
    if start.text is None or start.text != end.text:
        continue
    solid = [(p, o) for p, o, _v in kx._solid_texts(start.chapter.body)]
    k = solid.index((start.text.parent, start.text.ordinal))
    value = start.text.value
    out.append({"spine": item.index, "k": k, "s": word["s"], "e": word["e"], "t": word["t"],
                "start": kx._utf16(value, start.offset), "end": kx._utf16(value, end.offset)})
json.dump(out, sys.stdout)
