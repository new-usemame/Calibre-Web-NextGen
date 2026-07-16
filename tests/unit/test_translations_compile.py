# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression test for fork issue #121 sub-bug 3 follow-up — every shipped
.po file must compile cleanly with ``msgfmt``.

Background: ``scripts/compile_translations.sh`` deliberately doesn't fail
the Docker build when one locale fails to compile (so a broken .po in one
language doesn't strand all the others). The cost of that policy is that
per-locale failures are silent unless someone reads the build logs — and
that's exactly how v4.0.47 shipped without ``hu/messages.mo``: I'd added
four new translations but the file also contained an obsolete ``#~ msgid``
duplicate of one of them; msgfmt rejected the file, the build kept going,
and on teenyverse Hungarian users got the English fallback.

This test runs plain ``msgfmt`` — the exact invocation the Docker build
uses — against every ``messages.po`` in the translations tree, so a .po
that would be silently dropped from the image fails a unit test first.
Acts as the test-suite-side checker the deferred Docker build doesn't
enforce.

The stricter ``msgfmt --check`` format-consistency classes are gated
separately in ``test_i18n_format_flags.py``; that split is deliberate.
This file answers "does the locale ship at all", that one answers "is the
string it ships correct" (#936).
"""

import glob
import os
import shutil
import subprocess

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TRANSLATIONS_DIR = os.path.join(REPO_ROOT, "cps", "translations")


def _discover_po_files():
    pattern = os.path.join(TRANSLATIONS_DIR, "*", "LC_MESSAGES", "messages.po")
    return sorted(glob.glob(pattern))


PO_FILES = _discover_po_files()


@pytest.mark.unit
def test_translations_dir_exists():
    assert os.path.isdir(TRANSLATIONS_DIR), (
        f"Expected translations dir at {TRANSLATIONS_DIR}"
    )


@pytest.mark.unit
def test_at_least_one_po_file_present():
    assert PO_FILES, "No .po files found under cps/translations"


@pytest.mark.unit
@pytest.mark.skipif(
    shutil.which("msgfmt") is None,
    reason="msgfmt not available on this host (install gettext / brew install gettext)",
)
@pytest.mark.parametrize("po_path", PO_FILES, ids=lambda p: p.split(os.sep)[-3])
def test_po_file_compiles_cleanly(po_path, tmp_path):
    """Every shipped .po file must compile cleanly under the same
    invocation `scripts/compile_translations.sh` uses in the Docker build:
    plain ``msgfmt <po> -o <mo>``. Strictly hard errors (duplicate msgid,
    malformed PO syntax, encoding problems) are what we gate on — they
    cause the .mo to be missing in production. Per-format-string warnings
    that ``--check`` would surface are intentionally not gated so this
    test matches what the production build actually rejects.

    Fix hint: an obsolete ``#~ msgid`` line duplicating a newly-added
    active msgid is the most common cause of a silent .mo drop. Delete
    the obsolete block — it's commented out anyway.
    """
    out_mo = tmp_path / "test.mo"
    result = subprocess.run(
        ["msgfmt", po_path, "-o", str(out_mo)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        locale = po_path.split(os.sep)[-3]
        pytest.fail(
            f"msgfmt failed for locale {locale!r} — .mo would not ship:\n"
            f"  {po_path}\n"
            f"--- stderr ---\n{result.stderr.rstrip()}\n"
            f"--- fix hint ---\n"
            f"  Run `msgfmt {po_path}` locally and address every error.\n"
            f"  Most common cause: an obsolete `#~ msgid` line duplicates\n"
            f"  a newly-added active msgid — delete the obsolete block.\n"
        )
    assert out_mo.exists(), "msgfmt should have produced a .mo file"
