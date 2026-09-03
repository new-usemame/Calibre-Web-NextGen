# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import traceback

from flask import redirect, render_template, request
from werkzeug.exceptions import default_exceptions
try:
    from werkzeug.exceptions import FailedDependency
except ImportError:
    from werkzeug.exceptions import UnprocessableEntity as FailedDependency

from . import config, logger, services
from .cw_login import current_user
from .report_link import build_issue_url
from .url_policy import trailing_slash_redirect_url


log = logger.create()

# custom error page

def error_http(error):
    # A 404 with no matched rule came from routing, not from a view calling
    # abort(404). If the only thing wrong with the URL is a trailing slash,
    # send the user to the page they meant instead of an error page.
    if error.code == 404 and request.url_rule is None:
        target = trailing_slash_redirect_url()
        if target:
            # 307, not 308: both preserve the method, but a permanent redirect
            # is cached by the browser indefinitely. This app already has
            # routes where a trailing slash is meaningful (the SPA registers
            # both /app and /app/), so a cached permanent mapping could strand
            # a client on the slash-less form with no server-side remedy.
            return redirect(target, code=307)

    headers = {'WWW-Authenticate': f'Basic realm="{config.config_calibre_web_title or "calibre-web-automated"}"'} if error.code == 401 else {}
    return render_template('http_error.html',
                           error_code="Error {0}".format(error.code),
                           error_name=error.name,
                           issue=False,
                           unconfigured=not config.db_configured,
                           instance=config.config_calibre_web_title
                           ), error.code, headers


def internal_error(error):
    # Always log the full traceback server-side so operators can debug.
    log.error("500 Internal Server Error: %s", traceback.format_exc())
    # Only expose the stacktrace in the rendered page to authenticated admins —
    # traceback.format_exc() can contain internal paths, library versions,
    # function names, and variable values that leak useful info to attackers.
    error_stack = ""
    try:
        if current_user.is_authenticated and current_user.role_admin():
            error_stack = traceback.format_exc().split("\n")
    except Exception:
        pass
    error_name = ('The server encountered an internal error and was unable to complete your '
                  'request. There is an error in the application.')
    # Precomposed report link. This page asks the user to "report this issue
    # with all related information" while already holding the error, and until
    # now handed them a blank form and asked them to type the version by hand.
    # Composed, never sent: only the user clicking through posts anything.
    try:
        issue_url = build_issue_url("500 Internal Server Error", error_name)
    except Exception:
        # An error page that errors is the worst possible outcome here, so the
        # link is strictly best-effort — the template falls back to the plain
        # new-issue URL when this is empty.
        issue_url = ""
    return render_template('http_error.html',
                           error_code="500 Internal Server Error",
                           error_name=error_name,
                           issue=True,
                           issue_url=issue_url,
                           unconfigured=False,
                           error_stack=error_stack,
                           instance=config.config_calibre_web_title
                           ), 500


def init_errorhandler(application=None):
    if application is None:
        # Compatibility for the pinned oracle and pre-factory callers.
        from . import app as application

    # http error handling
    for ex in default_exceptions:
        if ex < 500:
            application.register_error_handler(ex, error_http)
        elif ex == 500:
            application.register_error_handler(ex, internal_error)

    if services.ldap:
        # Only way of catching the LDAPException upon logging in with LDAP server down
        @application.errorhandler(services.ldap.LDAPException)
        # pylint: disable=unused-variable
        def handle_exception(e):
            log.debug('LDAP server not accessible while trying to login to opds feed')
            return error_http(FailedDependency())
