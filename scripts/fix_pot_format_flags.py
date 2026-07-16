#!/usr/bin/env python3
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Strip babel's mis-detected ``python-format`` flag from brace-format entries.

Why this exists (fork issue #936)
---------------------------------
``pybabel extract`` decides an entry is ``python-format`` with a regex that
accepts ``%`` + flags + a conversion char. In ``"{pct}% read"`` the percent is
LITERAL — but ``% r`` parses as space-flag + ``r`` conversion, so babel emits::

    #, python-brace-format, python-format
    msgid "{pct}% read"

A msgid is interpolated by exactly one mechanism: ``%``-format or
``str.format``. Both flags on one entry is therefore always a mis-detection,
and it is not cosmetic — it INVERTS ``msgfmt --check``. gettext validates the
translation against the phantom ``% r`` spec, so:

* a msgstr that keeps the phantom spec ("Прочитано: {pct}% r") PASSES, and
* a msgstr that correctly drops it ("{pct}% lido") FATALS.

The corrupt Russian string shipped to screen-reader users for two releases
because it was faithful to a bogus spec, while the correct Brazilian
Portuguese one was the only locale the checker complained about.

Nearly every English word after a literal percent triggers this: read→``% r``,
done→``% d``, complete→``% c``, finished→``% f``. Rewording the msgid is not a
fix, it is a coin flip. Fixing the extractor output is.

Usage
-----
    python3 scripts/fix_pot_format_flags.py messages.pot [more.po ...]

Runs after ``pybabel extract`` in ``scripts/update_translations.sh``, before
``msgmerge`` fans the POT out to each locale. ``msgmerge`` drops the flag from
a .po once it is absent from the POT, so cleaning the POT is what keeps this
fixed across regeneration; the .po arguments are for one-time repair of files
that already carry it.

Exit codes: 0 = clean or repaired, 1 = usage/IO error. Idempotent.
"""

import re
import sys


FLAGS_LINE = re.compile(r"^#,(.*)$")


def strip_bogus_python_format(text):
    """Return (new_text, n_fixed) with python-format dropped where the entry
    is also python-brace-format.

    Operates line-wise on the flags comment only; msgids, msgstrs, source
    references and translator comments are never touched.
    """
    out = []
    n_fixed = 0
    for line in text.split("\n"):
        match = FLAGS_LINE.match(line)
        if not match:
            out.append(line)
            continue

        flags = [f.strip() for f in match.group(1).split(",") if f.strip()]
        if "python-format" in flags and "python-brace-format" in flags:
            flags = [f for f in flags if f != "python-format"]
            n_fixed += 1
            out.append("#, " + ", ".join(flags))
        else:
            out.append(line)
    return "\n".join(out), n_fixed


def fix_file(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    new_text, n_fixed = strip_bogus_python_format(text)
    if n_fixed:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        print(f"[i] {path}: stripped bogus python-format from {n_fixed} entr"
              f"{'y' if n_fixed == 1 else 'ies'}")
    return n_fixed


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().split("Usage\n-----\n")[-1], file=sys.stderr)
        return 1

    total = 0
    for path in argv[1:]:
        try:
            total += fix_file(path)
        except OSError as exc:
            print(f"[!] {path}: {exc}", file=sys.stderr)
            return 1

    if not total:
        print("[i] No bogus python-format flags found — nothing to do.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
