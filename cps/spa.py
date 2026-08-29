# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Serves the SPA shell at /app. Opt-OUT via env CWNG_SPA (default: enabled)."""
import json
import os
import re
from html import escape as html_escape
from urllib.parse import parse_qsl, urlencode, urlsplit
from flask import Blueprint, request, Response, abort, current_app, redirect
from werkzeug.datastructures import MIMEAccept
from werkzeug.http import parse_accept_header

from . import logger, constants, config
from .usermanagement import reverse_proxy_header_login

log = logger.create()

spa = Blueprint("spa", __name__)

_SPA_DIR = os.path.join(os.path.dirname(__file__), "static", "app")

# An explicit empty value ("CWNG_SPA=") is treated as opt-out too — an operator
# blanking the var clearly means "off". UNSET (env absent) keeps the default-on.
_DISABLE_VALUES = ("", "0", "false", "no", "off")


def _spa_enabled():
    """SPA availability — OPT-OUT (enabled by default).

    The new UI is the default browser surface. Set CWNG_SPA to a falsey value
    (empty/0/false/no/off) to keep every route on the classic UI."""
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
    bundle is on disk. The single source of truth the classic nav affordance,
    the SPA shell guard, and the classic-index preference redirect all gate on — the context
    processor exposes the same value to templates as ``cwng_spa_enabled``."""
    return _spa_enabled() and _spa_bundle_present()


@spa.app_context_processor
def _inject_spa_flag():
    """Expose to ALL Jinja templates whether the new SPA is available (so the
    legacy layout shows its return-to-SPA affordance only when /app will actually
    load) plus the running version used by the feedback popup.
    app_context_processor = app-wide, not just this blueprint."""
    return {
        "cwng_spa_enabled": spa_available(),
        "cwng_app_version": constants.INSTALLED_VERSION,
        # A callable defers request.script_root lookup until a request-backed
        # template actually renders; app-context-only callers can still inspect
        # this context processor without manufacturing a request.
        "cwng_spa_choice_url": spa_shell_choice_url,
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


# UI preference cookies (#739/#908). ``cwng_prefer_spa`` is retained for
# compatibility with browsers and older releases that used the opt-in scheme.
# The current scheme is an explicit Classic opt-out: no Classic cookie means
# SPA, and only the marked Classic-nav action clears that opt-out. Merely
# entering /app (including a shared deep link) preserves it. Both cookies remain
# per-browser.
PREFER_SPA_COOKIE = "cwng_prefer_spa"
PREFER_CLASSIC_COOKIE = "cwng_prefer_classic"
_UI_PREFERENCE_MAX_AGE = 60 * 60 * 24 * 365  # one year
_SPA_CHOICE_PARAM = "cwng_switch"
_SPA_CHOICE_VALUE = "spa"


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
        max_age=_UI_PREFERENCE_MAX_AGE,
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


def stamp_prefer_classic_cookie(resp):
    """Persist an explicit choice to use Classic on the two preference-routed
    surfaces. The cookie shares the legacy preference cookie's security and
    reverse-proxy path rules."""
    resp.set_cookie(
        PREFER_CLASSIC_COOKIE,
        value="1",
        max_age=_UI_PREFERENCE_MAX_AGE,
        path=prefer_spa_cookie_path(),
        secure=bool(current_app.config.get("SESSION_COOKIE_SECURE", False)),
        samesite=current_app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
        httponly=False,
    )
    return resp


def clear_prefer_classic_cookie(resp):
    """Remove the Classic opt-out when the user chooses the SPA again."""
    resp.delete_cookie(PREFER_CLASSIC_COOKIE, path=prefer_spa_cookie_path())
    return resp


def classic_index_redirects_to_spa():
    """Should the classic web index ('/') bounce to the SPA shell? True only when
    the SPA is available, this is NOT the SPA's own 'back to classic' marker
    (``cwng_feedback``), the browser has not opted into Classic, and the client
    is an HTML document navigation (not an API/OPDS machine client). Web index only — never
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
    if request.cookies.get(PREFER_CLASSIC_COOKIE) == "1":
        return False
    return _browser_document_html_request()


def spa_login_default_supported():
    """Whether the SPA can authenticate this instance's configured login mode.

    Keep this single carve-out until #1893 gives the SPA API an LDAP bind path.
    Closing that gap reduces the login decision to deleting this predicate call;
    OAuth and reverse-proxy login are supported. This is intentionally
    login-only: authenticated LDAP users can use the SPA catalog normally.
    """
    return config.config_login_type != constants.LOGIN_LDAP


def classic_fallback_requested_from_next(next_url):
    """Whether ``next`` is the exact no-JS Classic fallback emitted by us.

    On login-required instances, the feedback index is intercepted by the auth
    decorator before it can stamp the Classic cookie. Flask-Login then nests
    that fixed feedback URL in ``/login?next=...``. Recognize only the local,
    prefix-scoped marker so the login route can finish the no-JS handoff instead
    of routing back to the SPA and forming a cycle. This never redirects to
    ``next``; it only selects the Classic login response.
    """
    if not next_url:
        return False
    candidate = urlsplit(next_url)
    if candidate.scheme or candidate.netloc or candidate.fragment:
        return False
    expected_path = _mount_prefix() + "/"
    if candidate.path != expected_path:
        return False
    return parse_qsl(candidate.query, keep_blank_values=True) == [
        ("cwng_feedback", "newui")]


def _browser_document_html_request():
    """Return True only for an explicit browser-style HTML document request.

    Werkzeug's ``accept_html`` treats a wildcard as HTML. That was safe while a
    redirect also required an opt-in cookie, but default-SPA routing would turn
    ordinary curl/wget/Kobo ``Accept: */*`` requests into HTML redirects. Require
    an actual ``text/html`` media range with q>0. Fetch Metadata is optional for
    older browsers, but when present it must describe a top-level navigation.
    """
    raw_accept = request.headers.get("Accept", "")
    accepted = parse_accept_header(raw_accept, MIMEAccept)
    if not any(
        mimetype.lower() == "text/html" and quality > 0
        for mimetype, quality in accepted
    ):
        return False

    fetch_dest = request.headers.get("Sec-Fetch-Dest")
    if fetch_dest and fetch_dest.lower() != "document":
        return False
    fetch_mode = request.headers.get("Sec-Fetch-Mode")
    if fetch_mode and fetch_mode.lower() != "navigate":
        return False
    return True


def spa_shell_url():
    """Return the local, prefix-aware URL for the SPA shell.

    ``url_for`` includes ``request.script_root`` verbatim.  That value normally
    comes from a trusted reverse proxy, but a malformed forwarded prefix such as
    ``//evil.example`` would turn a redirect into a scheme-relative off-site
    destination.  Reuse the same strict prefix sanitizer that protects the SPA
    shell's asset and API URLs, then append the fixed app-owned route.
    """
    return f"{_mount_prefix()}/app/"


def spa_shell_choice_url():
    """Prefix-safe URL for the explicit Classic -> SPA navigation control.

    The path comes from :func:`spa_shell_url`, so it shares the strict #571
    mount-prefix sanitizer. The query is fixed server-owned data, never copied
    from a request value.
    """
    return "%s?%s" % (
        spa_shell_url(),
        urlencode({_SPA_CHOICE_PARAM: _SPA_CHOICE_VALUE}),
    )


def _explicit_spa_choice_requested(path):
    """True only for the exact marker on the base SPA shell route.

    Requiring an empty routed path prevents a marker copied onto a content deep
    link from becoming preference-mutating. Requiring the sole query pair keeps
    unrelated application query strings non-mutating too.
    """
    return not path and list(request.args.lists()) == [
        (_SPA_CHOICE_PARAM, [_SPA_CHOICE_VALUE])]


def _inline_script_json(value):
    """JSON for an inline script without an HTML ``</script>`` breakout."""
    return (
        json.dumps(value)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _render_shell(index_path, prefix):
    """Serve the built index.html adapted to the current mount prefix.

    The Vite build hardcodes root-absolute asset URLs (``/static/app/…``); behind
    a reverse-proxy subpath those 404 (the reporter's white page, #571). Rewrite
    them to ``<prefix>/static/app/…`` and expose the prefix to the SPA runtime via
    ``window.__CWNG_PREFIX__`` so its API calls, router base and resource URLs are
    prefixed too. Browsers with JavaScript disabled, or without module-script
    support, are returned to the prefix-aware Classic feedback route rather
    than being stranded on the shell's empty root element."""
    with open(index_path, "r", encoding="utf-8") as fh:
        html = fh.read()
    escaped_prefix = html_escape(prefix, quote=True)
    if prefix:
        html = html.replace(
            "/static/app/", escaped_prefix + "/static/app/")
    # Inject, into <head>:
    #  * the favicon (#574 — the Vite shell ships none, so the new UI had a blank
    #    tab icon); reuse the app's existing /static/favicon.ico, prefix-aware.
    #  * self-healing Classic fallbacks for JavaScript-disabled and pre-module
    #    browsers. Modern module-capable browsers ignore both branches.
    #  * the mount prefix (even "") so the SPA reads an authoritative value rather
    #    than guessing from the URL. Inline-script JSON additionally escapes HTML
    #    delimiters because JSON string quoting alone does not neutralize </script>.
    #  * the running version, so the SPA can name its own build. The classic UI
    #    has always had this via the ``cwng_app_version`` context processor; the
    #    SPA had no way to learn it, which meant the single most useful field in
    #    a bug report was the one field the reporter had to look up by hand.
    #    Deliberately NOT sourced from /api/about: that endpoint gates its
    #    version map behind role_admin() (#1287) because it exposes the kernel
    #    build, the Python build and every dependency version — a CVE
    #    fingerprint. This is only our own release tag, which identifies our
    #    build rather than the user, and is what the classic feedback popup
    #    already shows to every user regardless of role.
    static = prefix + "/static"
    classic_fallback = prefix + "/?cwng_feedback=newui"
    escaped_static = html_escape(static, quote=True)
    escaped_classic_fallback = html_escape(classic_fallback, quote=True)
    inject = (
        '<link rel="icon" href="%s/favicon.ico">'
        '<link rel="apple-touch-icon" sizes="180x180" href="%s/img/apple-touch-icon.png">'
        '<noscript><meta http-equiv="refresh" content="0;url=%s"></noscript>'
        '<script nomodule>window.location.replace(%s);</script>'
        '<script>window.__CWNG_PREFIX__=%s;window.__CWNG_VERSION__=%s;</script>'
    ) % (
        escaped_static,
        escaped_static,
        escaped_classic_fallback,
        _inline_script_json(classic_fallback),
        _inline_script_json(prefix),
        _inline_script_json(constants.INSTALLED_VERSION),
    )
    html = html.replace("</head>", inject + "</head>", 1)
    resp = Response(html, mimetype="text/html")
    # The shell NAMES the content-addressed bundle files, which are served
    # `immutable` for a year (web.add_static_asset_cache_headers) and are deleted
    # by the next Vite build (emptyOutDir). A shell served without cache
    # directives is heuristically cacheable, so a browser could keep asking for
    # an asset filename that no longer exists — a white page after an upgrade.
    # `no-cache` still allows a stored copy, it just requires revalidation, which
    # is exactly the freshness the pointer document needs.
    #
    # Scope, precisely: this keeps a NEWLY LOADED shell from naming deleted
    # bundles. It cannot help a tab that is already running, which may still try
    # to lazy-import a chunk the next build removed; that needs a deploy that
    # retains one previous asset generation, which is a separate change.
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@spa.route("/app")
@spa.route("/app/")
@spa.route("/app/<path:path>")
@reverse_proxy_header_login
def spa_shell(path=""):
    if not _spa_enabled():
        abort(404)
    index_path = os.path.join(_SPA_DIR, "index.html")
    if not os.path.isfile(index_path):
        log.warning("SPA shell requested but build artifact not found: %s — run the Vite build "
                    "or set CWNG_SPA=0 to suppress this warning", index_path)
        abort(404)
    if _explicit_spa_choice_requested(path):
        # Consume the preference-mutating marker once, then land on the clean
        # shell URL. Refreshes, bookmarks, and shared SPA URLs are therefore
        # ordinary non-mutating navigations. The fixed redirect target shares
        # the same sanitized mount-prefix path as the marked URL.
        resp = redirect(spa_shell_url())
        clear_prefer_classic_cookie(resp)
        stamp_prefer_spa_cookie(resp)
        return resp

    resp = _render_shell(index_path, _mount_prefix())
    # Every shell load retains the downgrade-compatible SPA cookie, but does
    # not revoke an explicit Classic choice. Only the marked nav action above
    # has authority to clear that opt-out.
    stamp_prefer_spa_cookie(resp)
    return resp
