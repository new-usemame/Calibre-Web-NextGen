# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Precomposed, zero-egress bug-report links for the CLASSIC (Jinja) UI.

The server-side counterpart to ``frontend/src/lib/reportBuilder.ts``. Same
contract, and it is the whole point of both: **this module never transmits
anything.** It builds a URL string. Only the user, clicking it in their own
browser and their own GitHub session, ever sends a byte — so no IP, payload or
fact about the instance reaches us unless they personally post it.

Kept deliberately small rather than mirroring the TypeScript module. The two
sides compose independent reports for independent surfaces and never have to
agree on anything except the tracker URL, so there is no byte-for-byte
correspondence here to drift out of sync. What is NOT duplicated is the
browser/route allowlist logic — server-side we have something better than the
client's heuristic: Flask's own matched route rule, which is already a pattern.

The redaction rule is the same allowlist stance: enumerate the few fields that
are safe by construction, never take a rich object and strip it. In particular
this deliberately does NOT include the traceback (it carries filesystem paths
and is already admin-gated in the template), the library title
(``config_calibre_web_title`` — user-chosen and potentially identifying), or
anything derived from the request URL beyond the matched rule.
"""
from urllib.parse import urlencode

from flask import request

from . import constants

ISSUES_NEW = "https://github.com/new-usemame/Calibre-Web-NextGen/issues/new"

# GitHub rejects an over-long URL, and the failure is bad: the user clicks
# "report", lands on an error page, and the report is gone. Stay well under it.
_MAX_URL = 6000


def _route_pattern():
    """The matched Flask rule, e.g. ``/book/<int:book_id>``.

    This is a route SHAPE by construction — the dynamic parts are converter
    placeholders, not the user's values — so it cannot carry a book id, a title
    slug or any other fact about their library. Falls back to a constant rather
    than ``request.path`` if nothing matched: on a 404 the raw path is
    attacker- or user-supplied text, and echoing it into a prefilled public
    issue body would be a reflection vector.
    """
    try:
        if request.url_rule is not None:
            return str(request.url_rule.rule)
    except Exception:
        pass
    return "(no matched route)"


def build_issue_url(error_code, error_name=""):
    """A prefilled GitHub issue URL for a server error page.

    ``error_code``/``error_name`` are OUR OWN strings (``"500 Internal Server
    Error"`` and werkzeug's static description), not user data — which is why
    they are safe to include verbatim.

    ⚠️ **That is the precondition for the body having no code fence.** If a
    future caller passes anything user- or library-derived (the obvious
    candidate is ``error_stack``), it needs the fence-escalation treatment the
    TypeScript side uses: a plain ```` ``` ```` fence CLOSES at the first
    ```` ``` ```` inside it, and the remainder then renders as live Markdown —
    headings, links and images — in a PUBLIC issue. Escaping is not optional
    there; pick a fence longer than any backtick run in the content.
    """
    route = _route_pattern()
    body = (
        "<!-- What were you doing when this happened? -->\n"
        "\n"
        "### What happened\n"
        "\n"
        "%s\n"
        "\n"
        "### Environment\n"
        "\n"
        "| | |\n"
        "|---|---|\n"
        "| Version | %s |\n"
        "| Page | `%s` |\n"
        "| Interface | Classic |\n"
        "\n"
        "<sub>This was prefilled by your own Calibre-Web-NextGen instance and "
        "nothing has been sent anywhere — posting it is entirely your choice, "
        "and you can edit or delete any of it first.</sub>"
    ) % (error_name or error_code, constants.INSTALLED_VERSION, route)

    title = "[Bug] %s on %s" % (error_code, route)
    url = "%s?%s" % (ISSUES_NEW, urlencode({"title": title, "body": body, "labels": "bug"}))
    if len(url) <= _MAX_URL:
        return url
    # Nothing here is near the cap today, but a future caller passing a longer
    # error_name should get a link that still works rather than a GitHub 404.
    short = "%s?%s" % (ISSUES_NEW, urlencode({"title": title[:120], "labels": "bug"}))
    return short
