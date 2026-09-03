# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import os
import sys

from . import create_app, limiter
from .jinjia import jinjia
from flask import request, g


def request_username():
    return request.authorization.username


def hide_console_windows():
    """Hide the console window on Windows. No-op everywhere else.

    Call this from a script entry point, never from main(). main() is also the
    `cps` console script (pyproject [project.scripts]), and a Windows user who
    types `cps` in a terminal wants that terminal: hiding it takes their server
    output and their Ctrl-C with it while the process keeps running.
    """
    if os.name != "nt":
        return

    import ctypes

    kernel32 = ctypes.WinDLL('kernel32')
    user32 = ctypes.WinDLL('user32')

    SW_HIDE = 0

    hWnd = kernel32.GetConsoleWindow()
    if hWnd:
        user32.ShowWindow(hWnd, SW_HIDE)


def register_blueprints(app):
    """Register the production blueprint set once, in its historical order."""
    marker = "cps_production_blueprints_registered"
    if app.extensions.get(marker):
        raise RuntimeError("production blueprints are already registered on this app")
    from .cwa_functions import switch_theme, library_refresh, convert_library, epub_fixer, cover_enforcer_ui, cwa_stats, cwa_check_status, cwa_settings, cwa_logs, profile_pictures, cwa_internal
    from .web import register_app_hooks, web
    from .opds import opds
    from .admin import admi
    from .gdrive import gdrive
    from .editbooks import editbook
    from .cover_picker import cover_picker
    from .cover_preview_blueprint import cover_preview_bp
    from .annotations import annotations_bp
    from .about import about
    from .search import search
    from .search_metadata import meta
    from .shelf import shelf
    from .tasks_status import tasks
    from .error_handler import init_errorhandler
    from .remotelogin import remotelogin
    from .progress_syncing.protocols.kosync import kosync
    from .duplicates import duplicates
    from .api import api_v1
    from .spa import spa
    try:
        from .kobo import kobo, get_kobo_activated
        from .kobo_auth import kobo_auth
        from .readingservices import readingservices_api_v3, readingservices_userstorage
        from flask_limiter.util import get_remote_address
        kobo_available = get_kobo_activated()
    except (ImportError, AttributeError):  # Catch also error for not installed flask-WTF (missing csrf decorator)
        kobo_available = False
        kobo = kobo_auth = get_remote_address = None

    try:
        from .oauth_bb import oauth
        oauth_available = True
    except ImportError:
        oauth_available = False
        oauth = None

    register_app_hooks(app)
    init_errorhandler(app)

    # CWA Blueprints
    app.register_blueprint(switch_theme)
    app.register_blueprint(library_refresh)
    app.register_blueprint(convert_library)
    app.register_blueprint(epub_fixer)
    app.register_blueprint(cover_enforcer_ui)
    app.register_blueprint(cwa_stats)
    app.register_blueprint(cwa_check_status)
    app.register_blueprint(cwa_settings)
    app.register_blueprint(cwa_logs)
    app.register_blueprint(profile_pictures)
    app.register_blueprint(cwa_internal)

    # Stock CW
    app.register_blueprint(search)
    app.register_blueprint(tasks)
    app.register_blueprint(web)
    app.register_blueprint(opds)
    if not getattr(opds, "_cps_rate_limit_registered", False):
        limiter.limit("3/minute", key_func=request_username)(opds)
        opds._cps_rate_limit_registered = True
    app.register_blueprint(jinjia)
    app.register_blueprint(about)
    app.register_blueprint(shelf)
    app.register_blueprint(admi)
    app.register_blueprint(remotelogin)
    app.register_blueprint(meta)
    app.register_blueprint(gdrive)
    app.register_blueprint(editbook)
    app.register_blueprint(cover_picker)
    app.register_blueprint(cover_preview_bp)
    app.register_blueprint(annotations_bp)
    app.register_blueprint(kosync)
    app.register_blueprint(duplicates)
    app.register_blueprint(api_v1)
    app.register_blueprint(spa)
    if kobo_available:
        app.register_blueprint(kobo)
        app.register_blueprint(kobo_auth)
        if not getattr(kobo, "_cps_rate_limit_registered", False):
            limiter.limit("3/minute", key_func=get_remote_address)(kobo)
            kobo._cps_rate_limit_registered = True
        app.register_blueprint(readingservices_api_v3)
        app.register_blueprint(readingservices_userstorage)
        from .services import kobo_patch_spool
        kobo_patch_spool.start_retention_maintenance()
    if oauth_available:
        app.register_blueprint(oauth)

    # kobo_auth historically decorates the compatibility login manager while
    # it is imported. Factory apps own their manager, so copy the completed
    # per-blueprint policy without sharing the mutable mapping.
    if app.login_manager is not None and app.login_manager is not getattr(
        sys.modules.get("cps"), "lm", None
    ):
        from . import lm
        app.login_manager.blueprint_login_views.update(lm.blueprint_login_views)

    app.extensions["cps_kobo_available"] = kobo_available
    app.extensions[marker] = True
    return app


def _start_runtime_tasks(app):
    # Annotation sync-target pushes are blocking HTTPS calls; on the request
    # greenlet they freeze the whole (unpatched-gevent) app, so hand them to
    # the WorkerThread instead (#920).
    from .services import annotation_sync
    annotation_sync.enable_background_dispatch()

    # Upgrades receive the default-on preference through the settings-table
    # migration without an admin save, so give that path its one-time trigger.
    # This is a convenience job: failure must never prevent HTTP startup.
    try:
        from .tasks.kepub_backfill import enqueue_startup_kepub_backfill
        enqueue_startup_kepub_backfill()
    except Exception as ex:
        from . import logger
        logger.create().error_or_exception(f"Could not queue startup KEPUB backfill: {ex}")

    try:
        from .tasks.kepub_package_repair import enqueue_startup_kepub_package_repair
        enqueue_startup_kepub_package_repair()
    except Exception as ex:
        from . import logger
        logger.create().error_or_exception(f"Could not queue startup KEPUB package repair: {ex}")


def main():
    app = create_app()
    register_blueprints(app)
    _start_runtime_tasks(app)

    from . import web_server

    success = web_server.start()
    sys.exit(0 if success else 1)
