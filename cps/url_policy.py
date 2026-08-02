# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Trailing-slash policy for hand-typed URLs.

Werkzeug treats ``/kosync`` and ``/kosync/`` as two different resources.
A rule declared *with* a trailing slash redirects when you ask for it
without one, but a rule declared *without* one — which is nearly every
route in this app — simply 404s when you ask for it with one. Users type
and bookmark URLs with trailing slashes, so pages that work when reached
through a link 404 when reached from the address bar (fork #628: KOReader
setup at ``/kosync/`` 404s, ``/kosync`` works).

Rather than hand-annotating hundreds of routes — the app already carries
one ad-hoc patch of this shape, ``/app`` plus ``/app/`` in the SPA
blueprint — the policy lives in one place and is applied when routing has
already failed. Nothing that resolves today changes behaviour; the only
requests affected are ones that currently return 404.
"""

from flask import current_app, request

__all__ = ["canonical_slashless_path", "trailing_slash_redirect_url"]


def canonical_slashless_path(url_adapter, path, method):
    """Return ``path`` without its trailing slash(es) when that is a real
    route, else ``None``.

    ``url_adapter`` is a bound :class:`werkzeug.routing.Map`. ``path`` is
    the script-root-relative path that failed to match. Only an exact
    match counts: a path that resolves solely for another HTTP method, or
    that would itself redirect, is left alone so the caller keeps the
    original error rather than papering over a different problem.
    """
    if not path or not path.endswith("/"):
        return None

    stripped = "/" + path.strip("/")
    if stripped == path:
        # ``path`` was "/" (or only slashes) — there is nothing to strip.
        return None

    if not _is_same_origin_path(stripped):
        return None

    try:
        url_adapter.match(stripped, method=method)
    except Exception:
        # NotFound, MethodNotAllowed, RequestRedirect — in every case the
        # slash was not the reason this request failed.
        return None

    return stripped


def _is_same_origin_path(path):
    """Reject anything a browser could read as an absolute or protocol-relative
    URL rather than a path on this host.

    Only a real route can reach the redirect, so this is belt and braces — but
    the value originates in the request line, and ``//evil.example`` or the
    backslash variant that some browsers fold into it would turn a convenience
    redirect into an open redirect.
    """
    if not path.startswith("/"):
        return False
    if path.startswith("//") or path.startswith("/\\"):
        return False
    return "\\" not in path and "\r" not in path and "\n" not in path


def trailing_slash_redirect_url():
    """Return the absolute path to redirect the current request to when it
    only 404'd because of a trailing slash, else ``None``.

    The script root is preserved so this stays correct behind a reverse
    proxy that mounts the app on a sub-path.
    """
    adapter = current_app.url_map.bind_to_environ(request.environ)
    stripped = canonical_slashless_path(adapter, request.path, request.method)
    if stripped is None:
        return None

    target = request.script_root + stripped
    if request.query_string:
        target += "?" + request.query_string.decode("latin-1")
    return target
