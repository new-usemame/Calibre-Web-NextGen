# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for CW #3668 — download endpoints crash when the
``User-Agent`` request header is missing.

Reporter (janeczku/calibre-web#3668): a download request that omits the
``User-Agent`` header 500s. Root cause:

    client = "kobo" if "Kobo" in request.headers.get('User-Agent') else ""

``request.headers.get('User-Agent')`` returns ``None`` when the header is
absent, so ``"Kobo" in None`` raises ``TypeError: argument of type
'NoneType' is not iterable`` before any book logic runs. Any client that
does not send a User-Agent (curl/scripts, some OPDS readers, some
e-readers) gets a 500 instead of their download.

The same expression lived in two places on our diverged tree:

  * ``cps/web.py``   → ``download_link``       (/download/<id>/<fmt>)
  * ``cps/opds.py``  → ``opds_download_link``  (/opds/download/<id>/<fmt>/)

Fix: give ``.get`` an empty-string default (the None-safe style already
used in ``cps/editbooks.py`` and ``cps/render_template.py``), so a
UA-less request resolves ``client == ""`` instead of crashing.

These tests evaluate the ACTUAL source expression from each module
against a real werkzeug ``Headers`` object with no User-Agent — so a
regression back to the None-unsafe form re-raises ``TypeError`` and the
test goes red.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from werkzeug.datastructures import Headers

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_PY = REPO_ROOT / "cps" / "web.py"
OPDS_PY = REPO_ROOT / "cps" / "opds.py"


class _FakeRequest:
    """Minimal stand-in exposing ``.headers`` like a Flask request."""

    def __init__(self, headers: Headers) -> None:
        self.headers = headers


def _extract_client_expr(source_path: Path, func_name: str) -> str:
    """Pull the RHS of the ``client = ...`` assignment out of a function.

    Returns the raw expression text as it appears in the source, so the
    test exercises exactly what ships — not a paraphrase.
    """
    src = source_path.read_text()
    func = re.search(rf"def {re.escape(func_name)}\(", src)
    assert func, f"Could not locate `def {func_name}(` in {source_path.name}"
    assign = re.search(r"client\s*=\s*(?P<expr>.+)", src[func.start():])
    assert assign, f"Could not locate `client = ...` in {func_name}"
    return assign.group("expr").strip()


def _eval_client(expr: str, user_agent):
    # ``expr`` is the ``client = ...`` RHS extracted verbatim from our own
    # committed cps/web.py / cps/opds.py source (not user input) — evaluating
    # it is the established source-pin technique for exercising exactly what
    # ships. No external/untrusted data reaches eval().
    headers = Headers()
    if user_agent is not None:
        headers["User-Agent"] = user_agent
    return eval(expr, {}, {"request": _FakeRequest(headers)})  # noqa: S307


# ---- the two shipping expressions under test ---------------------------

WEB_EXPR = _extract_client_expr(WEB_PY, "download_link")
OPDS_EXPR = _extract_client_expr(OPDS_PY, "opds_download_link")


@pytest.mark.parametrize("expr", [WEB_EXPR, OPDS_EXPR], ids=["web", "opds"])
def test_missing_user_agent_does_not_crash(expr):
    # Pre-fix this raised TypeError: argument of type 'NoneType' ...
    assert _eval_client(expr, None) == ""


@pytest.mark.parametrize("expr", [WEB_EXPR, OPDS_EXPR], ids=["web", "opds"])
def test_kobo_user_agent_still_detected(expr):
    assert _eval_client(expr, "Mozilla/5.0 (Kobo Touch)") == "kobo"


@pytest.mark.parametrize("expr", [WEB_EXPR, OPDS_EXPR], ids=["web", "opds"])
def test_ordinary_user_agent_is_not_kobo(expr):
    assert _eval_client(expr, "curl/8.4.0") == ""


@pytest.mark.parametrize("expr", [WEB_EXPR, OPDS_EXPR], ids=["web", "opds"])
def test_empty_user_agent_does_not_crash(expr):
    assert _eval_client(expr, "") == ""
