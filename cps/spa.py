# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Serves the SPA shell at /app. Opt-OUT via env CWNG_SPA (default: enabled)."""
import json
import os
import re
from flask import Blueprint, request, Response, abort

from . import logger, constants

log = logger.create()

spa = Blueprint("spa", __name__)

_SPA_DIR = os.path.join(os.path.dirname(__file__), "static", "app")

# An explicit empty value ("CWNG_SPA=") is treated as opt-out too — an operator
# blanking the var clearly means "off". UNSET (env absent) keeps the default-on.
_DISABLE_VALUES = ("", "0", "false", "no", "off")


def _spa_enabled():
    """SPA availability — OPT-OUT (enabled by default).

    Every updated instance should surface the new UI on its own so users can opt
    in, without the operator having to set anything (rollout goal: show the
    'Try the new UI' nudge to everyone, then eventually make it the default). Set
    CWNG_SPA to a falsey value (empty/0/false/no/off) to turn the new UI off."""
    value = os.environ.get("CWNG_SPA")
    if value is None:  # env absent → default ON
        return True
    return value.strip().lower() not in _DISABLE_VALUES


def _spa_bundle_present():
    """The compiled SPA must actually be on disk; a source checkout that never ran
    the Vite build has no bundle, so the nudge would lead to a 404."""
    return os.path.isfile(os.path.join(_SPA_DIR, "index.html"))


@spa.app_context_processor
def _inject_spa_flag():
    """Expose to ALL Jinja templates whether the new SPA is available (so the
    legacy layout shows the 'Switch to New UI' nudge only when /app will actually
    load) plus the running version (so the nudge banner can reset its dismissal
    on each update). app_context_processor = app-wide, not just this blueprint."""
    return {
        "cwng_spa_enabled": _spa_enabled() and _spa_bundle_present(),
        "cwng_app_version": constants.INSTALLED_VERSION,
    }


# A reverse-proxy mount prefix is a URL path: leading-slash segments of
# unreserved URL chars. Anything else (quotes, angle brackets, spaces) is
# rejected to "" so a spoofed X-Forwarded-Prefix / X-Script-Name header can't
# break out of the injected <script> string or the asset-URL rewrite below.
# \Z (not $) so a trailing newline can't sneak past the end anchor.
_SAFE_PREFIX_RE = re.compile(r"^(/[A-Za-z0-9._~-]+)+\Z")


def _mount_prefix():
    """The reverse-proxy path prefix the app is mounted under (e.g. ``/cwa``),
    or ``""`` at the domain root. Sourced from ``request.script_root`` — set by
    ReverseProxied (X-Script-Name) / ProxyFix (X-Forwarded-Prefix) upstream, the
    same value ``url_for`` already uses to build prefixed links for the classic
    UI. Sanitized so it's safe to reflect into HTML/JS."""
    prefix = (request.script_root or "").rstrip("/")
    if prefix and (not _SAFE_PREFIX_RE.match(prefix) or ".." in prefix):
        log.warning("Ignoring unexpected script_root/prefix %r for SPA shell", prefix)
        return ""
    return prefix


def _render_shell(index_path, prefix):
    """Serve the built index.html adapted to the current mount prefix.

    The Vite build hardcodes root-absolute asset URLs (``/static/app/…``); behind
    a reverse-proxy subpath those 404 (the reporter's white page, #571). Rewrite
    them to ``<prefix>/static/app/…`` and expose the prefix to the SPA runtime via
    ``window.__CWNG_PREFIX__`` so its API calls, router base and resource URLs are
    prefixed too. At the domain root (prefix="") the file is served unchanged."""
    with open(index_path, "r", encoding="utf-8") as fh:
        html = fh.read()
    if prefix:
        html = html.replace("/static/app/", prefix + "/static/app/")
    # Inject, into <head>:
    #  * the favicon (#574 — the Vite shell ships none, so the new UI had a blank
    #    tab icon); reuse the app's existing /static/favicon.ico, prefix-aware.
    #  * the mount prefix (even "") so the SPA reads an authoritative value rather
    #    than guessing from the URL. json.dumps → safely-quoted JS string.
    static = prefix + "/static"
    inject = (
        '<link rel="icon" href="%s/favicon.ico">'
        '<link rel="apple-touch-icon" sizes="140x140" href="%s/favicon.ico">'
        '<script>window.__CWNG_PREFIX__=%s;</script>'
    ) % (static, static, json.dumps(prefix))
    html = html.replace("</head>", inject + "</head>", 1)
    return Response(html, mimetype="text/html")


@spa.route("/app")
@spa.route("/app/")
@spa.route("/app/<path:path>")
def spa_shell(path=""):
    if not _spa_enabled():
        abort(404)
    index_path = os.path.join(_SPA_DIR, "index.html")
    if not os.path.isfile(index_path):
        log.warning("SPA shell requested but build artifact not found: %s — run the Vite build "
                    "or set CWNG_SPA=0 to suppress this warning", index_path)
        abort(404)
    return _render_shell(index_path, _mount_prefix())
