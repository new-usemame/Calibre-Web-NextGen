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

from . import config, app, logger, services
from .cw_login import current_user
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
            return redirect(target, code=308)

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
    return render_template('http_error.html',
                           error_code="500 Internal Server Error",
                           error_name='The server encountered an internal error and was unable to complete your '
                                      'request. There is an error in the application.',
                           issue=True,
                           unconfigured=False,
                           error_stack=error_stack,
                           instance=config.config_calibre_web_title
                           ), 500


def init_errorhandler():
    # http error handling
    for ex in default_exceptions:
        if ex < 500:
            app.register_error_handler(ex, error_http)
        elif ex == 500:
            app.register_error_handler(ex, internal_error)

    if services.ldap:
        # Only way of catching the LDAPException upon logging in with LDAP server down
        @app.errorhandler(services.ldap.LDAPException)
        # pylint: disable=unused-variable
        def handle_exception(e):
            log.debug('LDAP server not accessible while trying to login to opds feed')
            return error_http(FailedDependency())

