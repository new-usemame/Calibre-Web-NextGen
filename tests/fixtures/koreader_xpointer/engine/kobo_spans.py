"""Pair Kobo spans with the crengine words they start on (ground truth for kepub_alignment).

usage: python kobo_spans.py <book.epub> <book.kepub.epub> <engine-words.json> <every-nth> > spans.json

<book.kepub.epub> is what CWNG serves a Kobo for <book.epub>: `kepubify <book.epub>`
(the release the Dockerfile pins), then `normalize_kepub_package(path, split_chapters=True)`.
<engine-words.json> is every word crengine reports in <book.epub> (run-probe.sh ... words).

A row is kept only when crengine itself says a word starts exactly where the
converter places the span, and that word is where the span's text begins; the
rows then pin, for the test, which XPointer each span must come out as. Every
n-th such span is written. Run from the repository root.
"""
import json
import sys

from cps.services import kepub_alignment as ka, koreader_xpointer as kx

epub, kepub_path, words_path, every = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
starts = {w["s"]: w["t"].translate(kx._NO_SPACE) for w in json.load(open(words_path, encoding="utf-8"))}
kepub = ka._kepub(kepub_path)
rows = []
for document in kepub.documents:
    text = kepub.text[document.offset:]
    for span_id, (start, end) in document.spans.items():
        xpointer = ka.span_to_xpointer(epub, kepub_path, document.member, span_id)
        word = starts.get(xpointer)
        if word and text[start:end].startswith(word):
            rows.append({"source": document.member, "span": span_id, "xpointer": xpointer, "word": word})
json.dump(rows[::every], sys.stdout, ensure_ascii=False, indent=0)
