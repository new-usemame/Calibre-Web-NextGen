# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A small real library for the KOReader library, pairing and bundle tests.

One in-memory app.db with metadata.db attached (the same shape as production),
real book files and covers on disk, real accounts authenticated through the
real HTTP Basic path, and the real ``kosync`` / ``api_v1`` blueprints on a
bare Flask app. Nothing about the code under test is stubbed; only the
process-wide singletons (sessions, config) are pointed at this world.
"""

import base64
import io
import sys
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

from flask import Flask
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash

from cps import constants, db, ub

NOW = datetime(2026, 9, 1, 12, 0, 0)
FAST_HASH = "pbkdf2:sha256:1000"


def epub_bytes(title):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, "application/epub+zip")
        archive.writestr("META-INF/container.xml", (
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>'))
        archive.writestr("content.opf", (
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:title>%s</dc:title></metadata><manifest><item id="p" '
            'href="p.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="p"/></spine></package>' % title))
        archive.writestr("p.xhtml", "<html><body><p>%s</p></body></html>" % title
                         + "x" * 5000)
    return buffer.getvalue()


def jpeg_bytes(size=(800, 1200), color=(20, 90, 160)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


class LibraryWorld:
    def __init__(self, monkeypatch, tmp_path):
        import cps
        from cps import config, helper
        from cps.cw_login import current_user  # noqa: F401 - proxy import check
        import cps.progress_syncing.protocols.kosync  # noqa: F401

        self.kosync = sys.modules["cps.progress_syncing.protocols.kosync"]
        self.monkeypatch = monkeypatch
        self.engine = create_engine("sqlite://")
        event.listen(
            self.engine, "connect",
            lambda connection, _record: connection.execute(
                "ATTACH DATABASE ':memory:' AS calibre"),
        )
        ub.Base.metadata.create_all(self.engine)
        db.Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

        cdb = self.cdb = object.__new__(db.CalibreDB)
        cdb.session = self.session
        cdb.engine = self.engine
        cdb.config = SimpleNamespace(config_restricted_column=0)
        cdb.reconnect_db = lambda *_args, **_kwargs: None
        cdb.refresh_for_new_data = lambda: None
        cdb._desktop_compat = False

        self.root = tmp_path / "library"
        self.root.mkdir()

        monkeypatch.setattr(ub, "session", self.session)
        monkeypatch.setattr(db.ub, "session", self.session)
        monkeypatch.setattr(cps, "calibre_db", cdb)
        monkeypatch.setattr(helper, "calibre_db", cdb)
        monkeypatch.setattr(self.kosync, "is_koreader_sync_enabled", lambda: True)
        for name, value in (
                ("config_use_google_drive", False),
                ("config_read_column", 0),
                ("config_kobo_sync_magic_shelves", False),
                ("config_allow_reverse_proxy_header_login", False),
                ("config_login_type", constants.LOGIN_STANDARD),
                ("config_restricted_column", 0)):
            monkeypatch.setattr(config, name, value, raising=False)
        monkeypatch.setattr(config, "get_book_path", lambda: str(self.root))

        self.app = Flask(__name__)
        self.app.secret_key = "koreader-library-world"
        from flask_babel import Babel
        Babel(self.app)
        self.app.register_blueprint(self.kosync.kosync)
        self.client = self.app.test_client()
        self._authors = {}
        self._series = {}
        self._tags = {}

    def close(self):
        self.session.close()
        self.engine.dispose()

    # -- the website half ---------------------------------------------------

    def enable_web(self, *, rate_limits=False, spa_available=True):
        """Add the website: api_v1 and spa blueprints with real session login.

        Call before the first request. ``browser(name)`` then returns a client
        signed in as that account through the production login manager.
        """
        from cps import config, limiter
        from cps.MyLoginManager import MyLoginManager
        from cps.api import api_v1
        from cps.cw_login import login_user
        from cps.spa import spa
        import cps.api.auth as api_auth
        import cps.api.koreader_devices as koreader_devices
        from cps.progress_syncing.protocols import kosync_pairing

        self.monkeypatch.setattr(config, "config_anonbrowse", 0, raising=False)
        self.monkeypatch.setattr(koreader_devices, "is_koreader_sync_enabled", lambda: True)
        self.monkeypatch.setattr(api_auth, "is_koreader_sync_enabled", lambda: True)
        self.monkeypatch.setattr(kosync_pairing.spa, "spa_available", lambda: spa_available)
        self.app.config.update(WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=rate_limits,
                               RATELIMIT_STORAGE_URI="memory://")
        limiter.init_app(self.app)
        if rate_limits:
            limiter.reset()
        manager = MyLoginManager(self.app)
        manager.anonymous_user = ub.Anonymous
        ub.create_anonymous_user(self.session)  # the Guest row ub.Anonymous reads

        @manager.user_loader
        def _load_user(user_id, _random, _session_key):
            return self.session.get(ub.User, int(user_id))

        @self.app.route("/test/login/<name>", methods=["POST"])
        def _test_login(name):
            login_user(self.session.query(ub.User).filter(ub.User.name == name).one())
            return "signed in"

        self.app.register_blueprint(api_v1)
        self.app.register_blueprint(spa)

    def browser(self, name=None):
        """A test client, signed in as ``name`` when given."""
        client = self.app.test_client()
        if name is not None:
            assert client.post("/test/login/%s" % name).status_code == 200
        return client

    def sync_switch(self, enabled):
        """The admin's KOReader sync switch, as every KOReader route reads it."""
        import cps.api.auth as api_auth
        import cps.api.koreader_devices as koreader_devices
        for module in (self.kosync, koreader_devices, api_auth):
            self.monkeypatch.setattr(module, "is_koreader_sync_enabled", lambda: enabled)

    def freeze_pairing_clock(self, start=NOW):
        """Drive the pairing service's clock by hand: ``advance(seconds)``."""
        from cps.services import koreader_pairing
        self.pairing_now = start
        self.monkeypatch.setattr(koreader_pairing, "utcnow", lambda: self.pairing_now)

    def advance(self, seconds):
        from datetime import timedelta
        self.pairing_now = self.pairing_now + timedelta(seconds=seconds)

    # -- accounts -----------------------------------------------------------

    def add_user(self, name, *, password="secret", download=True, shelf_only=False,
                 my_library=False, denied_tags="", locale="en"):
        role = constants.ROLE_USER | (constants.ROLE_DOWNLOAD if download else 0)
        user = ub.User(
            name=name, email="%s@example.invalid" % name,
            password=generate_password_hash(password, method=FAST_HASH),
            role=role, default_language="all", locale=locale,
            kobo_only_shelves_sync=1 if shelf_only else 0,
            has_own_library=my_library, user_library_seeded=my_library,
            denied_tags=denied_tags,
        )
        self.session.add(user)
        self.session.commit()
        return user

    @staticmethod
    def basic(name, password="secret"):
        token = base64.b64encode(("%s:%s" % (name, password)).encode()).decode()
        return {"Authorization": "Basic " + token}

    def device_headers(self, name, password="secret", device_id="kindle-1"):
        headers = self.basic(name, password)
        headers.update({"X-CWNG-Device-ID": device_id,
                        "X-CWNG-Device-Name": "Kindle Paperwhite"})
        return headers

    # -- books --------------------------------------------------------------

    def _author(self, name):
        if name not in self._authors:
            self._authors[name] = db.Authors(name, name)
        return self._authors[name]

    def add_book(self, book_id, title, *, authors=("Ann Author",), formats=("EPUB",),
                 cover=True, series=None, series_index=1.0, tags=()):
        folder = "%s/%s (%d)" % (authors[0], title, book_id)
        book = db.Books(title, title, authors[0], NOW, NOW, series_index, NOW,
                        folder, 1 if cover else None, [], [])
        book.id = book_id
        book.uuid = "uuid-%d" % book_id
        for name in authors:
            book.authors.append(self._author(name))
        if series:
            self._series.setdefault(series, db.Series(series, series))
            book.series.append(self._series[series])
        for tag in tags:
            self._tags.setdefault(tag, db.Tags(tag))
            book.tags.append(self._tags[tag])
        self.session.add(book)
        directory = self.root / folder
        directory.mkdir(parents=True)
        stem = "%s - %s" % (title, authors[0])
        for fmt in formats:
            payload = (epub_bytes(title) if fmt == "EPUB"
                       else ("%s file of %s" % (fmt, title)).encode() * 50)
            (directory / ("%s.%s" % (stem, fmt.lower()))).write_bytes(payload)
            self.session.add(db.Data(book_id, fmt, len(payload), stem))
        if cover:
            (directory / "cover.jpg").write_bytes(jpeg_bytes())
        self.session.commit()
        return book

    def book_file(self, book_id, fmt):
        data = (self.session.query(db.Data)
                .filter(db.Data.book == book_id, db.Data.format == fmt).one())
        book = self.session.get(db.Books, book_id)
        return self.root / book.path / ("%s.%s" % (data.name, fmt.lower()))

    def shelf(self, user, name, book_ids, *, kobo_sync=False, uuid=None):
        shelf = ub.Shelf(name=name, user_id=user.id, is_public=0,
                         kobo_sync=kobo_sync)
        if uuid:
            shelf.uuid = uuid
        self.session.add(shelf)
        self.session.flush()
        for order, book_id in enumerate(book_ids, start=1):
            link = ub.BookShelf(shelf=shelf.id, book_id=book_id, order=order)
            link.ub_shelf = shelf
            self.session.add(link)
        self.session.commit()
        return shelf

    def position(self, user, book_id, percent,
                 status=ub.ReadBook.STATUS_IN_PROGRESS):
        """A synced reading position, shaped as every production writer
        leaves it: the read-status row owning its reading-state graph."""
        row = ub.ReadBook(user_id=user.id, book_id=book_id, read_status=status,
                          times_started_reading=1)
        state = ub.KoboReadingState(user_id=user.id, book_id=book_id)
        state.current_bookmark = ub.KoboBookmark(progress_percent=percent)
        state.statistics = ub.KoboStatistics()
        row.kobo_reading_state = state
        self.session.add(row)
        self.session.commit()

    def read_row(self, user, book_id):
        return (self.session.query(ub.ReadBook)
                .filter(ub.ReadBook.user_id == user.id,
                        ub.ReadBook.book_id == book_id).first())


def utcnow():
    return datetime.now(timezone.utc)
