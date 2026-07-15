# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Serves the SPA shell at /app. Opt-OUT via env CWNG_SPA (default: enabled)."""
import json
import os
import re
from flask import Blueprint, request, Response, abort, current_app

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


def spa_available():
    """The SPA is available to THIS request: the opt-out env is on AND the built
    bundle is on disk. The single source of truth the layout nudge, the SPA shell
    guard, and the classic-index sticky-redirect all gate on — the context
    processor exposes the same value to templates as ``cwng_spa_enabled``."""
    return _spa_enabled() and _spa_bundle_present()


@spa.app_context_processor
def _inject_spa_flag():
    """Expose to ALL Jinja templates whether the new SPA is available (so the
    legacy layout shows the 'Switch to New UI' nudge only when /app will actually
    load) plus the running version (so the nudge banner can reset its dismissal
    on each update). app_context_processor = app-wide, not just this blueprint."""
    return {
        "cwng_spa_enabled": spa_available(),
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


# Sticky new-UI preference (#739). The SPA shell stamps this cookie when it
# loads; the classic web index ('/') redirects to the shell while it's present,
# and the SPA's "Back to classic view" nav clears it. Per-browser only (no DB,
# no account) — a user who picked the new UI once keeps it, on every tab and
# bookmark, until they switch back.
PREFER_SPA_COOKIE = "cwng_prefer_spa"
_PREFER_SPA_MAX_AGE = 60 * 60 * 24 * 365  # one year


def prefer_spa_cookie_path():
    """Scope the preference cookie to the app's mount prefix (request.script_root),
    or '/' at the domain root. Two CWNG instances on different subpaths of one
    host must not share the cookie, and the path must match between set and
    delete or the browser keeps both — so both go through here. Mirrors how Flask
    scopes the session cookie (which also follows SCRIPT_NAME)."""
    return _mount_prefix() or "/"


def stamp_prefer_spa_cookie(resp):
    """Set the 'user prefers the SPA' cookie on a response (used when the SPA
    shell is served). ``httponly=False`` so the SPA runtime can read it; Secure
    and SameSite mirror the session cookie so they share transport guarantees."""
    resp.set_cookie(
        PREFER_SPA_COOKIE,
        value="1",
        max_age=_PREFER_SPA_MAX_AGE,
        path=prefer_spa_cookie_path(),
        secure=bool(current_app.config.get("SESSION_COOKIE_SECURE", False)),
        samesite=current_app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
        httponly=False,
    )
    return resp


def clear_prefer_spa_cookie(resp):
    """Delete the 'user prefers the SPA' cookie — used when the user returns to
    the classic UI from the SPA. Same path the set used, else the browser keeps
    both."""
    resp.delete_cookie(PREFER_SPA_COOKIE, path=prefer_spa_cookie_path())
    return resp


def classic_index_redirects_to_spa():
    """Should the classic web index ('/') bounce to the SPA shell? True only when
    the SPA is available, the browser carries the ``cwng_prefer_spa`` cookie, this
    is NOT the SPA's own 'back to classic' marker (``cwng_feedback``), and the
    client wants HTML (not an API/OPDS machine client). Web index only — never
    books_list, authors, OPDS, Kobo, API, or login (#739 design)."""
    if request.args.get("cwng_feedback"):
        return False
    return preferred_spa_html_request()


def preferred_spa_html_request():
    """Whether this browser should use the SPA for an HTML surface.

    Unlike :func:`classic_index_redirects_to_spa`, this has no route-specific
    ``cwng_feedback`` exception, so it can also route the anonymous login page.
    The destination remains the app-owned SPA shell; callers must never redirect
    directly to a user-controlled ``next`` value.
    """
    if not spa_available():
        return False
    if request.cookies.get(PREFER_SPA_COOKIE) != "1":
        return False
    return bool(request.accept_mimetypes.accept_html)


def spa_shell_url():
    """Return the local, prefix-aware URL for the SPA shell.

    ``url_for`` includes ``request.script_root`` verbatim.  That value normally
    comes from a trusted reverse proxy, but a malformed forwarded prefix such as
    ``//evil.example`` would turn a redirect into a scheme-relative off-site
    destination.  Reuse the same strict prefix sanitizer that protects the SPA
    shell's asset and API URLs, then append the fixed app-owned route.
    """
    return f"{_mount_prefix()}/app/"


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
    resp = _render_shell(index_path, _mount_prefix())
    # #739: loading the SPA is the act of choosing it — persist the preference so
    # a later visit to a classic URL lands back on the new UI instead of reverting.
    stamp_prefer_spa_cookie(resp)
    return resp
