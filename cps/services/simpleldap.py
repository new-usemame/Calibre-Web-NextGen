# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import base64
import threading

from flask_simpleldap import LDAP, LDAPException
from flask_simpleldap import ldap as pyLDAP
from flask import current_app, has_app_context
from .. import constants, logger

try:  # pragma: no cover - environment branch
    from gevent.threadpool import ThreadPool as _GeventThreadPool
except ImportError:  # pragma: no cover - environment branch
    _GeventThreadPool = None

try:
    from ldap.pkginfo import __version__ as ldapVersion
except ImportError:
    pass

log = logger.create()


def _escape_ldap_filter(s):
    """Escape special characters for safe use in LDAP filter strings (RFC 4515)."""
    s = s.replace('\\', '\\5c')
    s = s.replace('*', '\\2a')
    s = s.replace('(', '\\28')
    s = s.replace(')', '\\29')
    s = s.replace('\x00', '\\00')
    return s


class LDAPLogger(object):

    @staticmethod
    def write(message):
        try:
            log.debug(message.strip("\n").replace("\n", ""))
        except Exception:
            log.debug("Logging Error")


class mySimpleLDap(LDAP):

    @staticmethod
    def init_app(app):
        super(mySimpleLDap, mySimpleLDap).init_app(app)
        app.config.setdefault('LDAP_LOGLEVEL', 0)

    @property
    def initialize(self):
        """Initialize a connection to the LDAP server.

        :return: LDAP connection object.
        """
        try:
            log_level = 2 if current_app.config['LDAP_LOGLEVEL'] == logger.logging.DEBUG else 0
            conn = pyLDAP.initialize('{0}://{1}:{2}'.format(
                current_app.config['LDAP_SCHEMA'],
                current_app.config['LDAP_HOST'],
                current_app.config['LDAP_PORT']), trace_level=log_level, trace_file=LDAPLogger())
            conn.set_option(pyLDAP.OPT_NETWORK_TIMEOUT,
                            current_app.config['LDAP_TIMEOUT'])
            conn = self._set_custom_options(conn)
            conn.protocol_version = pyLDAP.VERSION3
            if current_app.config['LDAP_USE_TLS']:
                conn.start_tls_s()
            return conn
        except pyLDAP.LDAPError as e:
            raise LDAPException(self.error(e.args))


_ldap = mySimpleLDap()


def init_app(app, config):
    if config.config_login_type != constants.LOGIN_LDAP:
        return

    app.config['LDAP_HOST'] = config.config_ldap_provider_url
    app.config['LDAP_PORT'] = config.config_ldap_port
    app.config['LDAP_CUSTOM_OPTIONS'] = {pyLDAP.OPT_REFERRALS: 0}
    if config.config_ldap_encryption == 2:
        app.config['LDAP_SCHEMA'] = 'ldaps'
    else:
        app.config['LDAP_SCHEMA'] = 'ldap'
    if config.config_ldap_authentication > constants.LDAP_AUTH_ANONYMOUS:
        if config.config_ldap_authentication > constants.LDAP_AUTH_UNAUTHENTICATE:
            if config.config_ldap_serv_password_e is None:
                config.config_ldap_serv_password_e = ''
            app.config['LDAP_PASSWORD'] = config.config_ldap_serv_password_e
        else:
            app.config['LDAP_PASSWORD'] = ""
        app.config['LDAP_USERNAME'] = config.config_ldap_serv_username
    else:
        app.config['LDAP_USERNAME'] = ""
        app.config['LDAP_PASSWORD'] = ""
    if bool(config.config_ldap_cert_path):
        app.config['LDAP_CUSTOM_OPTIONS'].update({
            pyLDAP.OPT_X_TLS_REQUIRE_CERT: pyLDAP.OPT_X_TLS_DEMAND,
            pyLDAP.OPT_X_TLS_CACERTFILE: config.config_ldap_cacert_path,
            pyLDAP.OPT_X_TLS_CERTFILE: config.config_ldap_cert_path,
            pyLDAP.OPT_X_TLS_KEYFILE: config.config_ldap_key_path,
            pyLDAP.OPT_X_TLS_NEWCTX: 0
            })

    app.config['LDAP_BASE_DN'] = config.config_ldap_dn
    app.config['LDAP_USER_OBJECT_FILTER'] = config.config_ldap_user_object

    app.config['LDAP_USE_TLS'] = bool(config.config_ldap_encryption == 1)
    app.config['LDAP_USE_SSL'] = bool(config.config_ldap_encryption == 2)
    app.config['LDAP_OPENLDAP'] = bool(config.config_ldap_openldap)
    app.config['LDAP_GROUP_OBJECT_FILTER'] = config.config_ldap_group_object_filter
    app.config['LDAP_GROUP_MEMBERS_FIELD'] = config.config_ldap_group_members_field
    app.config['LDAP_LOGLEVEL'] = config.config_log_level
    try:
        _ldap.init_app(app)
    except ValueError:
        if bool(config.config_ldap_cert_path):
            app.config['LDAP_CUSTOM_OPTIONS'].pop(pyLDAP.OPT_X_TLS_NEWCTX)
        try:
            _ldap.init_app(app)
        except RuntimeError as e:
            log.error(e)
    except RuntimeError as e:
        log.error(e)


# python-ldap waits on the network inside C, holding the one OS thread the
# gevent hub runs on (this app does not monkey-patch), so every request
# waited on each directory call, and on a directory that does not answer, for
# the whole connect timeout (flask-simpleldap's default is 10 s) per sign-in.
# Directory calls run on a few worker threads instead, and the calling
# greenlet yields while it waits.
_DIRECTORY_THREADS = 4
_pool = None
_UNREACHABLE = ("Can't contact LDAP server", "Timed out")


class _Reachability:
    """While the directory is unreachable, one call at a time tries it.

    The others are answered "Can't contact LDAP server" at once rather than
    each waiting out the connect timeout. The first call that gets through
    marks it reachable again, so recovery is noticed on the next sign-in.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.unreachable = False
        self._trying = False

    def enter(self):
        """True if this call may reach the directory; False to fail fast."""
        with self._lock:
            if not self.unreachable:
                return True
            if self._trying:
                return False
            self._trying = True
            return True

    def leave(self, reached):
        with self._lock:
            if reached and self.unreachable:
                log.info("LDAP server reachable again")
            elif not reached and not self.unreachable:
                log.warning("LDAP server unreachable; sign-ins that need it fail "
                            "at once until a retry reaches it")
            self.unreachable = not reached
            self._trying = False


_reachability = _Reachability()


def _in_directory_thread(function, *args, **kwargs):
    """Run a directory call off the hub, failing fast while it is unreachable."""
    if not _reachability.enter():
        raise LDAPException(_UNREACHABLE[0])
    reached = True
    try:
        return _run_off_the_hub(function, *args, **kwargs)
    except LDAPException as ex:
        reached = ex.message not in _UNREACHABLE
        raise
    finally:
        _reachability.leave(reached)


def _run_off_the_hub(function, *args, **kwargs):
    global _pool
    if _GeventThreadPool is None or not has_app_context():
        return function(*args, **kwargs)
    app = current_app._get_current_object()

    def call():
        # flask-simpleldap reads its settings from current_app.
        with app.app_context():
            return function(*args, **kwargs)

    if _pool is None:
        _pool = _GeventThreadPool(_DIRECTORY_THREADS)
    return _pool.apply(call)


def get_object_details(user=None, query_filter=None):
    return _in_directory_thread(_ldap.get_object_details, user, query_filter=query_filter)


def bind():
    return _ldap.bind()


def get_group_members(group):
    return _in_directory_thread(_ldap.get_group_members, group)


def basic_auth_required(func):
    return _ldap.basic_auth_required(func)


def bind_user(username, password):
    '''Attempts a LDAP login.

    :returns: True if login succeeded, False if login failed, None if server unavailable.
    '''
    # flask-simpleldap leaves this check to its caller (see its bind_user),
    # and every sign-in path reaches the directory here.
    if not password:
        log.debug("LDAP login '%s' refused: empty password", username)
        return False, None
    # Escape LDAP special characters to prevent LDAP injection in search filters
    safe_username = _escape_ldap_filter(username)

    def look_up_and_bind():
        if _ldap.get_object_details(safe_username):
            return _ldap.bind_user(safe_username, password) is not None
        return None

    try:
        result = _in_directory_thread(look_up_and_bind)
        log.debug("LDAP login '%s': %r", username, result)
        return result, None     # None: user not found
    except (TypeError, AttributeError, KeyError) as ex:
        error = ("LDAP bind_user: %s" % ex)
        return None, error
    except LDAPException as ex:
        if ex.message == 'Invalid credentials':
            error = "LDAP admin login failed"
            return None, error
        if ex.message == "Can't contact LDAP server":
            # log.warning('LDAP Server down: %s', ex)
            error = ('LDAP Server down: %s' % ex)
            return None,  error
        else:
            error = ('LDAP Server error: %s' % ex.message)
            return None, error
