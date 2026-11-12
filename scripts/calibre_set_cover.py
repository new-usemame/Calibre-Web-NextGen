#!/usr/bin/env python3
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Set an EPUB's cover so its titlepage keeps the cover's aspect ratio (#2687).

Executed as ``calibre-debug -e calibre_set_cover.py -- <book.epub> <cover.jpg>``
so it runs inside the Calibre interpreter shipped with the image.

``ebook-polish -c`` calls Calibre's ``set_cover`` with no options, which writes
the generated titlepage SVG with ``preserveAspectRatio="none"``: the cover is
stretched to the reader's screen. ``set_cover`` itself accepts ``keep_aspect``,
the option behind Calibre's own "Preserve cover aspect ratio" (the editor's
"Add cover" uses it), and then writes ``xMidYMid meet``. ``ebook-polish`` has no
switch for it, so the enforcer calls this script for the cover instead of
passing ``-c``. The file it leaves is the one Calibre writes with that option.

Prints one ``CWNG_SET_COVER_RESULT=<json>`` line; exit status 0 on success.
"""

import json
import sys


def main(argv):
    if len(argv) != 2:
        print("usage: calibre-debug -e calibre_set_cover.py -- <book.epub> <cover>",
              file=sys.stderr)
        return 2
    book_path, cover_path = argv
    from calibre.ebooks.oeb.polish.container import get_container
    from calibre.ebooks.oeb.polish.cover import set_cover

    container = get_container(book_path, tweak_mode=True)
    if container.book_type != "epub":
        print("CWNG_SET_COVER_RESULT=" + json.dumps(
            {"ok": False, "error": "not an EPUB: %s" % container.book_type}))
        return 3
    set_cover(container, cover_path, report=lambda *a, **k: None,
              options={"keep_aspect": True})
    container.commit(book_path)
    print("CWNG_SET_COVER_RESULT=" + json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    # calibre-debug -e passes the script path first, then everything after "--".
    args = sys.argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    sys.exit(main(args))
