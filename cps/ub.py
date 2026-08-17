# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import atexit
import os
import re
import sys
import sqlite3
import time
from datetime import datetime, timezone, timedelta
import itertools
import uuid
from flask import session as flask_session, has_request_context, g
from binascii import hexlify

from .cw_login import AnonymousUserMixin, current_user
from .cw_login import user_logged_in

try:
    from flask_dance.consumer.backend.sqla import OAuthConsumerMixin  # pyright: ignore[reportMissingImports]
    oauth_support = True
except ImportError as e:
    # fails on flask-dance >1.3, due to renaming
    try:
        from flask_dance.consumer.storage.sqla import OAuthConsumerMixin
        oauth_support = True
    except ImportError as e:
        OAuthConsumerMixin = BaseException
        oauth_support = False
from sqlalchemy import create_engine, DDL, exc, exists, event, text
from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, UniqueConstraint
from sqlalchemy import String, Integer, SmallInteger, Boolean, DateTime, Float, JSON, Text, BLOB
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql.expression import func
try:
    # Compatibility with sqlalchemy 2.0
    from sqlalchemy.orm import declarative_base
except ImportError:
    from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import backref, relationship, sessionmaker, Session, scoped_session, validates
from werkzeug.security import generate_password_hash

from . import constants, logger
from .string_helper import strip_whitespaces

log = logger.create()

session: Session | None = None
app_DB_path = None
Base = declarative_base()
searched_ids = {}

logged_in = dict()


def _safe_session_rollback(_session, label=""):
    try:
        _session.rollback()
    except Exception as e:
        if label:
            log.debug("Failed to rollback session after %s migration check: %s", label, e)


def _run_ddl_with_retry(engine, statements, retries=5, base_delay=0.25):
    if isinstance(statements, str):
        statements = [statements]

    last_error = None
    for attempt in range(retries):
        try:
            with engine.begin() as conn:
                conn.execute(text("PRAGMA busy_timeout=5000"))
                for stmt in statements:
                    conn.execute(text(stmt))
            return True
        except exc.OperationalError as e:
            last_error = e
            if "database is locked" in str(e).lower() and attempt < retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise
    if last_error:
        raise last_error
    return False


def signal_store_user_session(object, user):
    store_user_session()


def store_user_session():
    _user = flask_session.get('_user_id', "")
    _id = flask_session.get('_id', "")
    _random = flask_session.get('_random', "")
    if flask_session.get('_user_id', ""):
        try:
            if not check_user_session(_user, _id, _random):
                expiry = int((datetime.now()  + timedelta(days=31)).timestamp())
                user_session = User_Sessions(_user, _id, _random, expiry)
                session.add(user_session)
                session.commit()
                log.debug("Login and store session : " + _id)
            else:
                log.debug("Found stored session: " + _id)
        except (exc.OperationalError, exc.InvalidRequestError) as e:
            session.rollback()
            log.exception(e)
    else:
        log.error("No user id in session")


def delete_user_session(user_id, session_key):
    try:
        log.debug("Deleted session_key: " + session_key)
        session.query(User_Sessions).filter(User_Sessions.user_id == user_id,
                                            User_Sessions.session_key == session_key).delete()
        session.commit()
    except (exc.OperationalError, exc.InvalidRequestError) as ex:
        session.rollback()
        log.exception(ex)


def check_user_session(user_id, session_key, random):
    try:
        found = session.query(User_Sessions).filter(User_Sessions.user_id==user_id,
                                                    User_Sessions.session_key==session_key,
                                                    User_Sessions.random == random,
                                                    ).one_or_none()
        if found is not None:
            new_expiry = int((datetime.now()  + timedelta(days=31)).timestamp())
            if new_expiry - found.expiry > 86400:
                found.expiry = new_expiry
                session.merge(found)
                session.commit()
        return bool(found)
    except (exc.OperationalError, exc.InvalidRequestError) as e:
        session.rollback()
        log.exception(e)
        return False


user_logged_in.connect(signal_store_user_session)

def store_ids(result):
    ids = list()
    for element in result:
        ids.append(element.id)
    searched_ids[current_user.id] = ids

def store_combo_ids(result):
    ids = list()
    for element in result:
        ids.append(element[0].id)
    searched_ids[current_user.id] = ids


class UserBase:

    @property
    def is_authenticated(self):
        return self.is_active

    def _has_role(self, role_flag):
        return constants.has_flag(self.role, role_flag)

    def role_admin(self):
        return self._has_role(constants.ROLE_ADMIN)

    def role_download(self):
        return self._has_role(constants.ROLE_DOWNLOAD)

    def role_upload(self):
        return self._has_role(constants.ROLE_UPLOAD)

    def role_edit(self):
        return self._has_role(constants.ROLE_EDIT)

    def role_passwd(self):
        return self._has_role(constants.ROLE_PASSWD)

    def role_anonymous(self):
        return self._has_role(constants.ROLE_ANONYMOUS)

    def role_edit_shelfs(self):
        return self._has_role(constants.ROLE_EDIT_SHELFS)

    def role_delete_books(self):
        return self._has_role(constants.ROLE_DELETE_BOOKS)

    def role_viewer(self):
        return self._has_role(constants.ROLE_VIEWER)

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return self.role_anonymous()

    def get_id(self):
        return str(self.id)

    def filter_language(self):
        return self.default_language

    def check_visibility(self, value):
        if value == constants.SIDEBAR_RECENT:
            return True
        return constants.has_flag(self.sidebar_view, value)

    def show_detail_random(self):
        return self.check_visibility(constants.DETAIL_RANDOM)

    def list_denied_tags(self):
        mct = self.denied_tags or ""
        return [strip_whitespaces(t) for t in mct.split(",")]

    def list_allowed_tags(self):
        mct = self.allowed_tags or ""
        return [strip_whitespaces(t) for t in mct.split(",")]

    def list_denied_column_values(self):
        mct = self.denied_column_value or ""
        return [strip_whitespaces(t) for t in mct.split(",")]

    def list_allowed_column_values(self):
        mct = self.allowed_column_value or ""
        return [strip_whitespaces(t) for t in mct.split(",")]

    def get_view_property(self, page, prop):
        if not self.view_settings.get(page):
            return None
        return self.view_settings[page].get(prop)

    def set_view_property(self, page, prop, value):
        if not self.view_settings.get(page):
            self.view_settings[page] = dict()
        self.view_settings[page][prop] = value
        try:
            flag_modified(self, "view_settings")
        except AttributeError:
            pass
        try:
            session.commit()
        except (exc.OperationalError, exc.InvalidRequestError) as e:
            session.rollback()
            log.error_or_exception(e)

    def __repr__(self):
        return '<User %r>' % self.name


# Baseclass for Users in Calibre-Web, settings which are depending on certain users are stored here. It is derived from
# User Base (all access methods are declared there)
class User(UserBase, Base):
    __tablename__ = 'user'
    __table_args__ = {'sqlite_autoincrement': True}

    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True)
    email = Column(String(120), unique=True, default="")
    role = Column(SmallInteger, default=constants.ROLE_USER)
    password = Column(String)
    kindle_mail = Column(String(120), default="")
    kindle_mail_subject = Column(String(256), default="", doc="Subject line for eReader email sending, empty=default")
    shelf = relationship('Shelf', backref='user', lazy='dynamic', order_by='Shelf.name')
    magic_shelf = relationship('MagicShelf', backref='user', lazy='dynamic', order_by='MagicShelf.name')
    downloads = relationship('Downloads', backref='user', lazy='dynamic')
    locale = Column(String(2), default="en")
    sidebar_view = Column(Integer, default=1)
    default_language = Column(String(3), default="all")
    denied_tags = Column(String, default="")
    allowed_tags = Column(String, default="")
    denied_column_value = Column(String, default="")
    allowed_column_value = Column(String, default="")
    remote_auth_token = relationship('RemoteAuthToken', backref='user', lazy='dynamic')
    kobo_annotation_book_states = relationship(
        "KoboAnnotationBookState", back_populates="user",
        cascade="all, delete-orphan",
    )
    kobo_opaque_present_guards = relationship(
        "KoboOpaqueContentPresentGuard", back_populates="user",
        cascade="all, delete-orphan",
    )
    view_settings = Column(JSON, default={})
    kobo_only_shelves_sync = Column(Integer, default=0)
    opds_only_shelves_sync = Column(Integer, default=0)
    # Stage 0 Kobo two-way annotation opt-in.  No route consumes this flag
    # until a later rollout stage; existing and new users are safely off.
    kobo_two_way_annotation_sync = Column(
        Boolean, nullable=False, default=False, server_default=text("0"),
    )
    hardcover_token = Column(String, default=None)
    # New per-user theme (0=default/light, 1=caliBlur) replacing global-only behavior
    theme = Column(Integer, default=1)
    # Auto-send settings for new books
    auto_send_enabled = Column(Boolean, default=False)
    # Allow entering additional email addresses on send-to-eReader
    allow_additional_ereader_emails = Column(Boolean, default=True)
    # Cover-preview rendering (eReader-shape previews on book detail / shelf).
    # Defaults match cps.services.cover_preview.DEFAULT_PRESET +
    # DEFAULT_FILL_MODE. New users opt-in; the Phase-2 migration in Task 2
    # sets show_ereader_previews=False for existing users so behavior is
    # unchanged on upgrade.
    show_ereader_previews = Column(Boolean, default=True)
    preview_preset = Column(String, default="kobo_libra_color")
    preview_default_fill = Column(String, default="edge_mirror")
    preview_default_color = Column(String, nullable=True)
    # #701 — user-selectable UI font presets (new interface). Stores a short
    # preset key ("", "serif", "mono", "system-sans"); the CSS font stacks live
    # only in the SPA (frontend/src/lib/fonts.ts). "" = use the theme default.
    ui_font_body = Column(String, default="")
    ui_font_display = Column(String, default="")


if oauth_support:
    class OAuth(OAuthConsumerMixin, Base):
        provider_user_id = Column(String(256))
        user_id = Column(Integer, ForeignKey(User.id))
        user = relationship(User)


class OAuthProvider(Base):
    __tablename__ = 'oauthProvider'

    id = Column(Integer, primary_key=True)
    provider_name = Column(String)
    oauth_client_id = Column(String)
    oauth_client_secret = Column(String)
    oauth_base_url = Column(String, default=None)
    oauth_authorize_url = Column(String, default=None)
    oauth_token_url = Column(String, default=None)
    oauth_userinfo_url = Column(String, default=None)
    oauth_admin_group = Column(String, default=None)
    oauth_group_claim = Column(String, default='groups')
    oauth_allowed_groups = Column(String, default=None)
    oauth_require_group = Column(Boolean, default=False)
    # Per-provider default role for newly created OAuth users. NULL means
    # "not configured" → fall back to the global config_default_role, so an
    # upgrade never silently strips permissions from new OAuth sign-ups.
    oauth_default_role = Column(SmallInteger, default=None)
    metadata_url = Column(String, default=None)  # For OIDC auto-discovery
    scope = Column(String, default="openid profile email")  # Customizable OAuth scopes
    username_mapper = Column(String, default="preferred_username")  # JWT field for username
    email_mapper = Column(String, default="email")  # JWT field for email
    login_button = Column(String, default="OpenID Connect")  # Custom button text
    active = Column(Boolean)


# Class for anonymous user is derived from User base and completely overrides methods and properties for the
# anonymous user
class Anonymous(AnonymousUserMixin, UserBase):
    def __init__(self):
        self.hardcover_token = None
        self.kobo_only_shelves_sync = None
        self.opds_only_shelves_sync = None
        self.kobo_two_way_annotation_sync = False
        self.view_settings = {}
        self.allowed_column_value = None
        self.allowed_tags = None
        self.denied_tags = None
        self.kindle_mail = None
        self.kindle_mail_subject = None
        self.locale = None
        self.default_language = None
        self.sidebar_view = None
        self.id = None
        self.role = None
        self.name = None
        self.auto_send_enabled = False
        self.loadSettings()

    def loadSettings(self):
        data = session.query(User).filter(User.role.op('&')(constants.ROLE_ANONYMOUS) == constants.ROLE_ANONYMOUS)\
            .first()  # type: User
        self.name = data.name
        self.role = data.role
        self.id=data.id
        self.sidebar_view = data.sidebar_view
        self.default_language = data.default_language
        self.locale = data.locale
        self.kindle_mail = data.kindle_mail
        self.kindle_mail_subject = data.kindle_mail_subject
        self.denied_tags = data.denied_tags
        self.allowed_tags = data.allowed_tags
        self.denied_column_value = data.denied_column_value
        self.allowed_column_value = data.allowed_column_value
        self.view_settings = data.view_settings
        self.kobo_only_shelves_sync = data.kobo_only_shelves_sync
        self.opds_only_shelves_sync = data.opds_only_shelves_sync
        self.kobo_two_way_annotation_sync = data.kobo_two_way_annotation_sync
        self.hardcover_token = data.hardcover_token
        self.auto_send_enabled = data.auto_send_enabled
        # Presentation columns live on User, not on the shared UserBase mixin,
        # so this hand-written copy is the only thing that puts them on a guest.
        # Omitting them made every guest-reachable path that renders the current
        # user raise AttributeError -- cps/api/serializers.py::serialize_user and
        # cps/api/account.py both read all three (#1023). Kept in sync by
        # tests/unit/test_1023_anon_browse_spa_login_wall.py, which AST-derives
        # the serializer's reads rather than trusting a hand-maintained list.
        self.theme = data.theme
        self.ui_font_body = data.ui_font_body
        self.ui_font_display = data.ui_font_display

    def role_admin(self):
        return False

    @property
    def is_active(self):
        return False

    @property
    def is_anonymous(self):
        return True

    @property
    def is_authenticated(self):
        return False

    def get_view_property(self, page, prop):
        if 'view' in flask_session:
            if not flask_session['view'].get(page):
                return None
            return flask_session['view'][page].get(prop)
        return None

    def set_view_property(self, page, prop, value):
        if not 'view' in flask_session:
            flask_session['view'] = dict()
        if not flask_session['view'].get(page):
            flask_session['view'][page] = dict()
        flask_session['view'][page][prop] = value

class User_Sessions(Base):
    __tablename__ = 'user_session'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'))
    session_key = Column(String, default="")
    random = Column(String, default="")
    expiry = Column(Integer)


    def __init__(self, user_id, session_key, random, expiry):
        super().__init__()
        self.user_id = user_id
        self.session_key = session_key
        self.random = random
        self.expiry = expiry


class UserAppPassword(Base):
    """Per-user app passwords for HTTP Basic auth on OPDS / KOSync endpoints.

    OAuth users (Authentik / Authelia / Keycloak) can authenticate to the web UI
    via the IdP redirect flow but have no usable password for HTTP Basic auth.
    LDAP users may prefer not to expose their directory password to OPDS / KOSync
    clients. App passwords let any user mint a long random token bound to a
    label (e.g. "Kobo", "KOReader iPad"); the cleartext is shown once at create
    time and then only its `werkzeug` hash is stored.

    See `notes/oauth-opds-app-passwords-DESIGN.md` and fork issue #95.
    """
    __tablename__ = 'user_app_password'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'),
                     nullable=False, index=True)
    label = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False,
                        default=lambda: datetime.now(timezone.utc))
    last_used_at = Column(DateTime)
    revoked = Column(Boolean, nullable=False, default=False)


# Baseclass representing Shelfs in calibre-web in app.db
class Shelf(Base):
    __tablename__ = 'shelf'

    id = Column(Integer, primary_key=True)
    uuid = Column(String, default=lambda: str(uuid.uuid4()))
    name = Column(String)
    is_public = Column(Integer, default=0)
    user_id = Column(Integer, ForeignKey('user.id'))
    kobo_sync = Column(Boolean, default=False)
    books = relationship("BookShelf", backref="ub_shelf", cascade="all, delete-orphan", lazy="dynamic")
    created = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_modified = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return '<Shelf %d:%r>' % (self.id, self.name)


# Baseclass representing Magic Shelfs in calibre-web in app.db
class MagicShelf(Base):
    __tablename__ = 'magic_shelf'

    id = Column(Integer, primary_key=True)
    uuid = Column(String, default=lambda: str(uuid.uuid4()))
    name = Column(String)
    is_public = Column(Integer, default=0)
    is_system = Column(Boolean, default=False)  # System-created template shelves
    user_id = Column(Integer, ForeignKey('user.id'))
    icon = Column(String, default="glyphicon-star")
    rules = Column(JSON, default={})
    kobo_sync = Column(Boolean, default=False)  # Sync to Kobo devices
    created = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_modified = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint('user_id', 'name', 'is_system', name='unique_user_system_shelf_name'),
    )

    def __repr__(self):
        return '<MagicShelf %d:%r>' % (self.id, self.name)


class MagicShelfCache(Base):
    __tablename__ = 'magic_shelf_cache'

    id = Column(Integer, primary_key=True)
    shelf_id = Column(Integer, ForeignKey('magic_shelf.id'), index=True)
    user_id = Column(Integer, ForeignKey('user.id'), index=True)
    sort_param = Column(String, default='stored')
    book_ids = Column(JSON)  # Stores [1, 45, 2, ...]
    total_count = Column(Integer)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Composite index for fast lookups
    __table_args__ = (
        Index('ix_magic_shelf_cache_lookup', 'shelf_id', 'user_id', 'sort_param'),
    )


class OpdsShelfExposure(Base):
    __tablename__ = 'opds_shelf_exposure'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False, index=True)
    shelf_id = Column(Integer, ForeignKey('shelf.id'), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint('user_id', 'shelf_id', name='unique_user_opds_shelf_exposure'),
    )


class OpdsMagicShelfExposure(Base):
    __tablename__ = 'opds_magic_shelf_exposure'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False, index=True)
    shelf_id = Column(Integer, ForeignKey('magic_shelf.id'), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint('user_id', 'shelf_id', name='unique_user_opds_magic_shelf_exposure'),
    )


class HiddenMagicShelfTemplate(Base):
    __tablename__ = 'hidden_magic_shelf_templates'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False)
    template_key = Column(String, nullable=True)  # For system templates: 'recently_added', 'highly_rated', etc.
    shelf_id = Column(Integer, ForeignKey('magic_shelf.id'), nullable=True)  # For custom public shelves
    hidden_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        # Either template_key OR shelf_id must be set, but not both
        # User can only hide the same template/shelf once
        UniqueConstraint('user_id', 'template_key', name='unique_user_template_hidden'),
        UniqueConstraint('user_id', 'shelf_id', name='unique_user_shelf_hidden'),
    )

    def __repr__(self):
        if self.template_key:
            return '<HiddenMagicShelfTemplate %d: user=%d template=%s>' % (self.id, self.user_id, self.template_key)
        else:
            return '<HiddenMagicShelfTemplate %d: user=%d shelf_id=%d>' % (self.id, self.user_id, self.shelf_id)


class DismissedDuplicateGroup(Base):
    __tablename__ = 'dismissed_duplicate_groups'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False)
    group_hash = Column(String(32), nullable=False)  # MD5 hash of title+author combo (display data; transitional)
    # D5: the stable identity of the group — the SHA-256 duplicate_key from
    # cwa_duplicate_book_keys. group_hash is derived from the DISPLAY
    # title/author of whichever book happens to sort first, so a new ingest or
    # a metadata edit changed it and dismissed groups resurfaced (and two
    # groups sharing a raw display title collided into one dismissal).
    # Dismissals match on duplicate_key when present; group_hash remains for
    # rows written before the migration and for the UI routes.
    duplicate_key = Column(String, nullable=True)
    dismissed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        # User can only dismiss the same duplicate group once
        UniqueConstraint('user_id', 'group_hash', name='unique_user_duplicate_dismissed'),
    )

    def __repr__(self):
        return '<DismissedDuplicateGroup %d: user=%d hash=%s>' % (self.id, self.user_id, self.group_hash)


# Baseclass representing Relationship between books and Shelfs in Calibre-Web in app.db (N:M)
class BookShelf(Base):
    __tablename__ = 'book_shelf_link'

    id = Column(Integer, primary_key=True)
    book_id = Column(Integer)
    order = Column(Integer)
    shelf = Column(Integer, ForeignKey('shelf.id'))
    date_added = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return '<Book %r>' % self.id


# This table keeps track of deleted Shelves so that deletes can be propagated to any paired Kobo device.
class ShelfArchive(Base):
    __tablename__ = 'shelf_archive'

    id = Column(Integer, primary_key=True)
    uuid = Column(String)
    user_id = Column(Integer, ForeignKey('user.id'))
    last_modified = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReadBook(Base):
    __tablename__ = 'book_read_link'

    STATUS_UNREAD = 0
    STATUS_FINISHED = 1
    STATUS_IN_PROGRESS = 2

    id = Column(Integer, primary_key=True)
    book_id = Column(Integer, unique=False)
    user_id = Column(Integer, ForeignKey('user.id'), unique=False)
    read_status = Column(Integer, unique=False, default=STATUS_UNREAD, nullable=False)
    kobo_reading_state = relationship("KoboReadingState", uselist=False,
                                      primaryjoin="and_(ReadBook.user_id == foreign(KoboReadingState.user_id), "
                                                  "ReadBook.book_id == foreign(KoboReadingState.book_id))",
                                      cascade="all",
                                      backref=backref("book_read_link",
                                                      uselist=False))
    last_modified = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    last_time_started_reading = Column(DateTime, nullable=True)
    times_started_reading = Column(Integer, default=0, nullable=False)

    # Audit 2026-05-11: enforce per-(user, book) uniqueness so concurrent
    # Kobo PUTs can't produce duplicate rows. The Kobo state handler reads
    # via .one_or_none(); duplicates would surface as MultipleResultsFound.
    __table_args__ = (
        UniqueConstraint('user_id', 'book_id', name='uq_book_read_link_user_book'),
    )


class Bookmark(Base):
    __tablename__ = 'bookmark'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'))
    book_id = Column(Integer)
    format = Column(String(collation='NOCASE'))
    bookmark_key = Column(String)


class BookCoverLock(Base):
    """Per-book flag that prevents the cover from being overwritten by the
    metadata-fetch path on the edit page. Set/cleared from the cover-picker
    page at /book/<id>/cover. Resolves janeczku/calibre-web#2165 ("Option
    to keep existing book cover when fetching metadata", 8 hearts).

    Lock is per book, not per user — a cover is a property of the book,
    not of the viewer. ``locked_by`` and ``locked_at`` are audit metadata
    only; reads check ``locked``.
    """
    __tablename__ = 'book_cover_lock'

    book_id = Column(Integer, primary_key=True)
    locked = Column(Boolean, nullable=False, default=False)
    locked_by = Column(Integer)
    locked_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class BookCoverPreview(Base):
    """Per-user-per-book override of the cover-preview fill style + color.

    Row exists only when the user has explicitly set fill/color for this
    book OR toggled the lock. No row = follows the user's default fill +
    default color (stored on the User row).

    user_id has FK with ON DELETE CASCADE.
    book_id references metadata.db's books.id but cannot be a SQL-level
    FK because the two databases are separate SQLite files; orphaned
    rows are swept by the daily cleanup task in
    cps/services/cover_preview_cleanup.py.
    """
    __tablename__ = "book_cover_preview"

    user_id = Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    book_id = Column(Integer, primary_key=True)
    fill_mode = Column(String, nullable=False)
    custom_color = Column(String, nullable=True)
    locked = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", lazy="joined", backref="cover_previews")


# Baseclass representing books that are archived on the user's Kobo device.
class ArchivedBook(Base):
    __tablename__ = 'archived_book'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'))
    book_id = Column(Integer)
    is_archived = Column(Boolean, unique=False)
    last_modified = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Audit 2026-05-11: enforce per-(user, book) uniqueness. Without it two
    # concurrent Kobo PUTs could create duplicate rows; later reads
    # (which use .one_or_none()) raise MultipleResultsFound -> 500.
    __table_args__ = (
        UniqueConstraint('user_id', 'book_id', name='uq_archived_book_user_book'),
    )


class UserHiddenBook(Base):
    """Per-user hidden books — fork issue #64.

    Distinct from ArchivedBook: archived is a deletion-track / sync-pause
    semantic; hidden is a personal-library declutter. Same shape (user_id +
    book_id pair, last_modified) but a separate table so the two semantics
    don't bleed into each other. The web UI exposes hide/unhide on the book
    detail page and a dedicated `/hidden` listing for un-hiding.

    common_filters() in cps/db.py reads this table and excludes hidden
    books from index, search, OPDS, and shelf listings (the same code path
    that already handles archived books).
    """
    __tablename__ = 'user_hidden_book'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'),
                     nullable=False, index=True)
    book_id = Column(Integer, nullable=False, index=True)
    hidden_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint('user_id', 'book_id', name='uq_user_hidden_book'),
    )


class FavoriteBook(Base):
    """Per-user favorited / starred books — fork #27.

    Presence-based (a row exists iff the user has starred that book), mirroring
    UserHiddenBook rather than ArchivedBook's boolean column: un-starring deletes
    the row. Starred books get a dedicated /favorites listing and a star badge on
    their cover. The per-(user, book) unique constraint keeps a fast double-tap
    from creating duplicate rows.
    """
    __tablename__ = 'favorite_book'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'),
                     nullable=False, index=True)
    book_id = Column(Integer, nullable=False, index=True)
    favorited_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint('user_id', 'book_id', name='uq_favorite_book'),
    )


class KoboSyncedBooks(Base):
    __tablename__ = 'kobo_synced_books'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('user.id'))
    book_id = Column(Integer)
    book_uuid = Column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint('user_id', 'book_id', name='uq_kobo_synced_books_user_book'),
    )


class NoticeEvent(Base):
    """Device-agnostic occurrence that may need to be shown to selected users.

    Book ids belong to calibre's separate metadata database, so they deliberately
    cannot be foreign keys here. ``occurrence_key`` makes recurrence explicit:
    dismissing one occurrence never suppresses a later event of the same type.
    """
    __tablename__ = "notice_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    notice_type = Column(String(80), nullable=False)
    occurrence_key = Column(String(64), nullable=False)
    scope = Column(String(16), nullable=False)
    book_id = Column(Integer, nullable=True)
    book_uuid = Column(String(64), nullable=True)
    title_snapshot = Column(String, nullable=True)
    payload_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    active = Column(Boolean, nullable=False, default=True)

    deliveries = relationship(
        "UserNoticeDelivery", back_populates="event",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("notice_type", "occurrence_key", name="uq_notice_event_occurrence"),
        CheckConstraint(
            "(scope = 'global' AND book_id IS NULL) OR "
            "(scope = 'book' AND book_id IS NOT NULL)",
            name="ck_notice_event_scope_book",
        ),
        Index("ix_notice_event_type_active", "notice_type", "active", "created_at"),
        Index("ix_notice_event_book", "book_id", "active"),
    )


class UserNoticeDelivery(Base):
    """Audience membership and permanent per-user dismissal for one event."""
    __tablename__ = "user_notice_delivery"

    event_id = Column(
        Integer, ForeignKey("notice_event.id", ondelete="CASCADE"), primary_key=True,
    )
    user_id = Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    first_presented_at = Column(DateTime, nullable=True)
    dismissed_at = Column(DateTime, nullable=True)

    event = relationship("NoticeEvent", back_populates="deliveries")

    __table_args__ = (
        Index("ix_user_notice_delivery_inbox", "user_id", "dismissed_at", "event_id"),
    )


class KepubPackageRepair(Base):
    """Durable cross-database/file state for one detected package repair."""
    __tablename__ = "kepub_package_repair"

    id = Column(Integer, primary_key=True, autoincrement=True)
    occurrence_key = Column(String(64), nullable=False, unique=True)
    book_id = Column(Integer, nullable=False, index=True)
    book_uuid = Column(String(64), nullable=True)
    source_sha256 = Column(String(64), nullable=False)
    source_size = Column(Integer, nullable=True)
    source_mtime_ns = Column(Integer, nullable=True)
    source_ctime_ns = Column(Integer, nullable=True)
    repair_version = Column(Integer, nullable=True)
    repaired_sha256 = Column(String(64), nullable=True)
    backup_path = Column(String, nullable=True)
    status = Column(String(24), nullable=False)
    error_message = Column(String, nullable=True)
    detected_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    file_repaired_at = Column(DateTime, nullable=True)
    metadata_bumped_at = Column(DateTime, nullable=True)
    notice_event_id = Column(Integer, ForeignKey("notice_event.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('detected', 'file_repaired', 'metadata_bumped', "
            "'completed', 'failed', 'unsupported')",
            name="ck_kepub_package_repair_status",
        ),
        Index("ix_kepub_package_repair_book_status", "book_id", "status"),
    )


class BookOriginalFilename(Base):
    """The filename a book arrived with in the ingest folder, captured at
    import time (fork #346, @BakaPhoenix + @magdalar). Ingest renames files
    to match their (possibly wrongly auto-matched) metadata, so the
    as-imported name is the one stable reference a user has for recognizing
    misidentified books while fixing tags.

    One row per book — the import that CREATED the book; later format
    additions never overwrite it (the ingest writer uses ON CONFLICT
    DO NOTHING). book_id refers to calibre's metadata.db (cross-database,
    so no FK). Row is removed by delete_whole_book with the other
    book-scoped ub rows.
    """
    __tablename__ = 'book_original_filename'

    book_id = Column(Integer, primary_key=True)
    filename = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class KoboDeletedBook(Base):
    """Tombstone table for books deleted from CW that need to be reported
    to Kobo devices as archived ChangedEntitlement entries on next sync.

    Why we need it: calibre's metadata.db row goes away the moment the
    book is deleted (cps/editbooks.py:delete_whole_book), and the
    KoboSyncedBooks table carries only (user_id, book_id) — no UUID. The
    Kobo protocol needs the book's UUID to address the ChangedEntitlement
    on the device. So we snapshot (user_id, book_uuid, deleted_at) at
    delete time, before the book row is gone.

    Lifecycle: rows live as long as the deletion is "newer than" any
    device's sync cursor. With cursor-based emission (advance
    archive_last_modified past deleted_at on emit), each device cursor moves
    beyond each ChangedEntitlement. Rows can be GC'd by a
    periodic cleanup once they're older than the oldest active sync
    token's archive_last_modified — left as a follow-up; current
    storage cost is one short row per deleted book per affected user.
    """
    __tablename__ = 'kobo_deleted_book'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False, index=True)
    book_uuid = Column(String, nullable=False)
    deleted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        UniqueConstraint('user_id', 'book_uuid', name='uq_kobo_deleted_book_user_uuid'),
    )


# CWA #1258 (backport for fork #153): per-user OPDS shelf-exposure helpers.
# Mirrors the kobo_only_shelves_sync pattern — the User-side flag is added
# in the User class above; the helpers below maintain the two exposure
# tables (regular shelves + magic shelves).
def is_opds_shelf_exposed_for_user(user_id, shelf_id, _session=None):
    s = _session if _session else session
    return s.query(OpdsShelfExposure).filter_by(user_id=user_id, shelf_id=shelf_id).first() is not None


def set_opds_shelf_exposed_for_user(user_id, shelf_id, exposed, _session=None):
    s = _session if _session else session
    existing = s.query(OpdsShelfExposure).filter_by(user_id=user_id, shelf_id=shelf_id).first()
    if exposed:
        if existing is None:
            s.add(OpdsShelfExposure(user_id=user_id, shelf_id=shelf_id))
    elif existing is not None:
        s.delete(existing)


def is_opds_magic_shelf_exposed_for_user(user_id, shelf_id, _session=None):
    s = _session if _session else session
    return s.query(OpdsMagicShelfExposure).filter_by(user_id=user_id, shelf_id=shelf_id).first() is not None


def set_opds_magic_shelf_exposed_for_user(user_id, shelf_id, exposed, _session=None):
    s = _session if _session else session
    existing = s.query(OpdsMagicShelfExposure).filter_by(user_id=user_id, shelf_id=shelf_id).first()
    if exposed:
        if existing is None:
            s.add(OpdsMagicShelfExposure(user_id=user_id, shelf_id=shelf_id))
    elif existing is not None:
        s.delete(existing)

# The Kobo ReadingState API keeps track of 4 timestamped entities:
#   ReadingState, StatusInfo, Statistics, CurrentBookmark
# Which we map to the following 4 tables:
#   KoboReadingState, ReadBook, KoboStatistics and KoboBookmark
class KoboReadingState(Base):
    __tablename__ = 'kobo_reading_state'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('user.id'))
    book_id = Column(Integer)
    last_modified = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    priority_timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    current_bookmark = relationship("KoboBookmark", uselist=False, backref="kobo_reading_state", cascade="all, delete")
    statistics = relationship("KoboStatistics", uselist=False, backref="kobo_reading_state", cascade="all, delete")

    __table_args__ = (
        UniqueConstraint('user_id', 'book_id', name='uq_kobo_reading_state_user_book'),
    )


class KoboBookmark(Base):
    __tablename__ = 'kobo_bookmark'

    id = Column(Integer, primary_key=True)
    kobo_reading_state_id = Column(Integer, ForeignKey('kobo_reading_state.id'))
    created_at = Column(DateTime)
    last_modified = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    location_source = Column(String)
    location_type = Column(String)
    location_value = Column(String)
    progress_percent = Column(Float)
    content_source_progress_percent = Column(Float)


class KoboStatistics(Base):
    __tablename__ = 'kobo_statistics'

    id = Column(Integer, primary_key=True)
    kobo_reading_state_id = Column(Integer, ForeignKey('kobo_reading_state.id'))
    last_modified = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    remaining_time_minutes = Column(Integer)
    spent_reading_minutes = Column(Integer)


class Device(Base):
    """User-visible device; raw hardware identifiers are never stored."""
    __tablename__ = 'device'

    id = Column(Integer, primary_key=True, autoincrement=True)
    public_id = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    kind = Column(String(32), nullable=False)
    display_name = Column(String(160), nullable=False)
    model = Column(String(160), nullable=True)
    platform = Column(String(80), nullable=True)
    firmware_version = Column(String(64), nullable=True)
    first_seen_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_seen_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_metadata_at = Column(DateTime, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(32), nullable=False, default="auto")

    identities = relationship("DeviceIdentity", back_populates="device", cascade="all, delete-orphan")
    __table_args__ = (
        Index('ix_device_user_active_last_seen', 'user_id', 'active', 'last_seen_at'),
        Index('ix_device_user_display_name', 'user_id', 'display_name'),
    )


class DeviceIdentity(Base):
    """Versioned, keyed derivation of an upstream device identifier."""
    __tablename__ = 'device_identity'

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(Integer, ForeignKey('device.id', ondelete='CASCADE'), nullable=False)
    scheme = Column(String(64), nullable=False)
    key_version = Column(Integer, nullable=False)
    fingerprint = Column(String(64), nullable=False)
    first_seen_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_seen_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    device = relationship("Device", back_populates="identities")
    __table_args__ = (
        UniqueConstraint('scheme', 'key_version', 'fingerprint',
                         name='uq_device_identity_scheme_version_fingerprint'),
        Index('ix_device_identity_device', 'device_id'),
    )


class AnnotationContentIdMigration(Base):
    """Exact undo journal for conservative content-id backfills."""
    __tablename__ = 'annotation_content_id_migration'

    id = Column(Integer, primary_key=True, autoincrement=True)
    annotation_row_id = Column(Integer, ForeignKey('annotation.id', ondelete='CASCADE'), nullable=False, unique=True)
    original_content_id = Column(Text, nullable=False)
    normalized_content_id = Column(Text, nullable=False)
    migrated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class Annotation(Base):
    """Per-user-per-annotation row. Canonical store for ALL highlight/note
    origins (Kobo device, web reader, KOReader plugin).

    Per-target sync state (Hardcover, future Readwise / Notion / etc.) lives
    in the AnnotationSyncTarget table — one row per (annotation, target).

    Renamed from KoboAnnotationSync as of 2026-05-21 — the table is no
    longer Kobo-specific. See
    notes/2026-05-21-annotation-decouple-source-target-DESIGN.md.
    """
    __tablename__ = 'annotation'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False)
    annotation_id = Column(String, nullable=False)
    book_id = Column(Integer, nullable=False)
    # Origin tracking — where this annotation was created.
    source = Column(String, nullable=True)
    # Content
    highlighted_text = Column(String, nullable=True)
    highlight_color = Column(String, nullable=True)
    note_text = Column(String, nullable=True)
    # Position (Kobo-native; cfi_range is the canonical web-reader form).
    content_id = Column(String, nullable=True)
    start_container_path = Column(Text, nullable=True)
    start_container_child_index = Column(Integer, nullable=True)
    start_offset = Column(Integer, nullable=True)
    end_container_path = Column(Text, nullable=True)
    end_container_child_index = Column(Integer, nullable=True)
    end_offset = Column(Integer, nullable=True)
    context_string = Column(Text, nullable=True)
    chapter_progress = Column(Float, nullable=True)
    cfi_range = Column(String, nullable=True)
    # Sub-project (3)/(4) — polymorphic position support for non-CFI formats.
    # position_type values: 'cfi' (default for EPUB), 'pdf_quad', 'comic_page',
    # 'koreader_xpointer', and 'unanchored'.
    # NULL on legacy rows means EPUB CFI (backward compatible) — which is exactly
    # why 'unanchored' has to be an explicit non-NULL value: absence is already
    # taken, so a note with no anchor cannot be expressed by leaving this empty.
    # It would be resolved as a CFI instead. See #325.
    position_type = Column(String, nullable=True)
    pdf_page = Column(Integer, nullable=True)         # 1-indexed PDF page number
    pdf_quad_json = Column(Text, nullable=True)       # JSON: [[x,y,w,h], ...] in PDF user-space coords
    comic_page = Column(Integer, nullable=True)       # 1-indexed comic page (CBR/CBZ)
    # KOReader's native reflowable locator.  This is intentionally kept
    # separate from EPUB CFI: KOReader xpointers are engine-private and are
    # not safe to present to epub.js as CFIs.
    start_xpointer = Column(Text, nullable=True)
    end_xpointer = Column(Text, nullable=True)
    # Phase 2 (KOReader bridge) — opaque per-device id of the row a device last
    # wrote/saw for this annotation (e.g. the KoboReader.sqlite Bookmark.BookmarkID
    # the plugin created). Lets the plugin dedup + suppress feedback loops without
    # the server knowing the device kind. NULL until a device materializes the row.
    device_origin_id = Column(String, nullable=True)
    # Lifecycle
    hidden = Column(Boolean, default=False, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Device-supplied modification clock. This is distinct from last_synced,
    # which remains the server's receipt/dispatch time.
    client_modified_at = Column(DateTime, nullable=True)
    origin_device_id = Column(Integer, ForeignKey('device.id', ondelete='SET NULL'), nullable=True)
    assigned_device_id = Column(Integer, ForeignKey('device.id', ondelete='SET NULL'), nullable=True)
    routing_revision = Column(Integer, nullable=False, default=1)
    # Stage 0 two-way-sync metadata.  Existing parsed position/content columns
    # remain the web reader's representation; these fields are additive.
    annotation_type = Column(String(32), nullable=True)
    content_revision = Column(Integer, nullable=False, default=1)
    server_modified_at = Column(DateTime, nullable=True)
    last_editor_device_id = Column(
        Integer, ForeignKey('device.id', ondelete='SET NULL'), nullable=True,
    )
    last_synced = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    sync_targets = relationship(
        "AnnotationSyncTarget",
        backref="annotation",
        cascade="all, delete-orphan",
        lazy="select",
        passive_deletes=True,
    )
    kobo_materialization = relationship(
        "KoboAnnotationMaterialization", back_populates="annotation",
        cascade="all, delete-orphan", uselist=False, single_parent=True,
    )

    __table_args__ = (
        Index('ix_annotation_user_annotation', 'user_id', 'annotation_id'),
        Index('ix_annotation_user_book', 'user_id', 'book_id'),
        UniqueConstraint(
            'user_id', 'book_id', 'annotation_id',
            name='uq_annotation_user_book_annotation',
        ),
    )

    _VALID_SOURCES = {"kobo", "webreader", "koreader"}
    _VALID_POSITION_TYPES = {
        "cfi", "pdf_quad", "comic_page", "koreader_xpointer", "unanchored",
    }

    @validates("source")
    def _validate_source(self, _key, value):
        if value is not None and value not in self._VALID_SOURCES:
            raise ValueError(
                f"invalid annotation source: {value!r}; "
                f"expected one of {sorted(self._VALID_SOURCES)} or None"
            )
        return value

    @validates("position_type")
    def _validate_position_type(self, _key, value):
        if value is not None and value not in self._VALID_POSITION_TYPES:
            raise ValueError(
                f"invalid position_type: {value!r}; "
                f"expected one of {sorted(self._VALID_POSITION_TYPES)} or None"
            )
        return value

    def sync_target(self, target_name):
        """Return the AnnotationSyncTarget row for a specific target, or None."""
        for st in self.sync_targets:
            if st.target == target_name:
                return st
        return None

    def is_synced_to(self, target_name):
        """True iff there's a sync_target row for `target_name` with status='synced'."""
        st = self.sync_target(target_name)
        return st is not None and st.status == "synced"

    def __repr__(self):
        return f'<Annotation annotation_id={self.annotation_id} book_id={self.book_id}>'


class KoboAnnotationMaterialization(Base):
    """Byte-exact Kobo replay evidence for one generic annotation row."""
    __tablename__ = 'kobo_annotation_materialization'

    id = Column(Integer, primary_key=True, autoincrement=True)
    annotation_id = Column(
        Integer, ForeignKey('annotation.id', ondelete='CASCADE'),
        nullable=False, unique=True,
    )
    raw_annotation_json = Column(BLOB, nullable=False)
    raw_location_json = Column(BLOB, nullable=False)
    raw_client_modified_utc = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    materialization_revision = Column(Integer, nullable=False, default=1)
    provenance = Column(String(24), nullable=False)
    attachments_state = Column(String(16), nullable=False)
    serveable = Column(Boolean, nullable=False, default=False)
    quarantine_reason = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    annotation = relationship("Annotation", back_populates="kobo_materialization")

    __table_args__ = (
        CheckConstraint(
            "provenance IN ('kobo_cloud_seed', 'kobo_patch', 'cwng_authored')",
            name='ck_kam_provenance',
        ),
        CheckConstraint(
            "attachments_state IN ('missing', 'empty', 'nonempty', 'invalid')",
            name='ck_kam_attachments_state',
        ),
        Index('ix_kam_serveable', 'annotation_id', 'serveable'),
    )


class KoboAnnotationBookState(Base):
    """Completeness and authoring-safety state for one user's Kobo book."""
    __tablename__ = 'kobo_annotation_book_state'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    book_id = Column(Integer, nullable=False)
    content_id = Column(String(64), nullable=False)
    authority_status = Column(String(24), nullable=False, default='unseeded')
    authority_revision = Column(Integer, nullable=False, default=0)
    generation_id = Column(String(36), nullable=False, default=lambda: str(uuid.uuid4()))
    set_digest = Column(String(64), nullable=True)
    current_etag = Column(Text, nullable=True)
    etag_kind = Column(String(24), nullable=True)
    upstream_seed_etag = Column(Text, nullable=True)
    opaque_content_status = Column(String(16), nullable=False, default='unknown')
    opaque_content_source = Column(String(32), nullable=True)
    opaque_content_checked_at = Column(DateTime, nullable=True)
    seeded_at = Column(DateTime, nullable=True)
    last_mutation_at = Column(DateTime, nullable=True)
    quarantine_reason = Column(String(64), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    user = relationship("User", back_populates="kobo_annotation_book_states")
    device_states = relationship(
        "KoboDeviceBookAnnotationState", back_populates="book_state",
        cascade="all, delete-orphan",
    )
    seed_captures = relationship(
        "KoboAnnotationSeedCapture", back_populates="book_state",
        cascade="all, delete-orphan",
    )
    page_snapshots = relationship(
        "KoboAnnotationPageSnapshot", back_populates="book_state",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint('user_id', 'book_id', name='uq_kabs_user_book'),
        UniqueConstraint('user_id', 'content_id', name='uq_kabs_user_content'),
        CheckConstraint(
            "authority_status IN ('unseeded', 'seeding', 'authoritative', "
            "'quarantined', 'disabled')", name='ck_kabs_authority_status',
        ),
        CheckConstraint(
            "etag_kind IS NULL OR etag_kind IN ('kobo_manifest', 'cwng_revision')",
            name='ck_kabs_etag_kind',
        ),
        CheckConstraint(
            "opaque_content_status IN ('unknown', 'absent', 'present')",
            name='ck_kabs_opaque_content_status',
        ),
        CheckConstraint(
            "opaque_content_source IS NULL OR opaque_content_source IN "
            "('device_db_audit', 'wire_attachments', 'wire_attachments_verified')",
            name='ck_kabs_opaque_content_source',
        ),
        Index('ix_kabs_user_content', 'user_id', 'content_id'),
        Index('ix_kabs_authority', 'user_id', 'authority_status'),
    )


class KoboOpaqueContentPresentGuard(Base):
    """Durable knowledge that opaque Kobo content was observed for a book.

    The separate record survives deletion/reinsertion of mutable authority
    state, so raw SQL cannot turn ``present`` into ``absent`` by replacing the
    row.  Explicit user/book purges remove this evidence with the rest of that
    scope's data.
    """
    __tablename__ = 'kobo_opaque_content_present_guard'

    user_id = Column(
        Integer, ForeignKey('user.id', ondelete='CASCADE'), primary_key=True,
    )
    book_id = Column(Integer, primary_key=True)
    first_observed_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
    )
    user = relationship("User", back_populates="kobo_opaque_present_guards")


class KoboDeviceBookAnnotationState(Base):
    """Per-device delivery and later ETag-acknowledgment evidence."""
    __tablename__ = 'kobo_device_book_annotation_state'

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(Integer, ForeignKey('device.id', ondelete='CASCADE'), nullable=False)
    book_state_id = Column(
        Integer, ForeignKey('kobo_annotation_book_state.id', ondelete='CASCADE'), nullable=False,
    )
    last_declared_etag = Column(Text, nullable=True)
    last_declared_at = Column(DateTime, nullable=True)
    last_served_revision = Column(Integer, nullable=True)
    last_served_etag = Column(Text, nullable=True)
    last_ack_revision = Column(Integer, nullable=True)
    last_ack_at = Column(DateTime, nullable=True)
    book_state = relationship("KoboAnnotationBookState", back_populates="device_states")

    __table_args__ = (
        UniqueConstraint('device_id', 'book_state_id', name='uq_kdbas_device_book'),
        Index('ix_kdbas_book_ack', 'book_state_id', 'last_ack_revision'),
    )


class KoboAnnotationSeedCapture(Base):
    __tablename__ = 'kobo_annotation_seed_capture'

    id = Column(Integer, primary_key=True, autoincrement=True)
    book_state_id = Column(
        Integer, ForeignKey('kobo_annotation_book_state.id', ondelete='CASCADE'), nullable=False,
    )
    device_id = Column(Integer, ForeignKey('device.id', ondelete='SET NULL'), nullable=True)
    started_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    device_etag = Column(Text, nullable=True)
    upstream_etag = Column(Text, nullable=True)
    response_sha256 = Column(String(64), nullable=True)
    annotation_count = Column(Integer, nullable=True)
    page_count = Column(Integer, nullable=True)
    result = Column(String(24), nullable=False, default='pending')
    failure_reason = Column(String(64), nullable=True)
    book_state = relationship("KoboAnnotationBookState", back_populates="seed_captures")
    pages = relationship(
        "KoboAnnotationSeedCapturePage", back_populates="seed_capture",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("result IN ('pending', 'accepted', 'rejected', 'failed')",
                        name='ck_kasc_result'),
        Index('ix_kasc_book_time', 'book_state_id', 'started_at'),
    )


class KoboAnnotationSeedCapturePage(Base):
    __tablename__ = 'kobo_annotation_seed_capture_page'

    id = Column(Integer, primary_key=True, autoincrement=True)
    seed_capture_id = Column(
        Integer, ForeignKey('kobo_annotation_seed_capture.id', ondelete='CASCADE'), nullable=False,
    )
    page_number = Column(Integer, nullable=False)
    request_offset_token = Column(Text, nullable=True)
    response_body_gzip = Column(BLOB, nullable=False)
    response_sha256 = Column(String(64), nullable=False)
    response_etag = Column(Text, nullable=True)
    next_offset_token = Column(Text, nullable=True)
    seed_capture = relationship("KoboAnnotationSeedCapture", back_populates="pages")

    __table_args__ = (
        UniqueConstraint('seed_capture_id', 'page_number', name='uq_kascp_capture_page'),
        Index('ix_kascp_capture', 'seed_capture_id', 'page_number'),
    )


class KoboAnnotationPageSnapshot(Base):
    __tablename__ = 'kobo_annotation_page_snapshot'

    snapshot_id = Column(String(64), primary_key=True)
    book_state_id = Column(
        Integer, ForeignKey('kobo_annotation_book_state.id', ondelete='CASCADE'), nullable=False,
    )
    device_id = Column(Integer, ForeignKey('device.id', ondelete='CASCADE'), nullable=True)
    authority_revision = Column(Integer, nullable=False)
    etag = Column(Text, nullable=False)
    ordered_payload_gzip = Column(BLOB, nullable=False)
    annotation_count = Column(Integer, nullable=False)
    page_size = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=False)
    book_state = relationship("KoboAnnotationBookState", back_populates="page_snapshots")
    cursors = relationship(
        "KoboAnnotationPageCursor", back_populates="snapshot",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index('ix_kaps_expiry', 'expires_at'),)


class KoboAnnotationPageCursor(Base):
    __tablename__ = 'kobo_annotation_page_cursor'

    token = Column(String(64), primary_key=True)
    snapshot_id = Column(
        String(64), ForeignKey('kobo_annotation_page_snapshot.snapshot_id', ondelete='CASCADE'),
        nullable=False,
    )
    page_offset = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    snapshot = relationship("KoboAnnotationPageSnapshot", back_populates="cursors")

    __table_args__ = (
        UniqueConstraint('snapshot_id', 'page_offset', name='uq_kapc_snapshot_offset'),
        Index('ix_kapc_snapshot', 'snapshot_id'),
    )


_KOBO_OPAQUE_GUARD_TRIGGER_DDL = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_kabs_opaque_present_sticky
    BEFORE UPDATE OF opaque_content_status ON kobo_annotation_book_state
    WHEN OLD.opaque_content_status = 'present'
         AND NEW.opaque_content_status <> 'present'
    BEGIN
      SELECT RAISE(ABORT, 'opaque_content_status present is sticky');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_kabs_opaque_present_guard_insert
    BEFORE INSERT ON kobo_annotation_book_state
    WHEN NEW.opaque_content_status <> 'present'
         AND EXISTS (
           SELECT 1 FROM kobo_opaque_content_present_guard g
           WHERE g.user_id = NEW.user_id AND g.book_id = NEW.book_id
         )
    BEGIN
      SELECT RAISE(ABORT, 'opaque_content_status present is sticky');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_kabs_opaque_present_record_insert
    AFTER INSERT ON kobo_annotation_book_state
    WHEN NEW.opaque_content_status = 'present'
    BEGIN
      INSERT OR IGNORE INTO kobo_opaque_content_present_guard
        (user_id, book_id, first_observed_at)
      VALUES (NEW.user_id, NEW.book_id, CURRENT_TIMESTAMP);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_kabs_opaque_present_record_update
    AFTER UPDATE OF opaque_content_status ON kobo_annotation_book_state
    WHEN NEW.opaque_content_status = 'present'
    BEGIN
      INSERT OR IGNORE INTO kobo_opaque_content_present_guard
        (user_id, book_id, first_observed_at)
      VALUES (NEW.user_id, NEW.book_id, CURRENT_TIMESTAMP);
    END
    """,
)

# SQLAlchemy create_all is the fresh-install path and normally has no concept
# of SQLite triggers.  Attach the guards to table creation so first boot has
# the same DB-boundary behavior as an upgraded database.
for _trigger_ddl in _KOBO_OPAQUE_GUARD_TRIGGER_DDL:
    event.listen(
        KoboAnnotationBookState.__table__, "after_create", DDL(_trigger_ddl),
    )


class AnnotationDeviceState(Base):
    """Per-device delivery intent/telemetry; reassignment never deletes it."""
    __tablename__ = 'annotation_device_state'

    id = Column(Integer, primary_key=True, autoincrement=True)
    annotation_id = Column(Integer, ForeignKey('annotation.id', ondelete='CASCADE'), nullable=False)
    device_id = Column(Integer, ForeignKey('device.id', ondelete='CASCADE'), nullable=False)
    native_annotation_id = Column(String, nullable=True)
    desired = Column(Boolean, nullable=False, default=False)
    delivery_status = Column(String(32), nullable=False, default='pending')
    first_seen_revision = Column(Integer, nullable=True)
    last_delivered_revision = Column(Integer, nullable=True)
    last_ack_revision = Column(Integer, nullable=True)
    last_seen_present_at = Column(DateTime, nullable=True)
    content_fingerprint = Column(String(64), nullable=True)
    native_metadata_json = Column(Text, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint('annotation_id', 'device_id', name='uq_annotation_device_state'),
        Index('ix_annotation_device_state_device_desired', 'device_id', 'desired'),
    )


class DeviceRetiredAssignment(Base):
    """Undo snapshot for assignments cleared by a device soft-delete."""
    __tablename__ = 'device_retired_assignment'

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(Integer, ForeignKey('device.id', ondelete='CASCADE'), nullable=False)
    annotation_id = Column(Integer, ForeignKey('annotation.id', ondelete='CASCADE'), nullable=False)
    retired_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        UniqueConstraint('device_id', 'annotation_id', name='uq_device_retired_assignment'),
    )


class AnnotationSyncTarget(Base):
    """Per-(annotation, target) row tracking sync state to a single remote
    destination (Hardcover today; Readwise / Notion / etc. later).

    Status state machine: pending -> synced/failed -> tombstone. See
    notes/2026-05-21-annotation-decouple-source-target-DESIGN.md.
    """
    __tablename__ = 'annotation_sync_target'

    id = Column(Integer, primary_key=True, autoincrement=True)
    annotation_id = Column(
        Integer,
        ForeignKey('annotation.id', ondelete='CASCADE'),
        nullable=False,
    )
    target = Column(String, nullable=False)
    target_record_id = Column(String, nullable=True)
    status = Column(String, nullable=False)
    error_message = Column(Text, nullable=True)
    last_attempt = Column(DateTime, nullable=True)
    last_synced = Column(DateTime, nullable=True)
    created_at = Column(
        DateTime, nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime, nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint('annotation_id', 'target', name='uq_ast_annotation_target'),
        Index('ix_ast_target_status', 'target', 'status'),
    )

    def __repr__(self):
        return (f'<AnnotationSyncTarget annotation_id={self.annotation_id} '
                f'target={self.target} status={self.status}>')


class KoboAnnotationBackup(Base):
    """Per-`(user_id, book_id)` index of gzipped annotation snapshots
    on disk. See ``cps/services/annotation_backup.py`` and fork #240.

    The actual annotation payload lives in
    ``/config/annotation-backups/<user_id>/<book_id>/<UTC-iso>.json.gz``
    — this table just indexes them so retention queries
    (``ORDER BY created_at DESC LIMIT 3``) hit an index, not the
    filesystem.
    """
    __tablename__ = 'kobo_annotation_backup'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False)
    book_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    content_hash = Column(String(64), nullable=False)
    file_path = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False, default=0)
    annotation_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index('ix_kobo_annotation_backup_user_book_created',
              'user_id', 'book_id', 'created_at'),
    )

    def __repr__(self):
        return (f'<KoboAnnotationBackup user_id={self.user_id} '
                f'book_id={self.book_id} created_at={self.created_at}>')


class HardcoverBookBlacklist(Base):
    """Track book-level blacklisting for hardcover sync features."""
    __tablename__ = 'hardcover_book_blacklist'

    id = Column(Integer, primary_key=True, autoincrement=True)
    book_id = Column(Integer, nullable=False, unique=True)  # Calibre book ID
    blacklist_annotations = Column(Boolean, default=False)  # Block annotation syncing
    blacklist_reading_progress = Column(Boolean, default=False)  # Block reading progress syncing

    def __repr__(self):
        return f'<HardcoverBookBlacklist book_id={self.book_id} annotations={self.blacklist_annotations} progress={self.blacklist_reading_progress}>'


class HardcoverMatchQueue(Base):
    """Queue for ambiguous Hardcover metadata matches requiring manual review."""
    __tablename__ = 'hardcover_match_queue'

    id = Column(Integer, primary_key=True, autoincrement=True)
    book_id = Column(Integer, nullable=False)
    book_title = Column(String, nullable=False)
    book_authors = Column(String, nullable=False)
    search_query = Column(String, nullable=False)
    hardcover_results = Column(String, nullable=False)  # JSON array of MetaRecord candidates
    confidence_scores = Column(String, nullable=False)  # JSON array of [score, reason] tuples
    created_at = Column(String, nullable=False)
    reviewed = Column(Integer, default=0, nullable=False)  # 0=pending, 1=reviewed
    selected_result_id = Column(String, default=None)  # Hardcover ID if manually selected
    review_action = Column(String, default=None)  # 'accept', 'reject', 'skip'
    reviewed_at = Column(String, default=None)
    reviewed_by = Column(String, default=None)

    def __repr__(self):
        return f'<HardcoverMatchQueue book_id={self.book_id} title="{self.book_title}" reviewed={bool(self.reviewed)}>'


# Updates the last_modified timestamp in the KoboReadingState table if any of its children tables are modified,
# and adds a KoboBookmark's created_at the first time it records progress > 0 ("started reading").
@event.listens_for(Session, 'before_flush')
def receive_before_flush(session, flush_context, instances):
    # Computed lazily on the first Kobo-related change so unrelated flushes don't
    # pay for a timestamp they never use; reused across the flush once set.
    ts = None
    for change in itertools.chain(session.new, session.dirty):
        if isinstance(change, (ReadBook, KoboStatistics, KoboBookmark)):
            if ts is None:
                ts = (g.kobo_reading_state_lm
                      if has_request_context() and getattr(g, 'kobo_reading_state_lm', None)
                      else datetime.now(timezone.utc))
            if change.kobo_reading_state:
                change.kobo_reading_state.last_modified = ts
                change.kobo_reading_state.priority_timestamp = ts
            if isinstance(change, KoboBookmark) and not change.created_at and (change.progress_percent or 0) > 0:
                change.created_at = ts

    # Maintain the last_modified_bit for the Shelf table.
    for change in itertools.chain(session.new, session.deleted):
        if isinstance(change, BookShelf):
            change.ub_shelf.last_modified = datetime.now(timezone.utc)


# Annotation backup safety net (fork #240): every INSERT or UPDATE to
# ``KoboAnnotationSync`` schedules a per-`(user, book)` snapshot on the
# background worker. Lives here so it captures every writer (Hardcover
# sync, the H1 import endpoint, the web-reader create path) without
# each caller having to remember.
#
# Two-phase wiring: ``after_flush`` collects keys onto a per-session
# attribute, ``after_commit`` dispatches them to the worker. This avoids
# the race where the worker thread queries before the source thread's
# commit lands. ``after_rollback`` drops the pending set so a rolled-back
# transaction doesn't trigger a phantom backup.
@event.listens_for(Session, 'after_flush')
def _collect_annotation_writes_for_backup(session, flush_context):
    try:
        from .services.annotation_backup import collect_annotation_writes
        collect_annotation_writes(session, flush_context)
    except Exception as e:
        log.error("annotation_backup collect hook failed: %s", e)


@event.listens_for(Session, 'after_commit')
def _dispatch_annotation_backup_writes(session):
    try:
        from .services.annotation_backup import dispatch_pending_writes
        dispatch_pending_writes(session)
    except Exception as e:
        log.error("annotation_backup dispatch hook failed: %s", e)


@event.listens_for(Session, 'after_rollback')
def _discard_annotation_backup_writes(session):
    try:
        from .services.annotation_backup import discard_pending_writes
        discard_pending_writes(session)
    except Exception as e:
        log.error("annotation_backup rollback hook failed: %s", e)


# Baseclass representing Downloads from calibre-web in app.db
class Downloads(Base):
    __tablename__ = 'downloads'

    id = Column(Integer, primary_key=True)
    book_id = Column(Integer)
    user_id = Column(Integer, ForeignKey('user.id'))

    def __repr__(self):
        return '<Download %r' % self.book_id


# Baseclass representing allowed domains for registration
class Registration(Base):
    __tablename__ = 'registration'

    id = Column(Integer, primary_key=True)
    domain = Column(String)
    allow = Column(Integer)

    def __repr__(self):
        return "<Registration('{0}')>".format(self.domain)


class RemoteAuthToken(Base):
    __tablename__ = 'remote_auth_token'

    id = Column(Integer, primary_key=True)
    auth_token = Column(String, unique=True)
    user_id = Column(Integer, ForeignKey('user.id'))
    verified = Column(Boolean, default=False)
    expiration = Column(DateTime)
    token_type = Column(Integer, default=0)

    def __init__(self):
        super().__init__()
        self.auth_token = (hexlify(os.urandom(16))).decode('utf-8')
        self.expiration = datetime.now() + timedelta(minutes=10)  # 10 min from now

    def __repr__(self):
        return '<Token %r>' % self.id


def filename(context):
    """Generate deterministic filename for thumbnails.

    Prefer the pattern:
        cover thumbnails:  book_<entity_id>_r<resolution>.<ext>
        series thumbnails: series_<entity_id>_r<resolution>.<ext>

    Fallback to legacy uuid-based naming if required fields are missing.
    This keeps previously generated files valid while making new ones easier
    to reason about and purge selectively.
    """
    params = context.get_current_parameters()
    file_format = params.get('format', 'jpeg')
    entity_id = params.get('entity_id')
    resolution = params.get('resolution')
    thumb_type = params.get('type')  # cover or series
    uuid_val = params.get('uuid')

    # map format 'jpeg' -> extension jpg
    if file_format == 'jpeg':
        ext = 'jpg'
    else:
        ext = file_format

    try:
        if entity_id is not None and resolution is not None and thumb_type is not None:
            if thumb_type == constants.THUMBNAIL_TYPE_COVER:
                return f"book_{entity_id}_r{resolution}.{ext}"
            elif thumb_type == constants.THUMBNAIL_TYPE_SERIES:
                return f"series_{entity_id}_r{resolution}.{ext}"
    except Exception:
        # fall back to uuid naming if anything unexpected occurs
        pass

    # legacy fallback
    return f"{uuid_val}.{ext}" if uuid_val else f"legacy_unknown.{ext}"


class Thumbnail(Base):
    __tablename__ = 'thumbnail'

    id = Column(Integer, primary_key=True)
    entity_id = Column(Integer)
    uuid = Column(String, default=lambda: str(uuid.uuid4()), unique=True)
    format = Column(String, default='jpeg')
    type = Column(SmallInteger, default=constants.THUMBNAIL_TYPE_COVER)
    resolution = Column(SmallInteger, default=constants.COVER_THUMBNAIL_SMALL)
    filename = Column(String, default=filename)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expiration = Column(DateTime, nullable=True)


# Add missing tables during migration of database
def add_missing_tables(engine, _session):
    # Local import: progress_syncing.models imports Base from this module, so a
    # module-level import would be circular. Every other table below is defined
    # in this file; this one is not, and referencing it as a bare global raised
    # NameError on any app.db missing kosync_progress (a fresh install).
    from .progress_syncing.models import KOSyncProgress

    if not engine.dialect.has_table(engine.connect(), "archived_book"):
        ArchivedBook.__table__.create(bind=engine, checkfirst=True)
    if not engine.dialect.has_table(engine.connect(), "thumbnail"):
        Thumbnail.__table__.create(bind=engine, checkfirst=True)
    if not engine.dialect.has_table(engine.connect(), "kosync_progress"):
        KOSyncProgress.__table__.create(bind=engine, checkfirst=True)
    if not engine.dialect.has_table(engine.connect(), "magic_shelf"):
        MagicShelf.__table__.create(bind=engine, checkfirst=True)
    if not engine.dialect.has_table(engine.connect(), "magic_shelf_cache"):
        MagicShelfCache.__table__.create(bind=engine, checkfirst=True)
    if not engine.dialect.has_table(engine.connect(), "opds_shelf_exposure"):
        OpdsShelfExposure.__table__.create(bind=engine, checkfirst=True)
    if not engine.dialect.has_table(engine.connect(), "book_original_filename"):
        BookOriginalFilename.__table__.create(bind=engine, checkfirst=True)
    if not engine.dialect.has_table(engine.connect(), "opds_magic_shelf_exposure"):
        OpdsMagicShelfExposure.__table__.create(bind=engine, checkfirst=True)
    if not engine.dialect.has_table(engine.connect(), "hidden_magic_shelf_templates"):
        HiddenMagicShelfTemplate.__table__.create(bind=engine, checkfirst=True)
    if not engine.dialect.has_table(engine.connect(), "kobo_annotation_backup"):
        KoboAnnotationBackup.__table__.create(bind=engine, checkfirst=True)
    if not engine.dialect.has_table(engine.connect(), "favorite_book"):
        FavoriteBook.__table__.create(bind=engine, checkfirst=True)


# migrate all settings missing in registration table
def migrate_registration_table(engine, _session):
    try:
        # Handle table exists, but no content
        cnt = _session.query(Registration).count()
        if not cnt:
            with engine.connect() as conn:
                trans = conn.begin()
                conn.execute(text("insert into registration (domain, allow) values('%.%',1)"))
                trans.commit()
    except exc.OperationalError:  # Database is not writeable
        print('Settings database is not writeable. Exiting...')
        sys.exit(2)


def migrate_user_session_table(engine, _session):
    try:
        _session.query(exists().where(User_Sessions.random)).scalar()
        _session.commit()
    except exc.OperationalError:  # Database is not compatible, some columns are missing
        _safe_session_rollback(_session, "user_session")
        _run_ddl_with_retry(
            engine,
            [
                "ALTER TABLE user_session ADD column 'random' String",
                "ALTER TABLE user_session ADD column 'expiry' Integer",
            ],
        )


def migrate_user_hardcover_token_constraint(engine):
    """Remove the fresh-install-only UNIQUE constraint on Hardcover tokens.

    Older upgrades received this column through ``ALTER TABLE`` without a
    constraint. Fresh databases created from the former model instead have a
    SQLite autoindex, which can only be removed by rebuilding the table.
    """
    connection = engine.raw_connection()
    cursor = connection.cursor()
    foreign_keys_enabled = False
    try:
        cursor.execute("PRAGMA busy_timeout=5000")
        foreign_keys_enabled = bool(cursor.execute("PRAGMA foreign_keys").fetchone()[0])

        unique_token_indexes = set()
        token_autoindex_present = False
        for index_row in cursor.execute("PRAGMA index_list('user')").fetchall():
            # index_list columns: seq, name, unique, origin, partial
            if not index_row[2]:
                continue
            index_name = index_row[1].replace('"', '""')
            indexed_columns = [
                row[2]
                for row in cursor.execute(
                    f'PRAGMA index_info("{index_name}")'
                ).fetchall()
            ]
            if indexed_columns == ["hardcover_token"]:
                unique_token_indexes.add(index_row[1])
                token_autoindex_present = token_autoindex_present or index_row[3] != "c"
        if not unique_token_indexes:
            return False

        table_sql_row = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='user'"
        ).fetchone()
        if not table_sql_row or not table_sql_row[0]:
            raise RuntimeError("Could not read CREATE TABLE SQL for user")
        table_sql = table_sql_row[0]

        replacement_sql, inline_replacements = re.subn(
            r'(?i)(["`\[]?hardcover_token["`\]]?\s+[^,]*?)\s+UNIQUE\b',
            r'\1',
            table_sql,
            count=1,
        )
        replacement_sql, table_replacements = re.subn(
            r'(?i),\s*(?:CONSTRAINT\s+["`\[]?\w+["`\]]?\s+)?UNIQUE\s*'
            r'\(\s*["`\[]?hardcover_token["`\]]?\s*\)',
            '',
            replacement_sql,
            count=1,
        )
        if token_autoindex_present and inline_replacements + table_replacements != 1:
            raise RuntimeError(
                "Detected an automatic unique hardcover_token index but could "
                "not safely remove its UNIQUE constraint"
            )
        replacement_sql, create_table_replacements = re.subn(
            r'(?i)^(\s*CREATE\s+TABLE\s+)["`\[]?user["`\]]?',
            r'\1user_new',
            replacement_sql,
            count=1,
        )
        if create_table_replacements != 1:
            raise RuntimeError("Could not safely rename user table in CREATE SQL")

        columns = [row[1] for row in cursor.execute("PRAGMA table_info('user')").fetchall()]
        if not columns:
            raise RuntimeError("Cannot rebuild user table without columns")
        quoted_columns = ", ".join(
            '"{}"'.format(column.replace('"', '""')) for column in columns
        )
        schema_objects = [
            row
            for row in cursor.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE tbl_name='user' AND type IN ('index', 'trigger') "
                "AND sql IS NOT NULL ORDER BY type, name"
            ).fetchall()
            if row[1] not in unique_token_indexes
        ]
        row_count_before = cursor.execute("SELECT COUNT(*) FROM user").fetchone()[0]
        foreign_key_violations_before = cursor.execute("PRAGMA foreign_key_check").fetchall()

        # PRAGMA foreign_keys is a no-op inside a transaction, so set it first.
        connection.commit()
        cursor.execute("PRAGMA foreign_keys=OFF")
        connection.commit()
        cursor.execute("BEGIN IMMEDIATE")
        if cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_new'"
        ).fetchone():
            raise RuntimeError("Refusing to overwrite pre-existing user_new table")
        cursor.execute(replacement_sql)
        cursor.execute(
            f"INSERT INTO user_new ({quoted_columns}) "
            f"SELECT {quoted_columns} FROM user"
        )
        row_count_copied = cursor.execute("SELECT COUNT(*) FROM user_new").fetchone()[0]
        if row_count_copied != row_count_before:
            raise RuntimeError(
                f"User-table rebuild copied {row_count_copied} of {row_count_before} rows"
            )
        cursor.execute("DROP TABLE user")
        cursor.execute("ALTER TABLE user_new RENAME TO user")
        for _object_type, _name, object_sql in schema_objects:
            cursor.execute(object_sql)

        row_count_after = cursor.execute("SELECT COUNT(*) FROM user").fetchone()[0]
        if row_count_after != row_count_before:
            raise RuntimeError(
                f"User-table rebuild retained {row_count_after} of {row_count_before} rows"
            )
        foreign_key_violations_after = cursor.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_violations_after != foreign_key_violations_before:
            raise RuntimeError("User-table rebuild changed foreign-key integrity")
        for index_row in cursor.execute("PRAGMA index_list('user')").fetchall():
            if index_row[2]:
                index_name = index_row[1].replace('"', '""')
                indexed_columns = [
                    row[2]
                    for row in cursor.execute(
                        f'PRAGMA index_info("{index_name}")'
                    ).fetchall()
                ]
                if indexed_columns == ["hardcover_token"]:
                    raise RuntimeError("Unique hardcover_token index survived rebuild")
        connection.commit()
        log.info(
            "Removed unique Hardcover token constraint while preserving %d user row(s)",
            row_count_after,
        )
        return True
    except Exception:
        connection.rollback()
        log.exception("Failed to remove unique Hardcover token constraint; rebuild rolled back")
        raise
    finally:
        try:
            cursor.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys_enabled else 'OFF'}")
            connection.commit()
        finally:
            cursor.close()
            connection.close()


def migrate_user_table(engine, _session):
    _ensure_kobo_two_way_gate_columns(engine)
    try:
        _session.query(exists().where(User.hardcover_token)).scalar()
        _session.commit()
    except exc.OperationalError:  # Database is not compatible, some columns are missing
        _safe_session_rollback(_session, "user.hardcover_token")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'hardcover_token' String")

    migrate_user_hardcover_token_constraint(engine)

    try:
        _session.query(exists().where(User.opds_only_shelves_sync)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.opds_only_shelves_sync")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'opds_only_shelves_sync' Integer DEFAULT 0")
    # Migration for per-user theme column
    try:
        _session.query(exists().where(User.theme)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.theme")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'theme' Integer DEFAULT 0")

    # Migration for auto-send feature columns
    try:
        _session.query(exists().where(User.auto_send_enabled)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.auto_send_enabled")
        try:
            _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'auto_send_enabled' Boolean DEFAULT 0")
        except Exception as e:
            db_hint = app_DB_path or str(engine.url)
            log.error(
                "Failed to add auto_send_enabled column to user table in app.db (%s). "
                "Check file permissions, locks, and CALIBRE_DBPATH mapping. Error: %s",
                db_hint,
                e,
            )

    # Migration for per-user additional eReader email address permission
    try:
        _session.query(exists().where(User.allow_additional_ereader_emails)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.allow_additional_ereader_emails")
        try:
            _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'allow_additional_ereader_emails' Boolean DEFAULT 1")
        except Exception as e:
            db_hint = app_DB_path or str(engine.url)
            log.error(
                "Failed to add allow_additional_ereader_emails column to user table in app.db (%s). "
                "Check file permissions, locks, and CALIBRE_DBPATH mapping. Error: %s",
                db_hint,
                e,
            )

    # Migration to add per-user email subject for Kindle sending
    try:
        _session.query(exists().where(User.kindle_mail_subject)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.kindle_mail_subject")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'kindle_mail_subject' String DEFAULT ''")

    # Migration to enable duplicates sidebar for existing admin users (one-time)
    try:
        from . import constants
        SIDEBAR_DUPLICATES = constants.SIDEBAR_DUPLICATES

        migration_dir = os.path.join(constants.CONFIG_DIR, ".cwa_migrations")
        migration_marker = os.path.join(migration_dir, "duplicates_sidebar_v1")

        if not os.path.isfile(migration_marker):
            # Check if any admin users don't have duplicates enabled
            admin_users = _session.query(User).filter(
                User.role.op('&')(constants.ROLE_ADMIN) == constants.ROLE_ADMIN
            ).all()
            for user in admin_users:
                if not (user.sidebar_view & SIDEBAR_DUPLICATES):
                    user.sidebar_view |= SIDEBAR_DUPLICATES
                    print(f"[Migration] Enabled duplicates sidebar for admin user: {user.name}")

            _session.commit()
            try:
                os.makedirs(migration_dir, exist_ok=True)
                with open(migration_marker, "w", encoding="utf-8") as marker:
                    marker.write(datetime.now(timezone.utc).isoformat())
            except Exception as marker_error:
                print(
                    f"[Migration] Warning: Could not persist duplicates sidebar migration marker: {marker_error}",
                    flush=True,
                )
    except Exception as e:
        print(f"[Migration] Warning: Could not update duplicates sidebar setting: {e}")

    # Migration to enable favorites sidebar for existing users (one-time) — fork #27
    try:
        from . import constants
        SIDEBAR_FAVORITES = constants.SIDEBAR_FAVORITES

        migration_dir = os.path.join(constants.CONFIG_DIR, ".cwa_migrations")
        migration_marker = os.path.join(migration_dir, "favorites_sidebar_v1")

        if not os.path.isfile(migration_marker):
            # Favorites is a general per-user feature, so enable it for every
            # existing user (not just admins) — otherwise the new sidebar bit
            # would be off for accounts created before this release.
            for user in _session.query(User).all():
                if not (user.sidebar_view & SIDEBAR_FAVORITES):
                    user.sidebar_view |= SIDEBAR_FAVORITES
            _session.commit()
            try:
                os.makedirs(migration_dir, exist_ok=True)
                with open(migration_marker, "w", encoding="utf-8") as marker:
                    marker.write(datetime.now(timezone.utc).isoformat())
            except Exception as marker_error:
                print(
                    f"[Migration] Warning: Could not persist favorites sidebar migration marker: {marker_error}",
                    flush=True,
                )
    except Exception as e:
        print(f"[Migration] Warning: Could not update favorites sidebar setting: {e}")
        _session.rollback()

    # Migration for cover-preview per-user preference columns (Phase 2 of
    # cover-normalization — see notes/COVER-NORMALIZATION-DESIGN.md).
    # Existing users default to False on upgrade so the rollout is silent;
    # new users default True per the column-level default.
    try:
        _session.query(exists().where(User.show_ereader_previews)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.show_ereader_previews")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'show_ereader_previews' Boolean DEFAULT 1")
        try:
            updated = _session.query(User).update({User.show_ereader_previews: 0})
            _session.commit()
            print(f"[cover-preview-migration] Defaulted show_ereader_previews=0 for {updated} existing user(s) to preserve current view on upgrade.", flush=True)
        except Exception as e:
            print(f"[cover-preview-migration] Could not back-fill show_ereader_previews=0 for existing users: {e}", flush=True)
            _session.rollback()

    try:
        _session.query(exists().where(User.preview_preset)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.preview_preset")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'preview_preset' String DEFAULT 'kobo_libra_color'")

    try:
        _session.query(exists().where(User.preview_default_fill)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.preview_default_fill")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'preview_default_fill' String DEFAULT 'edge_mirror'")

    try:
        _session.query(exists().where(User.preview_default_color)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.preview_default_color")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'preview_default_color' String")

    # #701 — user-selectable UI font presets.
    try:
        _session.query(exists().where(User.ui_font_body)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.ui_font_body")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'ui_font_body' String DEFAULT ''")

    try:
        _session.query(exists().where(User.ui_font_display)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.ui_font_display")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'ui_font_display' String DEFAULT ''")

def migrate_oauth_provider_table(engine, _session):
    """Ensure every migration-managed column on oauthProvider exists.

    Instances upgraded across several releases can reach a *partial* column
    state — e.g. ``oauth_base_url`` present but ``oauth_authorize_url`` missing.
    Introspect the live schema and add only the columns that are actually
    missing, one ALTER per statement, so the migration repairs any partial
    state and stays idempotent across restarts (fork #354).

    The old probe-by-proxy form queried a single column as a stand-in for its
    whole group, so a partially-migrated DB passed the probe and the missing
    columns were never added — OAuth init then failed on the missing column
    and ``/admin/config`` returned 500.
    """
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(engine)
    if "oauthProvider" not in inspector.get_table_names():
        return  # table is created fresh from the model with every column present
    existing = {c["name"] for c in inspector.get_columns("oauthProvider")}
    managed_columns = [
        ("oauth_base_url", "String DEFAULT NULL"),
        ("oauth_authorize_url", "String DEFAULT NULL"),
        ("oauth_token_url", "String DEFAULT NULL"),
        ("oauth_userinfo_url", "String DEFAULT NULL"),
        ("oauth_admin_group", "String DEFAULT NULL"),
        ("oauth_group_claim", "String DEFAULT 'groups'"),
        ("oauth_allowed_groups", "String DEFAULT NULL"),
        ("oauth_require_group", "Boolean DEFAULT 0"),
        ("oauth_default_role", "SmallInteger DEFAULT NULL"),
        ("metadata_url", "String DEFAULT NULL"),
        ("scope", "String DEFAULT 'openid profile email'"),
        ("username_mapper", "String DEFAULT 'preferred_username'"),
        ("email_mapper", "String DEFAULT 'email'"),
        ("login_button", "String DEFAULT 'OpenID Connect'"),
    ]
    for col, coltype in managed_columns:
        if col not in existing:
            # One ALTER per call so a stray duplicate column can't roll back the
            # rest, and tolerate "duplicate column name" per column: a concurrent
            # boot (gunicorn pre-fork) or a column present-but-absent-from-snapshot
            # can make ADD COLUMN raise it. Without swallowing it here the error
            # propagates and aborts every remaining column for a full boot cycle
            # (fork #354 / PR #355 hardening). Treat it as already-applied.
            try:
                _run_ddl_with_retry(engine, "ALTER TABLE oauthProvider ADD column '{}' {}".format(col, coltype))
            except exc.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise


def migrate_config_table(engine, _session):
    """Migrate configuration table to add new authentication columns"""
    _ensure_kobo_two_way_gate_columns(engine)
    if not engine or not _session:
            _safe_session_rollback(_session, "settings.config_reverse_proxy_auto_create_users")
            _run_ddl_with_retry(
                engine,
                "ALTER TABLE settings ADD column 'config_reverse_proxy_auto_create_users' Boolean DEFAULT 0",
            )
    try:
        # Test if the new column exists
        _session.execute(text("SELECT config_oauth_redirect_host FROM settings LIMIT 1"))
        _session.commit()
    except exc.OperationalError:  # Column doesn't exist
        try:
            with engine.connect() as conn:
                trans = conn.begin()
                conn.execute(text("ALTER TABLE settings ADD column 'config_oauth_redirect_host' String DEFAULT ''"))
                trans.commit()
        except Exception as e:
            log.error("Failed to add config_oauth_redirect_host column: %s", e)
            # Don't raise - let CWA continue without this feature
            pass

    # Add reverse proxy auto-create users configuration
    try:
        # Test if the new column exists
        _session.execute(text("SELECT config_reverse_proxy_auto_create_users FROM settings LIMIT 1"))
        _session.commit()
    except exc.OperationalError:  # Column doesn't exist
        try:
            with engine.connect() as conn:
                trans = conn.begin()
                conn.execute(text("ALTER TABLE settings ADD column 'config_reverse_proxy_auto_create_users' Boolean DEFAULT 0"))
                trans.commit()
        except Exception as e:
            log.error("Failed to add config_reverse_proxy_auto_create_users column: %s", e)
            # Don't raise - let CWA continue without this feature
            pass

    # Fork #225 (@froggybottomboys): server-wide announcement banner.
    try:
        _session.execute(text("SELECT config_server_announcement FROM settings LIMIT 1"))
        _session.commit()
    except exc.OperationalError:  # Column doesn't exist
        try:
            _safe_session_rollback(_session, "settings.config_server_announcement")
            _run_ddl_with_retry(
                engine,
                "ALTER TABLE settings ADD column 'config_server_announcement' String DEFAULT ''",
            )
        except Exception as e:
            log.error("Failed to add config_server_announcement column: %s", e)
            pass

    # Fork #323 (@olskar): admin-set custom CSS injected site-wide.
    try:
        _session.execute(text("SELECT config_custom_css FROM settings LIMIT 1"))
        _session.commit()
    except exc.OperationalError:  # Column doesn't exist
        try:
            _safe_session_rollback(_session, "settings.config_custom_css")
            _run_ddl_with_retry(
                engine,
                "ALTER TABLE settings ADD column 'config_custom_css' String DEFAULT ''",
            )
        except Exception as e:
            log.error("Failed to add config_custom_css column: %s", e)
            pass

    # Add LDAP auto-create users configuration
    try:
        # Test if the new column exists
        _session.execute(text("SELECT config_ldap_auto_create_users FROM settings LIMIT 1"))
        _session.commit()
    except exc.OperationalError:  # Column doesn't exist
        try:
            _safe_session_rollback(_session, "settings.config_ldap_auto_create_users")
            _run_ddl_with_retry(
                engine,
                "ALTER TABLE settings ADD column 'config_ldap_auto_create_users' Boolean DEFAULT 1",
            )
        except Exception as e:
            log.error("Failed to add config_ldap_auto_create_users column: %s", e)
            # Don't raise - let CWA continue without this feature
            pass


def migrate_magic_shelf_table(engine, _session):
    """Migrate magic_shelf table to add new columns."""
    # Check and add is_system column
    try:
        _session.query(exists().where(MagicShelf.is_system)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "magic_shelf.is_system")
        _run_ddl_with_retry(engine, "ALTER TABLE magic_shelf ADD column 'is_system' Boolean DEFAULT 0")
    
    # Check and add kobo_sync column
    try:
        _session.query(exists().where(MagicShelf.kobo_sync)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "magic_shelf.kobo_sync")
        _run_ddl_with_retry(engine, "ALTER TABLE magic_shelf ADD column 'kobo_sync' Boolean DEFAULT 0")


def migrate_kobo_synced_book_uuid(engine, _session):
    """Add delivery-time UUID retention to the existing Kobo sync ledger."""
    with engine.begin() as conn:
        table = conn.execute(text(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='kobo_synced_books'"
        )).fetchone()
        if table is None:
            return
        columns = {
            row[1] for row in conn.execute(
                text("PRAGMA table_info(kobo_synced_books)"))
        }
        if "book_uuid" in columns:
            return
    try:
        _run_ddl_with_retry(
            engine,
            "ALTER TABLE kobo_synced_books ADD COLUMN book_uuid VARCHAR(64)",
        )
    except exc.OperationalError as error:
        if "duplicate column" not in str(error).lower():
            raise


def migrate_kobo_unique_constraints(engine, _session):
    """One-time migration: dedupe + uniquify (user_id, book_id) on Kobo
    state tables (audit 2026-05-11).

    Race condition: ``get_or_create_reading_state`` does read-then-write
    without locking, so two concurrent Kobo PUTs to /v1/library/<uuid>/state
    can both miss the existing row and both insert. The next read returns
    multiple rows -> ``.one_or_none()`` raises MultipleResultsFound ->
    500 to the device, which then retries forever.

    The fix has two parts:

    1. This migration: dedupe any existing duplicates, then create a
       UNIQUE index on (user_id, book_id) so the DB rejects future races.
       Winner per duplicate group is the row with the newest
       ``last_modified`` (so the user's most recent reading position
       survives). Child rows (KoboBookmark, KoboStatistics) are
       reparented to the winning KoboReadingState; orphaned child rows
       (the losers' children) are deleted only after their non-null
       fields are merged into the winner's children — newest LM wins
       per child too.

    2. ``get_or_create_reading_state`` switches to an atomic
       INSERT ... ON CONFLICT(user_id, book_id) DO NOTHING upsert so
       the race itself can't produce a duplicate even before the DB
       constraint trips.

    Idempotent: gated by a marker file in CONFIG_DIR/.cwa_migrations/
    so it runs at most once per install.
    """
    from sqlalchemy import inspect as sa_inspect
    marker_path = os.path.join(constants.CONFIG_DIR, ".cwa_migrations",
                               "kobo_unique_constraints_v1")
    if os.path.isfile(marker_path):
        return

    inspector = sa_inspect(engine)
    tables_present = set(inspector.get_table_names())

    # All four tables share the (user_id, book_id) shape. Dedupe rules
    # differ slightly per table; see _dedupe_* helpers below.
    plan = [
        ("kobo_reading_state", _dedupe_kobo_reading_state,
         "uq_kobo_reading_state_user_book"),
        ("book_read_link", _dedupe_book_read_link,
         "uq_book_read_link_user_book"),
        ("kobo_synced_books", _dedupe_kobo_synced_books,
         "uq_kobo_synced_books_user_book"),
        ("archived_book", _dedupe_archived_book,
         "uq_archived_book_user_book"),
    ]

    try:
        for table_name, dedupe_fn, index_name in plan:
            if table_name not in tables_present:
                continue
            removed = dedupe_fn(_session)
            if removed:
                log.info(
                    "[kobo-unique-migration] %s: merged %d duplicate row(s)",
                    table_name, removed,
                )
        _session.commit()
    except Exception as e:
        log.error("[kobo-unique-migration] dedupe failed: %s", e)
        _safe_session_rollback(_session, "kobo_unique_constraints/dedupe")
        return

    # Now that duplicates are gone, create the UNIQUE indexes. SQLite
    # doesn't support ALTER TABLE ADD CONSTRAINT; a UNIQUE INDEX has
    # equivalent semantics for INSERT ... ON CONFLICT routing and is
    # what SQLAlchemy's UniqueConstraint generates on new installs.
    ddl = [
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_kobo_reading_state_user_book "
        "ON kobo_reading_state(user_id, book_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_book_read_link_user_book "
        "ON book_read_link(user_id, book_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_kobo_synced_books_user_book "
        "ON kobo_synced_books(user_id, book_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_archived_book_user_book "
        "ON archived_book(user_id, book_id)",
    ]
    # Skip statements for tables that don't exist yet (fresh install path
    # where add_missing_tables hasn't run create_all on a particular
    # table — defensive only; current order has add_missing_tables first).
    ddl = [s for s in ddl
           if s.split(" ON ")[1].split("(")[0] in tables_present]
    try:
        _run_ddl_with_retry(engine, ddl)
    except Exception as e:
        log.error("[kobo-unique-migration] index creation failed: %s", e)
        return

    try:
        os.makedirs(os.path.dirname(marker_path), exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as fh:
            fh.write(datetime.now(timezone.utc).isoformat())
    except OSError as e:
        # Marker is a perf optimization, not correctness — the migration
        # is idempotent because the indexes already exist and dedupe is
        # a no-op on a clean DB. Log but continue.
        log.warning(
            "[kobo-unique-migration] could not write marker %s: %s",
            marker_path, e,
        )


def _dedupe_kobo_reading_state(_session):
    """Pick winner per (user, book): newest last_modified. Reparent
    KoboBookmark + KoboStatistics children to the winner, merging
    non-null fields from losers' children (newest LM wins per child).
    Returns count of losing rows deleted.
    """
    dup_groups = _find_duplicate_groups(_session, KoboReadingState)
    deleted = 0
    for (user_id, book_id), rows in dup_groups.items():
        # Sort by last_modified DESC, NULL last; then id DESC as tiebreak.
        rows.sort(
            key=lambda r: (
                r.last_modified or datetime.min.replace(tzinfo=timezone.utc),
                r.id,
            ),
            reverse=True,
        )
        winner, losers = rows[0], rows[1:]
        for loser in losers:
            _merge_kobo_bookmark(_session, winner, loser)
            _merge_kobo_statistics(_session, winner, loser)
            _session.delete(loser)
            deleted += 1
    if deleted:
        _session.flush()
    return deleted


def _merge_kobo_bookmark(_session, winner, loser):
    """Merge loser.current_bookmark into winner.current_bookmark.
    Newest last_modified wins per field; non-null beats null when LM
    is missing. Loser's bookmark is left dangling (will be cascade-
    deleted when loser KoboReadingState is deleted).
    """
    if loser.current_bookmark is None:
        return
    if winner.current_bookmark is None:
        # Steal the row outright. Re-point its FK.
        loser.current_bookmark.kobo_reading_state_id = winner.id
        winner.current_bookmark = loser.current_bookmark
        loser.current_bookmark = None
        return
    w, l = winner.current_bookmark, loser.current_bookmark
    if l.created_at and (not w.created_at or l.created_at < w.created_at):
        w.created_at = l.created_at
    if _loser_wins_lm(l, w):
        for attr in ("location_source", "location_type", "location_value",
                     "progress_percent", "content_source_progress_percent",
                     "last_modified"):
            setattr(w, attr, getattr(l, attr))
    else:
        # Even if winner's bookmark is newer overall, prefer non-null
        # losing fields if the winner has nulls there (defensive).
        for attr in ("location_source", "location_type", "location_value",
                     "progress_percent", "content_source_progress_percent"):
            if getattr(w, attr) is None and getattr(l, attr) is not None:
                setattr(w, attr, getattr(l, attr))


def _merge_kobo_statistics(_session, winner, loser):
    if loser.statistics is None:
        return
    if winner.statistics is None:
        loser.statistics.kobo_reading_state_id = winner.id
        winner.statistics = loser.statistics
        loser.statistics = None
        return
    w, l = winner.statistics, loser.statistics
    if _loser_wins_lm(l, w):
        for attr in ("remaining_time_minutes", "spent_reading_minutes",
                     "last_modified"):
            setattr(w, attr, getattr(l, attr))
    else:
        for attr in ("remaining_time_minutes", "spent_reading_minutes"):
            if getattr(w, attr) is None and getattr(l, attr) is not None:
                setattr(w, attr, getattr(l, attr))


def _dedupe_book_read_link(_session):
    """ReadBook winner: prefer rows with the highest read_status (FINISHED
    > IN_PROGRESS > UNREAD), tiebreak by newest last_modified, then by
    highest times_started_reading. Sum times_started_reading from losers
    into the winner so the user doesn't lose their read-counter total.
    """
    dup_groups = _find_duplicate_groups(_session, ReadBook)
    deleted = 0
    for (user_id, book_id), rows in dup_groups.items():
        rows.sort(
            key=lambda r: (
                r.read_status or 0,
                r.last_modified or datetime.min.replace(tzinfo=timezone.utc),
                r.times_started_reading or 0,
                r.id,
            ),
            reverse=True,
        )
        winner, losers = rows[0], rows[1:]
        for loser in losers:
            winner.times_started_reading = (
                (winner.times_started_reading or 0)
                + (loser.times_started_reading or 0)
            )
            if (loser.last_time_started_reading is not None and
                (winner.last_time_started_reading is None or
                 loser.last_time_started_reading > winner.last_time_started_reading)):
                winner.last_time_started_reading = loser.last_time_started_reading
            _session.delete(loser)
            deleted += 1
    if deleted:
        _session.flush()
    return deleted


def _dedupe_kobo_synced_books(_session):
    """No payload to merge — just keep one row (lowest id) per (user, book)."""
    dup_groups = _find_duplicate_groups(_session, KoboSyncedBooks)
    deleted = 0
    for (user_id, book_id), rows in dup_groups.items():
        rows.sort(key=lambda r: r.id)
        for loser in rows[1:]:
            _session.delete(loser)
            deleted += 1
    if deleted:
        _session.flush()
    return deleted


def _dedupe_archived_book(_session):
    """ArchivedBook winner: is_archived=True takes precedence over False
    (archived is the more-recent semantic signal), then newest LM, then
    highest id.
    """
    dup_groups = _find_duplicate_groups(_session, ArchivedBook)
    deleted = 0
    for (user_id, book_id), rows in dup_groups.items():
        rows.sort(
            key=lambda r: (
                1 if r.is_archived else 0,
                r.last_modified or datetime.min.replace(tzinfo=timezone.utc),
                r.id,
            ),
            reverse=True,
        )
        for loser in rows[1:]:
            _session.delete(loser)
            deleted += 1
    if deleted:
        _session.flush()
    return deleted


def _find_duplicate_groups(_session, model):
    """Return dict {(user_id, book_id): [rows]} for groups with size >= 2."""
    from sqlalchemy import func as sa_func
    dup_keys = (
        _session.query(model.user_id, model.book_id)
        .group_by(model.user_id, model.book_id)
        .having(sa_func.count(model.id) > 1)
        .all()
    )
    groups = {}
    for user_id, book_id in dup_keys:
        rows = (
            _session.query(model)
            .filter(model.user_id == user_id, model.book_id == book_id)
            .all()
        )
        groups[(user_id, book_id)] = rows
    return groups


def _loser_wins_lm(loser, winner):
    """True if loser.last_modified > winner.last_modified (strictly newer)."""
    lo = getattr(loser, "last_modified", None)
    wn = getattr(winner, "last_modified", None)
    if lo is None:
        return False
    if wn is None:
        return True
    return lo > wn


def migrate_kobo_deleted_book(engine, _session):
    """Create the kobo_deleted_book tombstone table if it doesn't exist.

    Idempotent: gated by a marker file in CONFIG_DIR/.cwa_migrations/ so
    it runs at most once per install. The marker is a perf optimization,
    not correctness — the underlying DDL uses CREATE TABLE IF NOT EXISTS
    so re-running is safe.

    Why this table exists: see the KoboDeletedBook model docstring.
    Captures (user_id, book_uuid, deleted_at) at the moment a book is
    deleted, so HandleSyncRequest can emit an archived ChangedEntitlement on the
    next sync per affected user — the existing two-way deletion logic
    can only handle books removed from kobo_sync shelves, not hard
    deletes from the calibre library.
    """
    from sqlalchemy import inspect as sa_inspect
    marker_path = os.path.join(constants.CONFIG_DIR, ".cwa_migrations",
                               "kobo_deleted_book_v1")
    if os.path.isfile(marker_path):
        return

    inspector = sa_inspect(engine)
    if "kobo_deleted_book" not in inspector.get_table_names():
        try:
            KoboDeletedBook.__table__.create(engine, checkfirst=True)
        except Exception as e:
            log.error("[kobo-deleted-book-migration] create_all failed: %s", e)
            return

    # Indexes for the sync query path: (user_id, deleted_at) covers the
    # "rows for current_user where deleted_at > cursor" pattern.
    try:
        _run_ddl_with_retry(
            engine,
            "CREATE INDEX IF NOT EXISTS idx_kobo_deleted_book_user_deleted "
            "ON kobo_deleted_book(user_id, deleted_at)",
        )
    except Exception as e:
        log.warning("[kobo-deleted-book-migration] index creation failed: %s", e)

    try:
        os.makedirs(os.path.dirname(marker_path), exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as fh:
            fh.write(datetime.now(timezone.utc).isoformat())
    except OSError as e:
        log.warning(
            "[kobo-deleted-book-migration] could not write marker %s: %s",
            marker_path, e,
        )


def migrate_kobo_magic_shelf_intent(engine, _session):
    """One-time: turn on the global 'Sync Magic Shelves to Kobo' setting on
    installs where per-shelf intent already exists (fork #359).

    config_kobo_sync_magic_shelves ships default-False, but the magic-shelf
    edit UI let users tick the per-shelf "Enable Kobo sync" checkbox with the
    global flag off — the intent was then silently swallowed: no delivery, no
    collections, and a DeletedTag tombstone per shelf on every sync.
    @recruiterguy lived this across v4.0.76 → v4.0.155 (#359). The per-shelf
    checkbox IS the user's expressed intent; where any shelf carries it, the
    feature should be on.

    Runs once per install (marker file), so an admin who later disables the
    global setting deliberately is never re-flipped. If the settings table
    doesn't yet have the column (upgrade from a pre-flag schema — config_sql
    adds it AFTER ub migrations on first boot), the OperationalError path
    leaves the marker unwritten so the flip retries on the next boot.
    """
    marker_path = os.path.join(constants.CONFIG_DIR, ".cwa_migrations",
                               "kobo_magic_shelf_intent_v1")
    if os.path.isfile(marker_path):
        return

    def _write_marker():
        os.makedirs(os.path.dirname(marker_path), exist_ok=True)
        with open(marker_path, "w", encoding="utf-8") as fh:
            fh.write(datetime.now(timezone.utc).isoformat())

    try:
        with engine.connect() as conn:
            intent = conn.execute(text(
                "SELECT COUNT(*) FROM magic_shelf WHERE kobo_sync = 1"
            )).scalar()
            if intent:
                flipped = conn.execute(text(
                    "UPDATE settings SET config_kobo_sync_magic_shelves = 1 "
                    "WHERE config_kobo_sync_magic_shelves = 0 "
                    "   OR config_kobo_sync_magic_shelves IS NULL"
                ))
                # Marker BEFORE commit: the flip and its never-again guard
                # must land together. If the marker can't be written (e.g.
                # read-only /config), abort WITHOUT committing — flipping
                # while unable to record it would re-override a deliberate
                # admin disable on every subsequent boot (Greptile P1 on
                # PR #372). Not flipping is the safe failure: the gated
                # checkbox UI tells users how to enable the setting manually.
                try:
                    _write_marker()
                except OSError as e:
                    log.warning(
                        "[kobo-magic-shelf-intent-migration] marker %s not "
                        "writable (%s) — NOT applying the flag flip; will "
                        "retry next boot.", marker_path, e,
                    )
                    return  # no commit → UPDATE rolls back with the connection
                conn.commit()
                if getattr(flipped, "rowcount", 0):
                    log.info(
                        "[kobo-magic-shelf-intent-migration] %s magic shelves "
                        "are marked for Kobo sync but the global 'Sync Magic "
                        "Shelves to Kobo' setting was off — enabled it so the "
                        "existing per-shelf intent takes effect (#359).",
                        intent,
                    )
                return  # marker already written above
    except exc.OperationalError as e:
        # magic_shelf table or settings column missing — pre-flag schema
        # mid-upgrade. Retry next boot (marker intentionally not written).
        log.warning(
            "[kobo-magic-shelf-intent-migration] deferred (schema not ready "
            "yet, will retry next boot): %s", e,
        )
        return

    # No-intent path: record the decision so it isn't re-evaluated every
    # boot. A marker-write failure here is harmless (re-evaluating "no
    # intent" is a no-op) — log and move on.
    try:
        _write_marker()
    except OSError as e:
        log.warning(
            "[kobo-magic-shelf-intent-migration] could not write marker %s: %s",
            marker_path, e,
        )


def migrate_shelf_table(engine, _session):
    """Ensure Shelf.kobo_sync column exists; backfill DDL if not (legacy
    fork branch — predates the dedicated kobo_sync migrations elsewhere).
    Called from migrate_Database below."""
    try:
        _session.query(exists().where(Shelf.kobo_sync)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "shelf.kobo_sync")
        _run_ddl_with_retry(engine, "ALTER TABLE shelf ADD column 'kobo_sync' Boolean DEFAULT 0")


# Migrate database to current version, has to be updated after every database change. Currently migration from
# maybe 4/5 versions back to current should work.
# Migration is done by checking if relevant columns are existing, and then adding rows with SQL commands
def migrate_user_view_settings_null(engine, _session):
    """One-time normalization of legacy User.view_settings = NULL to '{}'.

    `view_settings` is `Column(JSON, default={})` but SQLAlchemy's
    `default=` only applies on INSERT through the ORM. Rows imported
    from older schemas (pre-2025-01-14 when this column was added
    upstream), or inserted via raw SQL or external admin tools, can
    land with NULL — which then 500s every page consulting
    view_settings (layout.html cover-settings cog, sort prefs, etc.)
    via the unguarded `self.view_settings.get(page)` call in
    get_view_property.

    Idempotent: the UPDATE matches zero rows on subsequent runs.
    No-op when there are no NULL rows. Logs the rowcount so operators
    can see in container logs whether any legacy NULLs existed.
    """
    try:
        result = _session.execute(
            text("UPDATE user SET view_settings = '{}' WHERE view_settings IS NULL")
        )
        _session.commit()
        if getattr(result, "rowcount", 0):
            log.info(
                "[view-settings-null-migration] normalized %d user(s) with NULL view_settings to '{}'",
                result.rowcount,
            )
    except Exception as ex:
        _session.rollback()
        log.error("[view-settings-null-migration] failed: %s", ex)


def migrate_dismissed_duplicate_groups_table(engine, _session):
    """Add the D5 ``duplicate_key`` column to dismissed_duplicate_groups.

    Introspects the live schema and adds only what's missing, so the
    migration is idempotent and repairs partial states (same pattern as
    migrate_oauth_provider_table, fork #354). Existing rows keep a NULL
    duplicate_key and are lazily backfilled by filter_dismissed_groups when
    their group_hash next matches a live group.
    """
    from sqlalchemy import inspect as sa_inspect
    try:
        inspector = sa_inspect(engine)
        if "dismissed_duplicate_groups" not in inspector.get_table_names():
            return  # created fresh from the model with every column present
        existing = {col["name"] for col in inspector.get_columns("dismissed_duplicate_groups")}
        if "duplicate_key" not in existing:
            _run_ddl_with_retry(
                engine,
                "ALTER TABLE dismissed_duplicate_groups ADD COLUMN duplicate_key TEXT",
            )
            print("[dup-dismiss-migration] Added duplicate_key column to dismissed_duplicate_groups", flush=True)
    except Exception as e:
        print(f"[dup-dismiss-migration] Failed to add duplicate_key column: {e}", flush=True)


def migrate_book_cover_preview_table(engine, _session):
    """Create the book_cover_preview table if it doesn't exist.
    Idempotent — `BookCoverPreview.__table__.create(engine, checkfirst=True)`
    no-ops if the table already exists.
    """
    try:
        with engine.connect() as conn:
            has_table = engine.dialect.has_table(conn, "book_cover_preview")
    except Exception:
        # Fall back to the SQLAlchemy 2.0-friendly form if dialect.has_table
        # signature differs across versions.
        has_table = False
    if not has_table:
        BookCoverPreview.__table__.create(engine, checkfirst=True)
        try:
            _run_ddl_with_retry(
                engine,
                "CREATE INDEX IF NOT EXISTS idx_bcp_user_locked ON book_cover_preview(user_id, locked)",
            )
        except Exception as e:
            print(f"[cover-preview-migration] Could not create idx_bcp_user_locked: {e}", flush=True)


def migrate_notice_tables(engine, _session):
    """Create the generic notice inbox and resumable repair journal idempotently."""
    Base.metadata.create_all(
        engine,
        tables=[
            NoticeEvent.__table__,
            UserNoticeDelivery.__table__,
            KepubPackageRepair.__table__,
        ],
        checkfirst=True,
    )


def migrate_kepub_package_repair_disposition(engine, _session):
    """Add nullable cheap-identity/version fields for terminal dispositions."""
    with engine.begin() as conn:
        table = conn.execute(text(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='kepub_package_repair'"
        )).fetchone()
        if table is None:
            return
        existing = {
            row[1] for row in conn.execute(
                text("PRAGMA table_info(kepub_package_repair)"))
        }

    columns = {
        "source_size": "INTEGER",
        "source_mtime_ns": "INTEGER",
        "source_ctime_ns": "INTEGER",
        "repair_version": "INTEGER",
    }
    for column, column_type in columns.items():
        if column in existing:
            continue
        try:
            _run_ddl_with_retry(
                engine,
                "ALTER TABLE kepub_package_repair ADD COLUMN {} {}".format(
                    column, column_type),
            )
        except exc.OperationalError as error:
            if "duplicate column" not in str(error).lower():
                raise


def migrate_kobo_annotation_sync_h1_columns(engine, _session):
    """H1 Phase 1: extend ``kobo_annotation_sync`` with position + source
    tracking columns for the Kobo highlight import / view / web-reader
    sync pipeline (see notes/KOBO-WEB-READER-ANNOTATIONS-DESIGN.md §1.1).

    Pre-H1 the table only carried Hardcover-sync fields (CWA #1166, #1324):
    ``synced_to_hardcover`` + ``hardcover_journal_id`` + inline
    ``highlighted_text`` / ``highlight_color`` / ``note_text``. H1 reuses
    the same table as the source-of-truth for ALL highlight ingestion
    paths; the columns added here are nullable so existing Hardcover-sync
    rows keep working untouched.

    All ADDs are individually conditional on column-existence, so this
    migration is idempotent — re-running it is a no-op.
    """
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(engine)
    if "kobo_annotation_sync" not in inspector.get_table_names():
        # Fresh install — Base.metadata.create_all already produced the
        # full schema; nothing to migrate.
        return

    existing_cols = {c["name"] for c in inspector.get_columns("kobo_annotation_sync")}

    # column name → DDL fragment (must omit leading "ALTER TABLE foo ADD COLUMN")
    pending = [
        ("content_id",                  "content_id VARCHAR"),
        ("start_container_path",        "start_container_path TEXT"),
        ("start_container_child_index", "start_container_child_index INTEGER"),
        ("start_offset",                "start_offset INTEGER"),
        ("end_container_path",          "end_container_path TEXT"),
        ("end_container_child_index",   "end_container_child_index INTEGER"),
        ("end_offset",                  "end_offset INTEGER"),
        ("context_string",              "context_string TEXT"),
        ("chapter_progress",            "chapter_progress REAL"),
        ("cfi_range",                   "cfi_range VARCHAR"),
        ("source",                      "source VARCHAR"),
        ("hidden",                      "hidden BOOLEAN DEFAULT 0"),
    ]

    statements = [
        f"ALTER TABLE kobo_annotation_sync ADD COLUMN {ddl}"
        for col, ddl in pending
        if col not in existing_cols
    ]
    if not statements:
        return

    try:
        _run_ddl_with_retry(engine, statements)
    except Exception as e:
        log.error("[kobo-annotation-sync-h1-migration] ADD COLUMN failed: %s", e)
        return

    # Backfill source='hardcover' for pre-H1 rows so the import path can
    # distinguish them from new ingestion sources without scanning all
    # five Hardcover-specific columns. Only touches rows that didn't get
    # a source set later (idempotent under re-runs).
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            conn.execute(text(
                "UPDATE kobo_annotation_sync SET source = 'hardcover' "
                "WHERE source IS NULL AND synced_to_hardcover = 1"
            ))
            trans.commit()
    except Exception as e:
        log.warning(
            "[kobo-annotation-sync-h1-migration] source backfill failed: %s", e
        )


def _migrate_step1_create_target_table(conn):
    """Create annotation_sync_target table + indexes if not present."""
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS annotation_sync_target (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            annotation_id     INTEGER NOT NULL,
            target            VARCHAR NOT NULL,
            target_record_id  VARCHAR,
            status            VARCHAR NOT NULL,
            error_message     TEXT,
            last_attempt      DATETIME,
            last_synced       DATETIME,
            created_at        DATETIME NOT NULL,
            updated_at        DATETIME NOT NULL,
            UNIQUE (annotation_id, target),
            FOREIGN KEY (annotation_id) REFERENCES annotation(id) ON DELETE CASCADE
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_ast_target_status "
        "ON annotation_sync_target (target, status)"
    ))


def _migrate_step2_backfill_sync_state(conn):
    """Copy synced_to_hardcover=1 rows into annotation_sync_target.

    Idempotent — WHERE NOT EXISTS so re-runs insert nothing new.
    Returns the row-count actually inserted.
    """
    result = conn.execute(text("""
        INSERT INTO annotation_sync_target
            (annotation_id, target, target_record_id, status,
             last_synced, last_attempt, created_at, updated_at)
        SELECT
            kas.id,
            'hardcover',
            CAST(kas.hardcover_journal_id AS VARCHAR),
            'synced',
            kas.last_synced,
            kas.last_synced,
            kas.last_synced,
            kas.last_synced
        FROM kobo_annotation_sync kas
        WHERE kas.synced_to_hardcover = 1
          AND NOT EXISTS (
              SELECT 1 FROM annotation_sync_target ast
              WHERE ast.annotation_id = kas.id AND ast.target = 'hardcover'
          )
    """))
    return result.rowcount


def _migrate_step3_fix_source_values(conn):
    """Correct source='hardcover' rows to source='kobo'. Idempotent."""
    result = conn.execute(text(
        "UPDATE kobo_annotation_sync SET source = 'kobo' WHERE source = 'hardcover'"
    ))
    return result.rowcount


def _migrate_step4_sanity_check(conn):
    """Refuse destructive steps unless every synced legacy row was backfilled.

    Verifies backfill *completeness* — that no ``synced_to_hardcover = 1`` row
    was left without a matching ``annotation_sync_target`` row — rather than a
    total-count equality. ``annotation_sync_target`` can already hold organic
    ``'hardcover'`` rows written by the live annotation-sync pipeline before
    the rename completed, so its total 'hardcover' count legitimately exceeds
    the legacy synced count. The old ``pre == post`` check falsely assumed the
    target table starts empty and crash-looped on any DB that had ever synced
    annotations to Hardcover (issue #684). This check is anchored on the legacy
    table's synced rows, so unrelated organic rows can't trip it.
    """
    missing = conn.execute(text("""
        SELECT COUNT(*) FROM kobo_annotation_sync kas
        WHERE kas.synced_to_hardcover = 1
          AND NOT EXISTS (
              SELECT 1 FROM annotation_sync_target ast
              WHERE ast.annotation_id = kas.id AND ast.target = 'hardcover'
          )
    """)).scalar()
    if missing:
        raise RuntimeError(
            f"[annotation-decouple-migration] backfill incomplete: "
            f"{missing} synced_to_hardcover=1 row(s) have no matching "
            f"annotation_sync_target 'hardcover' row"
        )


def _migrate_step5_rename_table(conn):
    """Rename kobo_annotation_sync -> annotation."""
    conn.execute(text("ALTER TABLE kobo_annotation_sync RENAME TO annotation"))


def _migrate_step6_rename_indexes(conn):
    """SQLite doesn't have ALTER INDEX RENAME; drop + create."""
    conn.execute(text("DROP INDEX IF EXISTS ix_kobo_annotation_sync_user_annotation"))
    conn.execute(text("DROP INDEX IF EXISTS ix_kobo_annotation_sync_user_book"))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_annotation_user_annotation "
        "ON annotation (user_id, annotation_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_annotation_user_book "
        "ON annotation (user_id, book_id)"
    ))


def _migrate_step7_drop_old_columns(conn):
    """Drop synced_to_hardcover + hardcover_journal_id columns.

    SQLite >= 3.35 supports DROP COLUMN. Each DROP is guarded by
    column-existence so re-runs are no-ops.
    """
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(conn)
    cols = {c["name"] for c in inspector.get_columns("annotation")}
    if "synced_to_hardcover" in cols:
        conn.execute(text("ALTER TABLE annotation DROP COLUMN synced_to_hardcover"))
    if "hardcover_journal_id" in cols:
        conn.execute(text("ALTER TABLE annotation DROP COLUMN hardcover_journal_id"))


def migrate_annotation_polymorphic_position(engine, _session):
    """Sub-projects (3)/(4) — add polymorphic position columns to annotation.

    Adds position_type, pdf_page, pdf_quad_json, comic_page. Idempotent.

    Runs AFTER the decouple migration. We query the live SQLite catalog
    via `PRAGMA table_info` (NOT SQLAlchemy's inspector) because the
    inspector's reflection cache can be stale after the preceding
    decouple migration's RENAME — observed on v4.0.130 teenyverse deploy
    where the inspector still saw the dropped `annotation` placeholder
    columns and incorrectly reported `position_type` as already present
    OR (more likely) reported the old kobo_annotation_sync columns
    AND THEN ADD COLUMN failed because the actual table came from
    create_all which had the polymorphic columns from the model.

    Per-statement try/except gives belt-and-suspenders idempotency:
    a duplicate-column-name error on any individual ADD COLUMN is
    caught, logged at INFO, and skipped — the migration completes the
    rest of the columns instead of aborting the whole transaction.
    """
    with engine.begin() as conn:
        # Bail out cleanly if the annotation table doesn't exist yet
        # (fresh install — create_all already produced the full schema).
        rows = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='annotation'"
        )).fetchall()
        if not rows:
            return

        # Query the actual live column set via PRAGMA — the SQLAlchemy
        # inspector cache is unreliable across migrations that RENAME tables.
        existing = {
            row[1] for row in conn.execute(text(
                "PRAGMA table_info(annotation)"
            )).fetchall()
        }
        pending = [
            ("position_type",   "position_type VARCHAR"),
            ("pdf_page",        "pdf_page INTEGER"),
            ("pdf_quad_json",   "pdf_quad_json TEXT"),
            ("comic_page",      "comic_page INTEGER"),
        ]
        added = 0
        for col, ddl in pending:
            if col in existing:
                continue
            try:
                conn.execute(text(f"ALTER TABLE annotation ADD COLUMN {ddl}"))
                added += 1
            except exc.OperationalError as e:
                # Duplicate-column-name means another concurrent path
                # already added it; treat as success, log + continue.
                if "duplicate column" in str(e).lower():
                    log.info(
                        "[annotation-polymorphic-position] column %s already "
                        "present despite PRAGMA check; treating as idempotent",
                        col,
                    )
                    continue
                raise
        if added:
            log.info(
                "[annotation-polymorphic-position] added %d columns", added,
            )


def migrate_annotation_device_origin(engine, _session):
    """Phase 2 (KOReader bridge) — add the nullable ``device_origin_id`` column
    to ``annotation``. Idempotent.

    Same PRAGMA-guarded, per-statement try/except shape as
    :func:`migrate_annotation_polymorphic_position` — query the live SQLite
    catalog (NOT the SQLAlchemy inspector, whose reflection cache is stale
    across the decouple migration's RENAME) so the existence check sees the
    same view as the DDL. Nullable column, zero data risk.
    """
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='annotation'"
        )).fetchall()
        if not rows:
            return
        existing = {
            row[1] for row in conn.execute(text(
                "PRAGMA table_info(annotation)"
            )).fetchall()
        }
        if "device_origin_id" in existing:
            return
        try:
            conn.execute(text("ALTER TABLE annotation ADD COLUMN device_origin_id VARCHAR"))
            log.info("[annotation-device-origin] added device_origin_id column")
        except exc.OperationalError as e:
            if "duplicate column" in str(e).lower():
                log.info(
                    "[annotation-device-origin] column already present despite "
                    "PRAGMA check; treating as idempotent"
                )
            else:
                raise


def migrate_multi_device_annotation_safe_slice(engine, _session):
    """Create the additive registry and timestamp schema.

    The content-id backfill runs only after the Calibre database is available;
    startup reaches this migration before ``calibre_db.init_db()``, so this
    stage cannot prove an annotation's authoritative book UUID.
    """
    Base.metadata.create_all(
        engine,
        tables=[Device.__table__, DeviceIdentity.__table__, AnnotationContentIdMigration.__table__],
        checkfirst=True,
    )
    with engine.begin() as conn:
        if not conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='annotation'"
        )).first():
            return
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(annotation)"))}
        if "client_modified_at" not in columns:
            try:
                conn.execute(text("ALTER TABLE annotation ADD COLUMN client_modified_at DATETIME"))
            except exc.OperationalError as error:
                if "duplicate column" not in str(error).lower():
                    raise


def backfill_annotation_content_ids(engine, book_uuid_lookup):
    """Journal and normalize only book-verified legacy annotation ids.

    ``book_uuid_lookup(book_id)`` reads Calibre's authoritative book record.
    Missing books, lookup errors, malformed UUIDs, and filename/book mismatches
    leave the stored value byte-for-byte unchanged. The repair block also
    reverses an earlier unsafe migration when its journaled canonical UUID does
    not belong to the row's actual book and nobody edited the value afterward.
    """
    from .services.annotation_content_id import (
        ContentIdError,
        normalize_content_id,
        normalize_content_id_for_backfill,
    )
    with engine.begin() as conn:
        tables = {row[0] for row in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))}
        if not {"annotation", "annotation_content_id_migration"} <= tables:
            return
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(annotation)"))}
        if not {"id", "book_id", "content_id"} <= columns:
            return
        rows = conn.execute(text(
            "SELECT a.id, a.book_id, a.content_id, "
            "m.original_content_id, m.normalized_content_id "
            "FROM annotation a LEFT JOIN annotation_content_id_migration m "
            "ON m.annotation_row_id=a.id WHERE a.content_id IS NOT NULL"
        )).fetchall()
        changed = 0
        repaired = 0
        for row_id, book_id, current, journal_original, journal_normalized in rows:
            try:
                book_uuid = book_uuid_lookup(book_id)
            except Exception:
                log.warning(
                    "[annotation-content-id] book lookup failed for book %s",
                    book_id, exc_info=True,
                )
                continue
            if not book_uuid:
                continue
            if journal_normalized is not None:
                if current != journal_normalized:
                    continue
                try:
                    normalize_content_id(journal_normalized, book_uuid=book_uuid)
                except ContentIdError:
                    conn.execute(text(
                        "UPDATE annotation SET content_id=:original "
                        "WHERE id=:row_id AND content_id=:normalized"
                    ), {"original": journal_original, "row_id": row_id,
                        "normalized": journal_normalized})
                    conn.execute(text(
                        "DELETE FROM annotation_content_id_migration "
                        "WHERE annotation_row_id=:row_id"
                    ), {"row_id": row_id})
                    current = journal_original
                    repaired += 1
                else:
                    continue
            normalized = normalize_content_id_for_backfill(
                current, book_uuid=book_uuid,
            )
            if normalized == current:
                continue
            conn.execute(text(
                "INSERT OR IGNORE INTO annotation_content_id_migration "
                "(annotation_row_id, original_content_id, normalized_content_id, migrated_at) "
                "VALUES (:row_id, :original, :normalized, :migrated_at)"
            ), {"row_id": row_id, "original": current, "normalized": normalized,
                "migrated_at": datetime.now(timezone.utc)})
            result = conn.execute(text(
                "UPDATE annotation SET content_id=:normalized "
                "WHERE id=:row_id AND content_id=:original"
            ), {"normalized": normalized, "row_id": row_id, "original": current})
            changed += result.rowcount
        if changed or repaired:
            log.info(
                "[annotation-content-id] normalized %d verified row(s); "
                "repaired %d unsafe prior migration(s)", changed, repaired,
            )


def migrate_device_management_slice(engine, _session):
    """Add nullable attribution/routing columns and per-device state."""
    Base.metadata.create_all(
        engine,
        tables=[AnnotationDeviceState.__table__, DeviceRetiredAssignment.__table__],
        checkfirst=True,
    )
    with engine.begin() as conn:
        if not conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='annotation'"
        )).first():
            return
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(annotation)"))}
        additions = (
            ("origin_device_id", "origin_device_id INTEGER REFERENCES device(id) ON DELETE SET NULL"),
            ("assigned_device_id", "assigned_device_id INTEGER REFERENCES device(id) ON DELETE SET NULL"),
            ("routing_revision", "routing_revision INTEGER NOT NULL DEFAULT 1"),
        )
        for name, ddl in additions:
            if name not in existing:
                try:
                    conn.execute(text(f"ALTER TABLE annotation ADD COLUMN {ddl}"))
                except exc.OperationalError as error:
                    if "duplicate column" not in str(error).lower():
                        raise
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_annotation_origin_device ON annotation(origin_device_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_annotation_assigned_device ON annotation(assigned_device_id)"
        ))


_KOBO_TWO_WAY_TABLES = (
    KoboAnnotationMaterialization.__table__,
    KoboAnnotationBookState.__table__,
    KoboOpaqueContentPresentGuard.__table__,
    KoboDeviceBookAnnotationState.__table__,
    KoboAnnotationSeedCapture.__table__,
    KoboAnnotationSeedCapturePage.__table__,
    KoboAnnotationPageSnapshot.__table__,
    KoboAnnotationPageCursor.__table__,
)

_KOBO_TWO_WAY_TABLE_NAMES = tuple(table.name for table in _KOBO_TWO_WAY_TABLES)


def _table_columns(engine, table_name):
    with engine.connect() as conn:
        if not conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"
        ), {"name": table_name}).first():
            return None
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})"))}


def _add_column_if_missing(engine, table_name, column_name, ddl):
    """PRAGMA-guarded additive DDL with duplicate-column race recovery."""
    columns = _table_columns(engine, table_name)
    if columns is None or column_name in columns:
        return False
    try:
        _run_ddl_with_retry(engine, f"ALTER TABLE {table_name} ADD COLUMN {ddl}")
    except exc.OperationalError as error:
        if "duplicate column" not in str(error).lower():
            raise
        log.info(
            "[kobo-two-way-stage0] %s.%s appeared during migration; treating as idempotent",
            table_name, column_name,
        )
    return True


def _ensure_kobo_two_way_gate_columns(engine):
    """Install both persisted opt-ins without assuming either table exists."""
    if engine is None:
        return
    _add_column_if_missing(
        engine, "user", "kobo_two_way_annotation_sync",
        "kobo_two_way_annotation_sync BOOLEAN NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        engine, "settings", "config_kobo_two_way_annotation_sync",
        "config_kobo_two_way_annotation_sync BOOLEAN NOT NULL DEFAULT 0",
    )
    has_user = _table_columns(engine, "user") is not None
    has_settings = _table_columns(engine, "settings") is not None
    with engine.begin() as conn:
        if has_user:
            conn.execute(text(
                "UPDATE user SET kobo_two_way_annotation_sync=0 "
                "WHERE kobo_two_way_annotation_sync IS NULL"
            ))
        if has_settings:
            conn.execute(text(
                "UPDATE settings SET config_kobo_two_way_annotation_sync=0 "
                "WHERE config_kobo_two_way_annotation_sync IS NULL"
            ))


def _kobo_stage0_foreign_key_errors(conn):
    """Return only FK violations attributable to Stage 0-owned schema.

    Long-lived app databases can contain unrelated historical orphans because
    SQLite foreign-key enforcement is normally disabled.  Checking the whole
    database here would make an additive migration responsible for data it did
    not create.  Stage 0 owns its seven new tables and the nullable
    ``annotation.last_editor_device_id`` reference; the annotation table's
    older foreign keys are deliberately outside this check.
    """
    existing_tables = {
        row[0] for row in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))
    }
    errors = set()
    for table_name in _KOBO_TWO_WAY_TABLE_NAMES:
        if table_name not in existing_tables:
            continue
        try:
            for row in conn.execute(text(f"PRAGMA foreign_key_check({table_name})")):
                errors.add((table_name, *tuple(row)))
        except exc.OperationalError:
            # A pre-existing malformed/rebuilt table can make SQLite reject
            # the diagnostic itself (for example, a lost parent PK). Keep a
            # structural sentinel in the before/after comparison; capability
            # inspection will remain false, but trigger healing can continue.
            errors.add((table_name, "foreign_key_check_unavailable"))

    annotation_columns = (
        {row[1] for row in conn.execute(text("PRAGMA table_info(annotation)"))}
        if "annotation" in existing_tables else set()
    )
    if "last_editor_device_id" in annotation_columns and "device" in existing_tables:
        for row in conn.execute(text(
            "SELECT a.id, a.last_editor_device_id FROM annotation a "
            "LEFT JOIN device d ON d.id=a.last_editor_device_id "
            "WHERE a.last_editor_device_id IS NOT NULL AND d.id IS NULL"
        )):
            errors.add(("annotation.last_editor_device_id", *tuple(row)))
    return errors


def _ensure_kobo_opaque_present_guards(engine):
    """Install and heal the durable opaque-content downgrade guards."""
    statements = [
        "DROP TRIGGER IF EXISTS trg_kabs_opaque_present_sticky",
        "INSERT OR IGNORE INTO kobo_opaque_content_present_guard "
        "(user_id, book_id, first_observed_at) "
        "SELECT user_id, book_id, CURRENT_TIMESTAMP "
        "FROM kobo_annotation_book_state "
        "WHERE opaque_content_status='present'",
        *_KOBO_OPAQUE_GUARD_TRIGGER_DDL,
    ]
    _run_ddl_with_retry(engine, statements)


def migrate_kobo_two_way_annotation_sync(engine, _session):
    """Stage 0 additive schema and conservative legacy-state backfill.

    This migration never updates a pre-existing annotation column.  The only
    annotation backfills target newly-added columns, and every legacy book is
    represented as unseeded/unknown rather than being promoted to authority.
    """
    # Capture existing Stage 0-scoped violations so a repeated or partially
    # completed migration never claims responsibility for historical data.
    with engine.connect() as conn:
        foreign_key_errors_before = _kobo_stage0_foreign_key_errors(conn)
    if foreign_key_errors_before:
        log.warning(
            "[kobo-two-way-stage0] %d pre-existing foreign-key violation(s) "
            "already exist in Stage 0-owned schema; continuing without "
            "attributing them to this migration",
            len(foreign_key_errors_before),
        )

    _ensure_kobo_two_way_gate_columns(engine)

    Base.metadata.create_all(engine, tables=list(_KOBO_TWO_WAY_TABLES), checkfirst=True)

    annotation_columns = _table_columns(engine, "annotation")
    if annotation_columns is not None:
        additions = (
            ("annotation_type", "annotation_type VARCHAR(32)"),
            ("content_revision", "content_revision INTEGER NOT NULL DEFAULT 1"),
            ("server_modified_at", "server_modified_at DATETIME"),
            (
                "last_editor_device_id",
                "last_editor_device_id INTEGER REFERENCES device(id) ON DELETE SET NULL",
            ),
        )
        for column_name, ddl in additions:
            _add_column_if_missing(engine, "annotation", column_name, ddl)

        with engine.begin() as conn:
            row_count_before = conn.execute(text("SELECT COUNT(*) FROM annotation")).scalar_one()
            conn.execute(text(
                "UPDATE annotation SET content_revision=1 WHERE content_revision IS NULL"
            ))
            conn.execute(text(
                "UPDATE annotation SET server_modified_at=COALESCE(last_synced, created_at) "
                "WHERE server_modified_at IS NULL"
            ))

            existing_states = {
                (row[0], row[1]): row[2]
                for row in conn.execute(text(
                    "SELECT user_id, book_id, content_id FROM kobo_annotation_book_state"
                ))
            }
            used_content_ids = {
                (row[0], row[1]) for row in conn.execute(text(
                    "SELECT user_id, content_id FROM kobo_annotation_book_state"
                ))
            }
            all_group_count = conn.execute(text(
                "SELECT COUNT(*) FROM ("
                "SELECT user_id, book_id FROM annotation GROUP BY user_id, book_id)"
            )).scalar_one()
            groups = conn.execute(text(
                "SELECT a.user_id, a.book_id "
                "FROM annotation a JOIN user u ON u.id=a.user_id "
                "WHERE a.user_id IS NOT NULL AND a.book_id IS NOT NULL "
                "GROUP BY a.user_id, a.book_id"
            )).fetchall()
            skipped_group_count = all_group_count - len(groups)
            if skipped_group_count:
                log.warning(
                    "[kobo-two-way-stage0] skipped %d legacy annotation book "
                    "group(s) with a NULL key or no current user; rows unchanged",
                    skipped_group_count,
                )
            inserted_state_ids = []
            for user_id, book_id in groups:
                if (user_id, book_id) in existing_states:
                    continue
                # annotation.content_id is chapter-scoped (book!!chapter),
                # never the bare Kobo book content id.  Use an explicit
                # non-wire sentinel until a later seed binds live evidence.
                candidate = f"legacy-book:{book_id}"
                if (user_id, candidate) in used_content_ids:
                    candidate = f"legacy-book:{book_id}"
                # Keep the schema's bounded content-id contract even for a
                # non-protocol placeholder.  It remains unseeded and is never
                # emitted to a Kobo device.
                candidate = str(candidate)[:64]
                suffix = 1
                base = candidate
                while (user_id, candidate) in used_content_ids:
                    tail = f":{suffix}"
                    candidate = base[:64 - len(tail)] + tail
                    suffix += 1
                result = conn.execute(text(
                    "INSERT INTO kobo_annotation_book_state "
                    "(user_id, book_id, content_id, authority_status, authority_revision, "
                    "generation_id, opaque_content_status, updated_at) VALUES "
                    "(:user_id, :book_id, :content_id, 'unseeded', 0, :generation_id, "
                    "'unknown', :updated_at)"
                ), {
                    "user_id": user_id,
                    "book_id": book_id,
                    "content_id": candidate,
                    "generation_id": str(uuid.uuid4()),
                    "updated_at": datetime.now(timezone.utc),
                })
                inserted_state_ids.append(result.lastrowid)
                used_content_ids.add((user_id, candidate))

            row_count_after = conn.execute(text("SELECT COUNT(*) FROM annotation")).scalar_one()
            if row_count_after != row_count_before:
                raise RuntimeError(
                    "Kobo Stage 0 migration changed the annotation row count "
                    f"({row_count_before} -> {row_count_after})"
                )
            missing_states = conn.execute(text(
                "SELECT COUNT(*) FROM ("
                "SELECT a.user_id, a.book_id FROM annotation a "
                "JOIN user u ON u.id=a.user_id "
                "LEFT JOIN kobo_annotation_book_state s "
                "ON s.user_id=a.user_id AND s.book_id=a.book_id "
                "WHERE a.user_id IS NOT NULL AND a.book_id IS NOT NULL "
                "GROUP BY a.user_id, a.book_id HAVING COUNT(DISTINCT s.id) <> 1)"
            )).scalar_one()
            if missing_states:
                # The INSERT path above either creates a row or raises.  A
                # surviving non-one cardinality therefore came from a partial
                # historical table that lacks the current unique constraint.
                log.warning(
                    "[kobo-two-way-stage0] %d eligible legacy annotation book "
                    "group(s) have pre-existing non-canonical authority state; "
                    "no state was promoted by this migration",
                    missing_states,
                )
            unsafe_legacy_state = 0
            if inserted_state_ids:
                inserted_id_sql = ",".join(str(int(row_id)) for row_id in inserted_state_ids)
                unsafe_legacy_state = conn.execute(text(
                    "SELECT COUNT(*) FROM kobo_annotation_book_state "
                    f"WHERE id IN ({inserted_id_sql}) AND ("
                    "authority_status <> 'unseeded' "
                    "OR opaque_content_status <> 'unknown')"
                )).scalar_one()
            if unsafe_legacy_state:
                raise RuntimeError(
                    "Kobo Stage 0 migration created a legacy book state beyond "
                    "unseeded/unknown; refusing an unsafe authority result"
                )
            foreign_key_errors_after = _kobo_stage0_foreign_key_errors(conn)
            new_foreign_key_errors = (
                foreign_key_errors_after - foreign_key_errors_before
            )
            if new_foreign_key_errors:
                raise RuntimeError(
                    "Kobo Stage 0 migration created foreign-key violation(s) "
                    f"for {len(new_foreign_key_errors)} row(s)"
                )
            try:
                global_foreign_key_errors = conn.execute(
                    text("PRAGMA foreign_key_check")
                ).fetchall()
            except exc.OperationalError:
                global_foreign_key_errors = []
                log.warning(
                    "[kobo-two-way-stage0] database-wide foreign-key "
                    "diagnostic is unavailable for a pre-existing schema "
                    "shape; Stage 0 capability remains fail-closed"
                )
            unrelated_foreign_key_errors = [
                row for row in global_foreign_key_errors
                if row[0] not in _KOBO_TWO_WAY_TABLE_NAMES
            ]
            if unrelated_foreign_key_errors:
                log.warning(
                    "[kobo-two-way-stage0] %d pre-existing foreign-key "
                    "violation(s) remain outside Stage 0-owned tables; continuing",
                    len(unrelated_foreign_key_errors),
                )

    # The mutable row plus durable per-book guard cover UPDATE, replacement,
    # and delete/reinsert downgrade attempts.  A deliberate privacy purge
    # erases both records; ordinary state deletion leaves the knowledge guard.
    _ensure_kobo_opaque_present_guards(engine)

    log.info("[kobo-two-way-stage0] additive schema ready; runtime ownership unchanged")


def downgrade_device_management_slice(engine):
    """Manual rollback for the additive, NULL-backfilled management schema."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS device_retired_assignment"))
        conn.execute(text("DROP TABLE IF EXISTS annotation_device_state"))
        conn.execute(text("DROP INDEX IF EXISTS ix_annotation_assigned_device"))
        conn.execute(text("DROP INDEX IF EXISTS ix_annotation_origin_device"))
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(annotation)"))}
        for name in ("routing_revision", "assigned_device_id", "origin_device_id"):
            if name in existing:
                conn.execute(text(f"ALTER TABLE annotation DROP COLUMN {name}"))


def downgrade_multi_device_annotation_safe_slice(engine):
    """Manual rollback; refuses to clobber content ids edited after migration."""
    with engine.begin() as conn:
        tables = {row[0] for row in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))}
        if "annotation_content_id_migration" in tables:
            conflicts = conn.execute(text(
                "SELECT COUNT(*) FROM annotation_content_id_migration m JOIN annotation a "
                "ON a.id=m.annotation_row_id WHERE a.content_id != m.normalized_content_id"
            )).scalar()
            if conflicts:
                raise RuntimeError("content ids changed after migration; refusing lossy downgrade")
            conn.execute(text(
                "UPDATE annotation SET content_id=(SELECT original_content_id FROM "
                "annotation_content_id_migration m WHERE m.annotation_row_id=annotation.id) "
                "WHERE id IN (SELECT annotation_row_id FROM annotation_content_id_migration)"
            ))
            conn.execute(text("DROP TABLE annotation_content_id_migration"))
        if "device_identity" in tables:
            conn.execute(text("DROP TABLE device_identity"))
        if "device" in tables:
            conn.execute(text("DROP TABLE device"))
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(annotation)"))}
        if "client_modified_at" in columns:
            conn.execute(text("ALTER TABLE annotation DROP COLUMN client_modified_at"))


def migrate_annotation_koreader_identity(engine, _session):
    """Add KOReader-native locator columns and enforce merge identity.

    The unique index makes parallel device pushes converge on one canonical
    row.  Existing duplicates are not guessed away: aborting the transaction
    is rollback-safe and leaves an operator-visible data repair requirement.
    """
    with engine.begin() as conn:
        if not conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='annotation'"
        )).first():
            return
        duplicate = conn.execute(text(
            "SELECT user_id, book_id, annotation_id, COUNT(*) AS n "
            "FROM annotation GROUP BY user_id, book_id, annotation_id "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )).first()
        if duplicate:
            raise RuntimeError(
                "annotation identity migration found duplicate "
                f"(user={duplicate[0]}, book={duplicate[1]}, id={duplicate[2]!r})"
            )
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(annotation)"))}
        for name in ("start_xpointer", "end_xpointer"):
            if name not in existing:
                try:
                    conn.execute(text(f"ALTER TABLE annotation ADD COLUMN {name} TEXT"))
                except exc.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_annotation_user_book_annotation "
            "ON annotation(user_id, book_id, annotation_id)"
        ))


def migrate_annotation_decouple_source_target(engine, _session):
    """Decouple annotation origin from sync target.

    8-step transactional migration. Idempotent. See
    notes/2026-05-21-annotation-decouple-source-target-DESIGN.md §4.

    Refuses destructive steps if the sanity check (step 4) finds a
    ``synced_to_hardcover`` row left without a matching
    ``annotation_sync_target`` row (an incomplete backfill) — DB stays in
    pre-migration state in that case. Organic pipeline-written target rows
    are ignored, so a Hardcover-synced server no longer trips the check.

    Note on co-existence: ``add_missing_tables`` (which runs before this
    migration in ``migrate_Database``) creates an empty ``annotation``
    table via ``Base.metadata.create_all``. We detect that case + drop
    the empty placeholder before renaming the real ``kobo_annotation_sync``
    onto it.
    """
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(engine)
    tables = set(inspector.get_table_names())

    # Idempotency check: migration is already complete when the legacy
    # table is gone AND the target table exists.
    if "kobo_annotation_sync" not in tables and "annotation" in tables:
        log.info("[annotation-decouple-migration] target schema already in place; skip")
        return
    if "kobo_annotation_sync" not in tables and "annotation" not in tables:
        log.info("[annotation-decouple-migration] fresh install; nothing to migrate")
        return

    log.info("[annotation-decouple-migration] starting")
    try:
        with engine.begin() as conn:
            _migrate_step1_create_target_table(conn)
            inserted = _migrate_step2_backfill_sync_state(conn)
            updated = _migrate_step3_fix_source_values(conn)
            _migrate_step4_sanity_check(conn)
            # Drop the empty ORM-created annotation placeholder so RENAME
            # below has a clean target. The placeholder has zero rows
            # because add_missing_tables only just created it this boot.
            if "annotation" in inspector.get_table_names():
                placeholder_count = conn.execute(text(
                    "SELECT COUNT(*) FROM annotation"
                )).scalar()
                if placeholder_count == 0:
                    conn.execute(text("DROP TABLE annotation"))
                else:
                    raise RuntimeError(
                        "[annotation-decouple-migration] both kobo_annotation_sync "
                        f"and annotation tables exist + annotation has "
                        f"{placeholder_count} rows; manual investigation required"
                    )
            _migrate_step5_rename_table(conn)
            _migrate_step6_rename_indexes(conn)
            _migrate_step7_drop_old_columns(conn)
            log.info(
                "[annotation-decouple-migration] complete: "
                "%d sync_target rows backfilled, %d source values corrected",
                inserted, updated,
            )
    except Exception:
        log.exception("[annotation-decouple-migration] failed; rolling back")
        raise


def migrate_kobo_bookmark_created_at(engine, _session):
    """Add the nullable ``created_at`` column to ``kobo_bookmark`` — the
    "started reading" date, stamped on the first sync with progress > 0.
    Idempotent.

    PRAGMA-guarded like `migrate_annotation_device_origin`, but deliberately
    two-step: the column check commits first, then the ALTER runs through
    `_run_ddl_with_retry` (which owns its connection and retries
    "database is locked"). The window between the two is closed by treating
    a duplicate-column error from the ADD COLUMN as a no-op. Nullable
    column, zero data risk.
    """
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kobo_bookmark'"
        )).fetchall()
        if not rows:
            return
        existing = {
            row[1] for row in conn.execute(text(
                "PRAGMA table_info(kobo_bookmark)"
            )).fetchall()
        }
        if "created_at" in existing:
            return
    try:
        _run_ddl_with_retry(engine, "ALTER TABLE kobo_bookmark ADD COLUMN created_at DATETIME")
        log.info("[kobo_bookmark] added created_at column")
    except exc.OperationalError as e:
        if "duplicate column" in str(e).lower():
            log.info(
                "[kobo_bookmark] column already present despite "
                "PRAGMA check; treating as idempotent"
            )
        else:
            raise


def migrate_bookmark_format_lowercase(engine, _session):
    """Normalize legacy Bookmark formats and merge case-only duplicates.

    Old classic-reader writes used uppercase formats while the SPA uses
    lowercase.  SQLite installations upgraded from an older schema can retain
    both values despite the current NOCASE column declaration.  The largest id
    is the newest-write proxy because both writers replace rows on save.
    """
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bookmark'"
        )).fetchall()
        if not rows:
            return

        ambiguous = conn.execute(text(
            "SELECT user_id, book_id, lower(format), COUNT(*) "
            "FROM bookmark GROUP BY user_id, book_id, lower(format) HAVING COUNT(*) > 1"
        )).fetchall()
        merged = conn.execute(text(
            "DELETE FROM bookmark WHERE id NOT IN ("
            "SELECT MAX(id) FROM bookmark GROUP BY user_id, book_id, lower(format))"
        )).rowcount
        # COLLATE BINARY forces a case-SENSITIVE comparison: the ``format``
        # column is declared ``COLLATE NOCASE``, so a bare ``format <>
        # lower(format)`` compares case-insensitively and is always false —
        # the UPDATE would silently no-op on every NOCASE database and leave
        # stray uppercase rows behind. Binary comparison lowercases them too.
        updated = conn.execute(text(
            "UPDATE bookmark SET format = lower(format) "
            "WHERE format <> lower(format) COLLATE BINARY"
        )).rowcount
        if ambiguous:
            log.warning("[bookmark-format-migration] merged %d ambiguous case-only groups", len(ambiguous))
        if merged or updated:
            log.info("[bookmark-format-migration] merged %d rows; lowercased %d rows", merged, updated)


def migrate_Database(_session):
    engine = _session.bind
    add_missing_tables(engine, _session)
    migrate_registration_table(engine, _session)
    migrate_user_session_table(engine, _session)
    migrate_user_table(engine, _session)
    # Normalize legacy NULL view_settings → '{}' so get_view_property can't
    # 500 the entire web UI for any user whose row was imported pre-default.
    migrate_user_view_settings_null(engine, _session)
    migrate_shelf_table(engine, _session)
    migrate_oauth_provider_table(engine, _session)
    migrate_config_table(engine, _session)
    migrate_magic_shelf_table(engine, _session)
    migrate_kobo_synced_book_uuid(engine, _session)
    migrate_kobo_unique_constraints(engine, _session)
    migrate_kobo_deleted_book(engine, _session)
    migrate_kobo_bookmark_created_at(engine, _session)
    migrate_bookmark_format_lowercase(engine, _session)
    # Must run before config_sql.load_configuration (it does — ub.init_db
    # precedes config load in cps/__init__.py) so the flipped value is live
    # the same boot.
    migrate_kobo_magic_shelf_intent(engine, _session)
    migrate_kobo_annotation_sync_h1_columns(engine, _session)
    migrate_annotation_decouple_source_target(engine, _session)
    migrate_annotation_polymorphic_position(engine, _session)
    migrate_annotation_device_origin(engine, _session)
    migrate_annotation_koreader_identity(engine, _session)
    migrate_multi_device_annotation_safe_slice(engine, _session)
    migrate_device_management_slice(engine, _session)
    migrate_kobo_two_way_annotation_sync(engine, _session)
    migrate_book_cover_preview_table(engine, _session)
    migrate_notice_tables(engine, _session)
    migrate_kepub_package_repair_disposition(engine, _session)
    migrate_dismissed_duplicate_groups_table(engine, _session)

    # Ensure progress syncing tables in app.db (user-related tables).
    # Schema invariant — must not be gated on KOReader sync being enabled.
    # See fork #219.
    from .progress_syncing.models import ensure_app_db_tables
    ensure_app_db_tables(engine.raw_connection())
    
    # Migrate system magic shelves for existing users
    try:
        from . import magic_shelf
        
        # Get all valid current template names
        current_template_names = {template['name'] for template in magic_shelf.SYSTEM_SHELF_TEMPLATES.values()}
        
        log.info("Migrating system magic shelves...")
        users = _session.query(User).filter(User.role != constants.ROLE_ANONYMOUS).all()
        total_deleted = 0
        total_created = 0
        
        for user in users:
            # Get all system shelves for this user
            user_system_shelves = _session.query(MagicShelf).filter(
                MagicShelf.user_id == user.id,
                MagicShelf.is_system == True
            ).all()
            
            # Delete system shelves that don't match current templates
            for shelf in user_system_shelves:
                if shelf.name not in current_template_names:
                    # This is an old/deprecated system shelf - delete it
                    _session.query(MagicShelfCache).filter_by(shelf_id=shelf.id).delete()
                    _session.query(HiddenMagicShelfTemplate).filter_by(shelf_id=shelf.id).delete()
                    _session.delete(shelf)
                    total_deleted += 1
                    log.debug(f"Deleted deprecated system shelf '{shelf.name}' (ID: {shelf.id}) for user {user.id}")
            
            # Get user's template-based hide preferences (not shelf-specific)
            hidden_templates = _session.query(HiddenMagicShelfTemplate.template_key).filter(
                HiddenMagicShelfTemplate.user_id == user.id,
                HiddenMagicShelfTemplate.template_key.isnot(None)
            ).all()
            hidden_keys = {ht.template_key for ht in hidden_templates}
            
            # Create missing current templates
            templates_to_create = []
            for template_key, template_data in magic_shelf.SYSTEM_SHELF_TEMPLATES.items():
                # Skip if user has hidden this template type
                if template_key in hidden_keys:
                    continue
                
                # Check if user already has this current template
                has_template = _session.query(MagicShelf).filter(
                    MagicShelf.user_id == user.id,
                    MagicShelf.name == template_data['name'],
                    MagicShelf.is_system == True
                ).first()
                
                if not has_template:
                    templates_to_create.append(template_key)
            
            # Create missing templates
            if templates_to_create:
                created = magic_shelf.create_system_magic_shelves(user.id, templates_to_create)
                total_created += created
        
        if total_deleted > 0 or total_created > 0:
            _session.commit()
            log.info(f"System shelf migration complete: {total_deleted} old shelves removed, {total_created} new shelves created")
    except Exception as e:
        log.error(f"Error during system shelf migration: {e}")
        _session.rollback()

    # Keep trigger-backed invariants intact if any current or future startup
    # migration rebuilt the guarded table after the Stage 0 migration ran.
    _ensure_kobo_opaque_present_guards(engine)


def clean_database(_session):
    # Remove expired remote login tokens
    now = datetime.now()
    try:
        _session.query(RemoteAuthToken).filter(now > RemoteAuthToken.expiration).\
            filter(RemoteAuthToken.token_type != 1).delete()
        _session.commit()
    except exc.OperationalError:  # Database is not writeable
        print('Settings database is not writeable. Exiting...')
        sys.exit(2)


# Save downloaded books per user in calibre-web's own database
def update_download(book_id, user_id):
    check = session.query(Downloads).filter(Downloads.user_id == user_id).filter(Downloads.book_id == book_id).first()

    if not check:
        new_download = Downloads(user_id=user_id, book_id=book_id)
        session.add(new_download)
        try:
            session.commit()
        except exc.OperationalError:
            session.rollback()


# Delete non existing downloaded books in calibre-web's own database
def delete_download(book_id):
    session.query(Downloads).filter(book_id == Downloads.book_id).delete()
    try:
        session.commit()
    except exc.OperationalError:
        session.rollback()

# Generate user Guest (translated text), as anonymous user, no rights
def create_anonymous_user(_session):
    user = User()
    user.name = "Guest"
    user.email = 'no@email'
    user.role = constants.ROLE_ANONYMOUS
    user.password = ''

    _session.add(user)
    try:
        _session.commit()
        # Note: Anonymous users don't get system shelves
        # They will be created if/when the user registers
    except Exception:
        _session.rollback()


# Generate User admin with admin123 password, and access to everything
def create_admin_user(_session):
    user = User()
    user.name = "admin"
    user.email = "admin@example.org"
    user.role = constants.ADMIN_USER_ROLES
    user.sidebar_view = constants.ADMIN_USER_SIDEBAR

    user.password = generate_password_hash(constants.DEFAULT_PASSWORD)

    _session.add(user)
    try:
        _session.commit()
        # Create system magic shelves for admin user
        try:
            from . import magic_shelf
            magic_shelf.create_system_magic_shelves(user.id)
        except Exception as e:
            log.error(f"Failed to create system magic shelves for admin: {e}")
    except Exception:
        _session.rollback()


def create_system_magic_shelves_for_user(user_id):
    """
    Create system magic shelves for a user if they don't already exist.
    Should be called after user creation.
    """
    try:
        from . import magic_shelf
        return magic_shelf.create_system_magic_shelves(user_id)
    except Exception as e:
        log.error(f"Failed to create system magic shelves for user {user_id}: {e}")
        return 0


def init_db_thread():
    global app_DB_path
    if not app_DB_path:
        # Without this guard, 'sqlite:///{}'.format(None) builds the URL
        # 'sqlite:///None' and SQLite silently creates (and writes real
        # data into) a phantom DB file literally named 'None' in the
        # working directory — that's how the stray 0-byte 'None' file got
        # committed in #440 (the annotation-backup worker fires this in
        # contexts where init_db() was never called, e.g. unit tests).
        raise RuntimeError(
            "ub.init_db_thread() called before ub.init_db(); app_DB_path "
            "is unset, refusing to create a stray 'None' SQLite file")
    engine = create_engine('sqlite:///{0}'.format(app_DB_path), echo=False,
                           connect_args={'timeout': 30})

    Session = scoped_session(sessionmaker())
    Session.configure(bind=engine)
    return Session()


def init_db(app_db_path):
    # Open session for database connection
    global session
    global app_DB_path

    app_DB_path = app_db_path
    engine = create_engine('sqlite:///{0}'.format(app_db_path), echo=False,
                           connect_args={'timeout': 30})

    Session = scoped_session(sessionmaker())
    Session.configure(bind=engine)
    session = Session()

    _healthcheck_app_db(app_db_path)

    if os.path.exists(app_db_path):
        Base.metadata.create_all(engine)
        migrate_Database(session)
        clean_database(session)
    else:
        Base.metadata.create_all(engine)
        _ensure_kobo_opaque_present_guards(engine)
        create_admin_user(session)
        create_anonymous_user(session)


def _healthcheck_app_db(app_db_path: str) -> None:
    """Basic startup checks for app.db path, permissions, and integrity."""
    try:
        if not app_db_path:
            log.error("app.db path is empty; cannot validate settings database")
            return
        if os.path.isdir(app_db_path):
            log.error("app.db path points to a directory: %s", app_db_path)
            return
        if not os.path.exists(app_db_path):
            log.warning("app.db not found at %s; it will be created on first run", app_db_path)
            return
        if not os.access(app_db_path, os.W_OK):
            log.error("app.db is not writable: %s", app_db_path)
        network_share_mode = os.environ.get("NETWORK_SHARE_MODE", "false").lower() in ("1", "true", "yes")
        if network_share_mode:
            log.info("Skipping PRAGMA quick_check for app.db due to NETWORK_SHARE_MODE=true")
            return
        try:
            with sqlite3.connect(app_db_path, timeout=5) as con:
                con.execute("PRAGMA quick_check;")
        except sqlite3.OperationalError as e:
            log.error("app.db integrity/lock check failed for %s: %s", app_db_path, e)
    except Exception as e:
        log.error("app.db healthcheck failed for %s: %s", app_db_path, e)

def password_change(user_credentials=None):
    if user_credentials:
        username, password = user_credentials.split(':', 1)
        user = session.query(User).filter(func.lower(User.name) == username.lower()).first()
        if user:
            if not password:
                print("Empty password is not allowed")
                sys.exit(4)
            try:
                from .helper import valid_password
                user.password = generate_password_hash(valid_password(password))
            except Exception:
                print("Password doesn't comply with password validation rules")
                sys.exit(4)
            # #1318: this used to read `session_commit() == ""`, which was
            # unconditionally true, so the failure branch below was dead and an
            # admin whose write rolled back was told the password changed and
            # got exit 0 — locked out, believing otherwise.
            if session_commit():
                print("Password for user '{}' changed".format(username))
                sys.exit(0)
            else:
                print("Failed changing password")
                sys.exit(3)
        else:
            print("Username '{}' not valid, can't change password".format(username))
            sys.exit(3)


def get_new_session_instance():
    new_engine = create_engine('sqlite:///{0}'.format(app_DB_path), echo=False,
                               connect_args={'timeout': 30})
    new_session = scoped_session(sessionmaker())
    new_session.configure(bind=new_engine)

    atexit.register(lambda: new_session.remove() if new_session else True)

    return new_session


def dispose():
    global session

    old_session = session
    session = None
    if old_session:
        try:
            old_session.close()
        except Exception:
            pass
        if old_session.bind:
            try:
                old_session.bind.dispose()
            except Exception:
                pass

def session_commit(success=None, _session=None):
    """Commit, reporting honestly whether the write landed.

    Returns ``True`` when the transaction committed and ``False`` when it was
    rolled back.  Most callers commit-and-forget and can keep ignoring this;
    callers whose answer to the user depends on the write actually landing MUST
    check it (#1318 — the previous ``""`` return could not express failure, so
    a rolled-back bookmark was still answered 201 and a failed admin password
    reset still exited 0).

    The caught set is deliberately unchanged: anything else — an
    ``IntegrityError`` from a racing writer, say — still propagates, because
    callers such as ``services/reading_position`` contain exactly that in a
    savepoint of their own.
    """
    s = _session if _session else session
    try:
        s.commit()
        if success:
            log.info(success)
        return True
    except (exc.OperationalError, exc.InvalidRequestError) as e:
        s.rollback()
        log.error_or_exception(e)
        return False


def session_flush(_session=None):
    """Settle pending writes, reporting honestly whether they landed.

    The ``session_commit`` shape, one step earlier.  A route that needs its own
    write settled before opening a savepoint for an optional follow-up write
    uses this, so a failure belonging to the required write is raised where it
    is owned rather than inside the optional write's guard (#1318).

    Broader than ``session_commit`` on purpose: a flush is where constraint
    violations surface, and any flush failure means the write did not land.
    """
    s = _session if _session else session
    try:
        s.flush()
        return True
    except exc.SQLAlchemyError as e:
        s.rollback()
        log.error_or_exception(e)
        return False
