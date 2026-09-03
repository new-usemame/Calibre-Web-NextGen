# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import sys
import os
import mimetypes
import threading
from functools import wraps

from flask import Flask, current_app, g, has_app_context, session
from .MyLoginManager import MyLoginManager
from flask_principal import Principal
from werkzeug.middleware.proxy_fix import ProxyFix

from . import logger
from . import constants
from .cli import CliParameter
from .reverseproxy import ReverseProxied
from .server import WebServer
from .updater import Updater
from . import config_sql
from . import cache_buster
from . import ub, db, magic_shelf

try:
    from flask_limiter import Limiter
    limiter_present = True
except ImportError:
    limiter_present = False
try:
    from flask_wtf.csrf import CSRFProtect
    wtf_present = True
except ImportError:
    wtf_present = False


mimetypes.init()
mimetypes.add_type('application/xhtml+xml', '.xhtml')
mimetypes.add_type('application/epub+zip', '.epub')
mimetypes.add_type('application/epub+zip', '.kepub')
mimetypes.add_type('text/xml', '.fb2')
mimetypes.add_type('application/x-mobipocket-ebook', '.mobi')
mimetypes.add_type('application/x-mobipocket-ebook', '.prc')
mimetypes.add_type('application/vnd.amazon.ebook', '.azw')
mimetypes.add_type('application/x-mobi8-ebook', '.azw3')
mimetypes.add_type('application/vnd.comicbook-rar', '.cbr')
mimetypes.add_type('application/vnd.comicbook+zip', '.cbz')
mimetypes.add_type('application/x-cbt', '.cbt')
mimetypes.add_type('application/x-7z-compressed', '.cb7')
mimetypes.add_type('image/vnd.djv', '.djv')
mimetypes.add_type('image/vnd.djv', '.djvu')
mimetypes.add_type('application/mpeg', '.mpeg')
mimetypes.add_type('audio/mpeg', '.mp3')
mimetypes.add_type('audio/x-m4a', '.m4a')
mimetypes.add_type('audio/x-m4a', '.m4b')
mimetypes.add_type('audio/x-hx-aac-adts', '.aac')
mimetypes.add_type('audio/vnd.dolby.dd-raw', '.ac3')
mimetypes.add_type('video/x-ms-asf', '.asf')
mimetypes.add_type('audio/ogg', '.ogg')
mimetypes.add_type('application/ogg', '.oga')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/x-ms-reader', '.lit')
mimetypes.add_type('text/javascript; charset=UTF-8', '.js')
mimetypes.add_type('application/vnd.adobe.adept+xml', '.acsm')
mimetypes.add_type('application/vnd.readium.lcp.license.v1.0+json', '.lcpl')
mimetypes.add_type('application/vnd.amazon.ebook', '.kfx')
mimetypes.add_type('application/zip', '.kfx-zip')

log = logger.create()


def protect_user_specific_catalog_responses(response):
    """Prevent a shared cache from crossing account-specific catalog views."""
    if not getattr(g, "_common_filters_user_specific", False):
        return response
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    response.vary.add("Authorization")
    runtime_config = current_app.extensions.get("cps_config", config)
    if getattr(runtime_config, "config_allow_reverse_proxy_header_login", False):
        header_name = getattr(runtime_config, "config_reverse_proxy_login_header_name", "")
        if header_name:
            response.vary.add(header_name)
    return response


_BASE_HOOK_MARKER = "cps_base_after_request_registered"
_PROXY_FIX_MARKER = "cps_proxy_fix_registered"


def _configure_base_app(application, runtime_config=None):
    """Install import-time Flask defaults exactly once on one app object."""
    application.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true',
        SESSION_COOKIE_SAMESITE='Lax',
        REMEMBER_COOKIE_SAMESITE='Strict',
        WTF_CSRF_SSL_STRICT=False,
        SESSION_COOKIE_NAME=os.environ.get('COOKIE_PREFIX', "") + "session",
        REMEMBER_COOKIE_NAME=os.environ.get('COOKIE_PREFIX', "") + "remember_token"
    )
    if runtime_config is not None:
        application.extensions["cps_config"] = runtime_config

    if not application.extensions.get(_BASE_HOOK_MARKER):
        application.after_request(protect_user_specific_catalog_responses)
        application.extensions[_BASE_HOOK_MARKER] = True

    if application.extensions.get(_PROXY_FIX_MARKER):
        return application

    # Fix for running behind reverse proxy (e.g. nginx, apache, caddy, ...)
    # Without it, url_for will generate http:// urls even if https:// is used.
    # Preserve the existing defaults exactly; PROXY-01 changes them in P2.02.
    application.wsgi_app = ProxyFix(application.wsgi_app, **proxyfix_hops)
    application.extensions[_PROXY_FIX_MARKER] = True
    if len(set(proxyfix_hops.values())) == 1:
        log.info(f'ProxyFix configured to trust {num_proxies} proxy(ies) for X-Forwarded-* headers')
    else:
        log.info(
            'ProxyFix configured with trusted proxy hops: '
            f'x_for={proxyfix_hops["x_for"]}, x_proto={proxyfix_hops["x_proto"]}, '
            f'x_host={proxyfix_hops["x_host"]}, x_prefix={proxyfix_hops["x_prefix"]}'
        )
    return application


# These values intentionally remain import-time environment reads. Moving the
# read to saved configuration would alter PROXY-01 rather than expose a seam.
num_proxies = int(os.environ.get('TRUSTED_PROXY_COUNT', '1'))
proxyfix_hops = {
    'x_for': int(os.environ.get('PROXYFIX_X_FOR', num_proxies)),
    'x_proto': int(os.environ.get('PROXYFIX_X_PROTO', num_proxies)),
    'x_host': int(os.environ.get('PROXYFIX_X_HOST', num_proxies)),
    'x_prefix': num_proxies,
}

# Compatibility singleton: imports of ``cps.app`` keep the same hook and
# middleware they had before the factory seam. Explicit factory callers use
# the object returned by create_app(config, services).
app = _configure_base_app(Flask(__name__))

lm = MyLoginManager()

cli_param = CliParameter()

config = config_sql.ConfigSQL()
app.extensions["cps_config"] = config

if wtf_present:
    csrf = CSRFProtect()
else:
    csrf = None

calibre_db = db.CalibreDB()

web_server = WebServer()

updater_thread = Updater()

if limiter_present:
    limiter = Limiter(key_func=True, headers_enabled=True, auto_check=False, swallow_errors=False)
else:
    limiter = None


class _ProcessRuntimeState:
    """State owned by this Python process, never by an injected service bundle."""

    def __init__(self):
        self.initialized = False
        self.config_fingerprint = None
        self.goodreads_support = None
        self.limiter_initialized = False
        self.limiter_registered = False
        self.limiter_before_hooks = []
        self.limiter_after_hooks = []
        self.limiter_teardown_hooks = []
        self.limiter_used_fallback = False


_process_runtime_state = _ProcessRuntimeState()
_process_runtime_lock = threading.RLock()
_FACTORY_COMPLETE_MARKER = "cps_factory_complete"


def _frozen_process_value(value):
    if isinstance(value, dict):
        return tuple(sorted((key, _frozen_process_value(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_frozen_process_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted(_frozen_process_value(item) for item in value))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _process_config_fingerprint(runtime_config):
    """Return only settings consumed by process-scoped runtime objects."""
    values = []
    for name in (
        "config_ratelimiter",
        "config_limiter_uri",
        "config_limiter_options",
        "config_updatechannel",
        "config_access_log",
        "config_access_logfile",
        "schedule_reconnect",
        "config_use_goodreads",
        "config_goodreads_api_key",
    ):
        values.append((name, _frozen_process_value(getattr(runtime_config, name, None))))
    for name in ("get_config_ipaddress", "get_config_certfile", "get_config_keyfile"):
        getter = getattr(runtime_config, name, None)
        try:
            value = getter() if callable(getter) else None
        except AttributeError:
            # Minimal factory test configs may deliberately omit server-only
            # fields; two such omissions are compatible with one another.
            value = None
        values.append((name, _frozen_process_value(value)))
    values.append(("memory_backend", bool(cli_param.memory_backend)))
    return tuple(values)


def _assert_process_runtime_compatible(runtime_config, runtime_services):
    state = _process_runtime_state
    candidate = _process_config_fingerprint(runtime_config)
    changed = [
        name
        for (name, existing), (_, requested) in zip(state.config_fingerprint, candidate)
        if existing != requested
    ]
    candidate_goodreads = getattr(runtime_services, "goodreads_support", None)
    if state.goodreads_support is not candidate_goodreads:
        changed.append("goodreads_support")
    if changed:
        raise RuntimeError(
            "create_app() cannot reconfigure process-scoped runtime settings: "
            + ", ".join(changed)
        )


def _new_app_login_manager(runtime_config, compatibility_app):
    manager = lm if compatibility_app else MyLoginManager()
    manager.login_view = 'web.login'
    manager.anonymous_user = ub.Anonymous
    manager.session_protection = 'strong' if runtime_config.config_session == 1 else "basic"
    if manager is not lm:
        # Blueprint modules still register the historical loader on ``cps.lm``.
        # Resolve it at request time so imports after construction remain valid,
        # while keeping all mutable policy on this app's own manager.
        def load_user(*args):
            callback = lm.user_callback
            if callback is None:
                raise RuntimeError("The compatibility login manager has no user loader")
            return callback(*args)

        manager.user_loader(load_user)
    return manager


def _configure_process_limiter(application, runtime_config, first_process_initialization):
    """Initialize the decorated global limiter once, then attach it without mutation."""
    state = _process_runtime_state
    config = runtime_config
    application.config.update(RATELIMIT_ENABLED=runtime_config.config_ratelimiter)
    if runtime_config.config_limiter_uri != "" and not cli_param.memory_backend:
        application.config.update(RATELIMIT_STORAGE_URI=config.config_limiter_uri)
        if runtime_config.config_limiter_options != "":
            application.config.update(RATELIMIT_STORAGE_OPTIONS=config.config_limiter_options)
    else:
        # Flask-Limiter warns when storage is implicit. This process is single-
        # process, so one explicit in-memory backend is shared by every app.
        application.config.update(RATELIMIT_STORAGE_URI="memory://")

    if not first_process_initialization:
        if state.limiter_used_fallback:
            application.config.update(RATELIMIT_STORAGE_URI="memory://")
        if state.limiter_registered:
            application.extensions.setdefault("limiter", set()).add(limiter)
        for hook in state.limiter_before_hooks:
            application.before_request(hook)
        for hook in state.limiter_after_hooks:
            application.after_request(hook)
        for hook in state.limiter_teardown_hooks:
            application.teardown_request(hook)
        return

    before_count = len(application.before_request_funcs.get(None, []))
    after_count = len(application.after_request_funcs.get(None, []))
    teardown_count = len(application.teardown_request_funcs.get(None, []))
    try:
        limiter.init_app(application)
    except Exception as e:
        log.error('Wrong Flask Limiter configuration, falling back to default: {}'.format(e))
        application.config.update(RATELIMIT_STORAGE_URI="memory://")
        limiter.init_app(application)
        state.limiter_used_fallback = True
    state.limiter_before_hooks = application.before_request_funcs.get(None, [])[before_count:]
    state.limiter_after_hooks = application.after_request_funcs.get(None, [])[after_count:]
    state.limiter_teardown_hooks = application.teardown_request_funcs.get(None, [])[teardown_count:]
    state.limiter_registered = limiter in application.extensions.get("limiter", set())
    state.limiter_initialized = True


def apply_https_runtime_config(application=None, runtime_config=None):
    """Refresh cookie security flags from the current saved config."""
    application = application or (current_app._get_current_object() if has_app_context() else app)
    runtime_config = runtime_config or application.extensions.get("cps_config", config)
    if (runtime_config.config_login_type == constants.LOGIN_OAUTH
            or getattr(runtime_config, 'config_use_https', False)):
        application.config['SESSION_COOKIE_SECURE'] = True
        application.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
        log.info("Enforcing SESSION_COOKIE_SECURE=True (OAuth enabled or HTTPS enforced)")
    else:
        application.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
        log.info(f"SESSION_COOKIE_SECURE set to {application.config['SESSION_COOKIE_SECURE']} (Standard/LDAP login)")


# Last-logged magic-shelf filter snapshot per user_id; the before_request
# hook fires on every request (incl. UI polling), so the DEBUG line is
# deduped to once per change (cf. _AUTHOR_SORT_DRIFT_WARNED).
_MAGIC_SHELF_COUNTS_LOGGED = {}

# (user_id, shelf_id) pairs already warned about an orphaned system shelf,
# so that WARNING fires once per user+shelf instead of on every request.
_ORPHANED_SYSTEM_SHELF_WARNED = set()


def _ensure_user_profiles_json():
    """Create the classic profile-picture map without making startup depend on it."""
    json_path = constants.USER_PROFILES_JSON
    if os.path.exists(json_path):
        return
    try:
        with open(json_path, 'w+') as f:
            f.write('{\n}')
    except OSError as e:
        log.warning("Could not create user profiles file %s: %s", json_path, e)


def _log_magic_shelf_counts(user_id, total_shelves, visible_shelves,
                            hidden_templates=(), hidden_shelves=()):
    snapshot = (total_shelves, visible_shelves,
                tuple(hidden_templates), tuple(hidden_shelves))
    if _MAGIC_SHELF_COUNTS_LOGGED.get(user_id) != snapshot:
        _MAGIC_SHELF_COUNTS_LOGGED[user_id] = snapshot
        msg = (f"Found {total_shelves} total magic shelves for user {user_id}, "
               f"{visible_shelves} visible after filtering")
        if hidden_templates:
            msg += "; hidden system templates: " + ", ".join(hidden_templates)
        if hidden_shelves:
            msg += "; hidden public shelves: " + ", ".join(hidden_shelves)
        log.debug(msg)


def _serialized_factory(factory):
    @wraps(factory)
    def locked(*args, **kwargs):
        with _process_runtime_lock:
            return factory(*args, **kwargs)

    return locked


@_serialized_factory
def create_app(config=None, services=None):
    """Build an app without reconfiguring an app that is already live.

    The no-argument form initializes and returns the stable module-level
    compatibility app. Explicit callers receive a fresh Flask object. Their
    ``config`` controls app-local cookie, login-session, request-hook and LDAP
    setup; the first call also selects the process-scoped server, updater,
    scheduler, Goodreads and limiter settings. Later calls must match those
    process settings and otherwise fail before changing them.

    This seam does not yet make injection authoritative throughout the legacy
    package: Kobo availability, web security headers, OAuth generation, error
    handlers and runtime-task modules still read ``cps.config``/``cps.services``.
    That module-by-module cutover belongs to P0.0b.
    """
    if (config is None) != (services is None):
        raise TypeError("create_app() requires both config and services, or neither")

    runtime_config = config if config is not None else globals()["config"]
    if services is None:
        from . import services as runtime_services
    else:
        runtime_services = services

    compatibility_app = config is None
    if compatibility_app:
        application = globals()["app"]
        _configure_base_app(application, runtime_config)
        if application.extensions.get(_FACTORY_COMPLETE_MARKER):
            return application
    else:
        application = _configure_base_app(Flask(__name__), runtime_config)

    state = _process_runtime_state
    first_process_initialization = not state.initialized
    if not first_process_initialization:
        _assert_process_runtime_compatible(runtime_config, runtime_services)

    if csrf:
        csrf.init_app(application)

    error = None
    if first_process_initialization:
        cli_param.init()

        ub.init_db(cli_param.settings_path)
        # pylint: disable=no-member
        encrypt_key, error = config_sql.get_encryption_key(os.path.dirname(cli_param.settings_path))

        config_sql.load_configuration(ub.session, encrypt_key)
        runtime_config.init_config(ub.session, encrypt_key, cli_param)
        state.config_fingerprint = _process_config_fingerprint(runtime_config)
        state.goodreads_support = getattr(runtime_services, "goodreads_support", None)

    # Intelligent Security Configuration
    # Force SESSION_COOKIE_SECURE if OAuth is enabled OR if "Use via HTTPS" is checked.
    if config is None:
        apply_https_runtime_config()
    else:
        apply_https_runtime_config(application, runtime_config)

    # Set OAuth redirect host consistency
    if (hasattr(runtime_config, 'config_oauth_redirect_host')
            and runtime_config.config_oauth_redirect_host):
        from urllib.parse import urlparse
        parsed = urlparse(runtime_config.config_oauth_redirect_host)
        if parsed.netloc:
            application.config['FORCE_HOST_FOR_REDIRECTS'] = parsed.netloc

    if error:
        log.error(error)

    if first_process_initialization:
        ub.password_change(cli_param.user_credentials)

    if sys.version_info < (3, 0):
        log.info(
            '*** Python2 is EOL since end of 2019, this version of Calibre-Web is no longer supporting Python2, '
            'please update your installation to Python3 ***')
        print(
            '*** Python2 is EOL since end of 2019, this version of Calibre-Web is no longer supporting Python2, '
            'please update your installation to Python3 ***')
        web_server.stop(True)
        sys.exit(5)

    app_login_manager = _new_app_login_manager(runtime_config, compatibility_app)

    if first_process_initialization:
        _ensure_user_profiles_json()

        from .calibre_init import init_calibre_db_from_config
        init_calibre_db_from_config(runtime_config, cli_param.settings_path)
        calibre_db.init_db()
        # A process can die after staging or after committing cover metadata but
        # before publication. The stage alone cannot tell us which occurred, so
        # startup logs and removes it rather than guessing at publication.
        try:
            from . import helper
            helper.scavenge_staged_cover_files()
        except Exception as ex:
            log.error("Cover stage startup scavenging failed: %s", ex)
        # The annotation content-id backfill needs both databases: app.db owns the
        # annotation, while metadata.db is authoritative for book UUID.
        ub.backfill_annotation_content_ids(
            ub.session.bind,
            lambda book_id: getattr(calibre_db.get_book(book_id), "uuid", None),
        )

        updater_thread.init_updater(runtime_config, web_server)
    # Perform dry run of updater and exit afterward
    if cli_param.dry_run:
        updater_thread.dry_run()
        sys.exit(0)
    if first_process_initialization:
        updater_thread.start()
    if not application.extensions.get("cps_reverse_proxy_registered"):
        application.wsgi_app = ReverseProxied(application.wsgi_app)
        application.extensions["cps_reverse_proxy_registered"] = True

    if os.environ.get('FLASK_DEBUG'):
        cache_buster.init_cache_busting(application)
    log.info('Starting Calibre Web...')
    Principal(application)
    app_login_manager.init_app(application)
    application.secret_key = os.getenv('SECRET_KEY', config_sql.get_flask_session_key(ub.session))

    if first_process_initialization:
        web_server.init_app(application, runtime_config)
    from .cw_babel import babel as compatibility_babel, get_locale
    if compatibility_app:
        app_babel = compatibility_babel
    else:
        app_babel = type(compatibility_babel)()
    if hasattr(app_babel, "localeselector"):
        app_babel.init_app(application)
        app_babel.localeselector(get_locale)
    else:
        app_babel.init_app(application, locale_selector=get_locale)

    # Initialize OAuth blueprints AFTER babel to ensure translations are loaded
    # Issue: OAuth blueprint generation was happening during module import (before babel init),
    # causing babel.list_translations() to return empty list and hiding language options
    if ub.oauth_support:
        try:
            from . import oauth_bb
            oauth_bb.init_oauth_blueprints(application)
            log.info("OAuth blueprints initialized successfully")
        except Exception as e:
            log.error("Failed to initialize OAuth blueprints: %s", e)

    if runtime_services.ldap:
        runtime_services.ldap.init_app(application, runtime_config)
    if first_process_initialization and runtime_services.goodreads_support:
        runtime_services.goodreads_support.connect(runtime_config.config_goodreads_api_key,
                                                   runtime_config.config_use_goodreads)
    if first_process_initialization:
        runtime_config.store_calibre_uuid(calibre_db, db.Library_Id)
    _configure_process_limiter(application, runtime_config, first_process_initialization)

    # Register scheduled tasks
    # Ensure a valid calibre_db session exists before handling each request
    @application.before_request
    def _cwa_ensure_db_session():
        from flask import g, request
        from .cw_login import current_user
        from sqlalchemy import or_
        import time

        if runtime_config.config_allow_reverse_proxy_header_login:
            """
            Load user from reverse proxy authentication header if configured.
            Sets g.flask_httpauth_user early so that current_user proxy resolves correctly
            for user-specific settings like theme preferences.

            This must run before any blueprint before_request handlers that access current_user.
            """

            from . import usermanagement
            user = usermanagement.load_user_from_reverse_proxy_header(request)
            if user:
                g.flask_httpauth_user = user
            else:
                # Explicitly set to None to indicate we checked but found nothing
                g.flask_httpauth_user = None

        if current_user.is_authenticated:
            try:
                # Verify required tables exist before querying
                from sqlalchemy import inspect
                inspector = inspect(ub.session.bind)
                required_tables = ['magic_shelf', 'hidden_magic_shelf_templates']
                existing_tables = inspector.get_table_names()
                
                missing_tables = [t for t in required_tables if t not in existing_tables]
                if missing_tables:
                    log.error(f"Magic shelf tables missing from database: {missing_tables}. Run migration to create them.")
                    g.magic_shelves_access = []
                    return
                
                # Get hidden items for this user (both system templates and custom shelves)
                hidden_items = ub.session.query(
                    ub.HiddenMagicShelfTemplate.template_key,
                    ub.HiddenMagicShelfTemplate.shelf_id
                ).filter(
                    ub.HiddenMagicShelfTemplate.user_id == current_user.id
                ).all()
                
                hidden_template_keys = {item.template_key for item in hidden_items if item.template_key}
                hidden_shelf_ids = {item.shelf_id for item in hidden_items if item.shelf_id}
                
                # Get user's own shelves + public shelves (will filter hidden ones below)
                g.magic_shelves_access = ub.session.query(ub.MagicShelf).filter(
                    or_(
                        ub.MagicShelf.is_public == 1,
                        ub.MagicShelf.user_id == current_user.id
                    )
                ).all()

                total_shelves = len(g.magic_shelves_access)

                # Filter out hidden items
                from . import magic_shelf
                filtered_shelves = []
                hidden_template_hits = []
                hidden_public_hits = []
                for shelf in g.magic_shelves_access:
                    # Skip hidden system templates
                    if shelf.is_system and shelf.user_id == current_user.id:
                        # Find template key for this system shelf
                        template_key = None
                        for key, template in magic_shelf.SYSTEM_SHELF_TEMPLATES.items():
                            if template['name'] == shelf.name:
                                template_key = key
                                break

                        # If template_key not found, this is an orphaned/deprecated system shelf
                        if template_key is None:
                            if (current_user.id, shelf.id) not in _ORPHANED_SYSTEM_SHELF_WARNED:
                                _ORPHANED_SYSTEM_SHELF_WARNED.add((current_user.id, shelf.id))
                                log.warning(f"System shelf '{shelf.name}' (ID: {shelf.id}) doesn't match any current template - may need migration")
                            # Show it anyway - migration should clean it up on next restart
                            filtered_shelves.append(shelf)
                            continue

                        # Skip if hidden
                        if template_key in hidden_template_keys:
                            hidden_template_hits.append(template_key)
                            continue

                    # Skip hidden custom public shelves (not owned by user)
                    if shelf.is_public == 1 and shelf.user_id != current_user.id:
                        if shelf.id in hidden_shelf_ids:
                            hidden_public_hits.append(f"'{shelf.name}' (ID: {shelf.id})")
                            continue

                    filtered_shelves.append(shelf)

                g.magic_shelves_access = filtered_shelves

                # Deduplicated — this hook fires on every request
                _log_magic_shelf_counts(current_user.id, total_shelves, len(filtered_shelves),
                                        hidden_template_hits, hidden_public_hits)

                # Magic Shelf Count Caching
                if 'magic_shelf_counts' not in session:
                    session['magic_shelf_counts'] = {}
                
                counts = session['magic_shelf_counts']
                cache_updated = False
                now = time.time()
                CACHE_DURATION = 300  # 5 minutes
                
                for shelf in g.magic_shelves_access:
                    shelf_id_str = str(shelf.id)
                    cached_data = counts.get(shelf_id_str)
                    
                    if cached_data and (now - cached_data.get('timestamp', 0) < CACHE_DURATION):
                        shelf.book_count = cached_data['count']
                    else:
                        count = magic_shelf.get_book_count_for_magic_shelf(shelf.id)
                        counts[shelf_id_str] = {'count': count, 'timestamp': now}
                        shelf.book_count = count
                        cache_updated = True
                
                if cache_updated:
                    session.modified = True

                try:
                    magic_shelf.sort_magic_shelves_for_user(g.magic_shelves_access, current_user)
                except Exception as e:
                    log.warning(f"Failed to sort magic shelves for user {current_user.id}: {e}")
            except Exception as e:
                log.error(f"Error populating magic shelves for user {current_user.id}: {str(e)}", exc_info=True)
                g.magic_shelves_access = []
        else:
            g.magic_shelves_access = []
        try:
            calibre_db.ensure_session()
        except Exception:
            # Failsafe: let route-level code handle specific DB errors
            pass

    @application.before_request
    def _clear_pending_app_password():
        """Drop a just-created app-password cleartext from session when
        the user navigates away from the profile page. Fork issue #223:
        the cleartext shows inline on /me and survives reloads of /me,
        but disappears as soon as the user clicks anything else.

        Scoped to **top-level HTML navigations** via the ``Sec-Fetch-Dest``
        header (browsers send ``document`` for address-bar navigations
        and link clicks; ``image``/``style``/``script``/``empty`` for
        sub-resources). XHR + fetch requests count as sub-resources and
        do not clear the session — only an actual page change does.
        """
        from flask import session, request
        if "pending_app_password" not in session:
            return
        # Modern browsers send Sec-Fetch-Dest on every request. Treat
        # missing header as "unknown — be conservative, don't clear"
        # so curl / older clients can't accidentally pop the cleartext.
        dest = request.headers.get("Sec-Fetch-Dest", "")
        if dest and dest != "document":
            return
        if not dest:
            # Fallback for clients without the header: only clear on
            # text/html GETs that aren't static-file fetches.
            if request.endpoint == "static" or request.method != "GET":
                return
            if not request.accept_mimetypes.accept_html:
                return
        # On a real top-level navigation, only the profile-section
        # endpoints keep the cleartext alive.
        keep_endpoints = {
            "web.profile",
            "web.app_password_create",
            "web.app_password_revoke",
        }
        if request.endpoint not in keep_endpoints:
            session.pop("pending_app_password", None)

    @application.before_request
    def _desktop_compat_fresh_snapshot():
        from flask import request
        # Rollback ends the SERIALIZABLE snapshot so the next query sees Calibre desktop's writes.
        if not calibre_db._desktop_compat or request.endpoint == 'static':
            return
        if calibre_db.session is not None:
            try:
                calibre_db.session.rollback()
                calibre_db.session.expire_all()
            except Exception as e:
                log.debug("DESKTOP_COMPAT_MODE: rollback failed, snapshot may be stale: %s", e)
        # Clear the Flask-session shelf count cache so sidebar counts stay fresh.
        session.pop('magic_shelf_counts', None)

    @application.teardown_appcontext
    def shutdown_session(exception=None):
        # Close before session_factory.remove(): they operate on different objects (concrete
        # Session vs scoped proxy), and NullPool needs an explicit close to drop the connection.
        if calibre_db._desktop_compat and calibre_db.session is not None:
            try:
                calibre_db.session.rollback()
            except Exception:
                pass
            try:
                calibre_db.session.close()
            except Exception:
                pass
        if calibre_db.session_factory:
            calibre_db.session_factory.remove()

    if first_process_initialization:
        from .schedule import register_scheduled_tasks, register_startup_tasks
        register_scheduled_tasks(runtime_config.schedule_reconnect)
        register_startup_tasks()
        state.initialized = True

    # cps.web historically binds two app-wide response hooks when the module is
    # imported. If it predates this factory call, copy those hooks to the fresh
    # app; if it does not, its first import will bind them to globals()["app"].
    web_module = sys.modules.get("cps.web")
    if web_module is not None:
        web_module.register_app_hooks(application)

    application.extensions[_FACTORY_COMPLETE_MARKER] = True
    return application
