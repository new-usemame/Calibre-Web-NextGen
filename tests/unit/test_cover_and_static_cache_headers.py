# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests: the browser was allowed to cache nothing.

Reported symptom — "the site is very slow to scroll", on /app/book/151. It was
not CPU (the SPA attaches no scroll handler on that page and holds 60fps at 6x
throttle); it was network work firing as covers came into view.

Three defects, all pinned here:

1. Every cover was served ``Cache-Control: no-cache``. ``helper`` calls
   ``send_from_directory`` without a ``max_age``, and Flask's
   ``SEND_FILE_MAX_AGE_DEFAULT`` is ``None`` — which makes ``send_file`` emit
   ``no-cache``. Nothing in ``cps/`` sets that config. So a grid that
   lazy-loads covers issued one revalidation per card, per scroll, for bytes
   already on disk.

2. The SPA detail page asked for the ``og`` cover. ``web.get_cover`` maps
   ``og`` to ``constants.COVER_THUMBNAIL_ORIGINAL``, which is ``0`` — falsy —
   so ``get_book_cover_internal``'s ``if resolution:`` branch never runs and
   the raw library ``cover.jpg`` is served: a ~1250x2000 JPEG for a ~280 CSS-px
   column, when the ``md`` thumbnail (524x840 WebP) already exists.

3. The content-hashed SPA bundle under ``/static/app/assets/`` was revalidated
   on every load for the same reason as (1), even though its filename changes
   whenever its bytes do.

The safety property these tests exist to hold: caching is opt-in **per URL**,
never per duration, and the URL is not taken on trust. Two conditions must both
hold before a cover gets a long lifetime —

  1. the URL's ``c=`` token MATCHES this book's current cover version
     (``cps.cover_version``, microsecond ``Books.last_modified``), and
  2. the bytes being sent are the ones that URL permanently names — never the
     original standing in for a thumbnail that has not been generated yet.

— and everything else keeps ``no-cache``, which is main's behaviour for every
cover URL, so nothing can regress into staleness.
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import flask
import pytest

pytestmark = pytest.mark.unit


LAST_MODIFIED = datetime.datetime(2026, 8, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
# The token is whatever cps.cover_version says it is — asserting against a
# hand-computed literal here would just be a second, drifting definition.
LAST_MODIFIED_EPOCH = str(int(LAST_MODIFIED.timestamp() * 1_000_000))


# --------------------------------------------------------------------------- #
# 1. Cover responses                                                            #
# --------------------------------------------------------------------------- #

def _library_with_cover(tmp_path):
    """A minimal on-disk Calibre library holding one book folder + cover.jpg."""
    book_dir = tmp_path / "Some Author" / "Some Book (42)"
    book_dir.mkdir(parents=True)
    (book_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"jpeg-ish bytes" * 8)
    return book_dir


def _cover_app(monkeypatch, tmp_path):
    """Serve a real book cover through the real helper, on a throwaway app.

    Exercises ``get_book_cover_internal``'s on-disk ``cover.jpg`` branch — the
    one the detail page hits — rather than source-pinning the fix, so the test
    fails on the actual header a browser would receive.
    """
    from cps import helper

    _library_with_cover(tmp_path)
    monkeypatch.setattr(helper.config, "get_book_path", lambda: str(tmp_path), raising=False)
    monkeypatch.setattr(helper.config, "config_use_google_drive", False, raising=False)

    book = SimpleNamespace(
        id=42,
        has_cover=1,
        path="Some Author/Some Book (42)",
        last_modified=LAST_MODIFIED,
    )

    app = flask.Flask(__name__)

    @app.route("/cover/<int:book_id>")
    def cover(book_id):
        return helper.get_book_cover_internal(book)

    return app


def test_versioned_cover_response_is_cacheable(monkeypatch, tmp_path):
    """A cover URL carrying its version token must not be revalidated.

    Red before the fix: ``Cache-Control: no-cache``.
    """
    client = _cover_app(monkeypatch, tmp_path).test_client()
    resp = client.get("/cover/42?c=%s" % LAST_MODIFIED_EPOCH)

    assert resp.status_code == 200
    cache_control = resp.headers.get("Cache-Control", "")
    assert "no-cache" not in cache_control, (
        "a versioned cover URL is still served no-cache: %r" % cache_control)
    assert "max-age=31536000" in cache_control, cache_control
    assert "immutable" in cache_control, cache_control
    # `private`, never `public`: get_book_cover resolves the book through the
    # CURRENT USER's visibility filters, so a shared cache must not reuse one
    # user's cover response for another.
    assert "private" in cache_control, cache_control
    assert "public" not in cache_control, cache_control


def test_unversioned_cover_response_still_revalidates(monkeypatch, tmp_path):
    """A cover URL with no version token has no invalidation story, so it keeps
    the framework default. This is the guard against serving a stale cover: the
    long lifetime is bought by the URL changing, never by the copy expiring."""
    client = _cover_app(monkeypatch, tmp_path).test_client()
    resp = client.get("/cover/42")

    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("Cache-Control", "")


def test_versioned_cover_keeps_conditional_requests_working(monkeypatch, tmp_path):
    """The cache policy must not disturb the ETag / 304 handling already there."""
    client = _cover_app(monkeypatch, tmp_path).test_client()
    first = client.get("/cover/42?c=%s" % LAST_MODIFIED_EPOCH)
    etag = first.headers.get("ETag")
    assert etag

    second = client.get("/cover/42?c=%s" % LAST_MODIFIED_EPOCH,
                        headers={"If-None-Match": etag})
    assert second.status_code == 304


def test_placeholder_cover_is_never_cached_long(monkeypatch, tmp_path):
    """``get_cover_on_failure`` answers both "this book has no cover" and "the
    cover could not be read this time". Pinning the placeholder into a browser
    cache for a year would outlive the transient failure it stands for."""
    from cps import helper

    monkeypatch.setattr(helper.config, "get_book_path", lambda: str(tmp_path), raising=False)
    monkeypatch.setattr(helper.config, "config_use_google_drive", False, raising=False)
    book = SimpleNamespace(id=42, has_cover=0, path="nope", last_modified=LAST_MODIFIED)

    app = flask.Flask(__name__)

    @app.route("/cover/<int:book_id>")
    def cover(book_id):
        return helper.get_book_cover_internal(book)

    resp = app.test_client().get("/cover/42?c=%s" % LAST_MODIFIED_EPOCH)
    assert resp.status_code == 200
    assert "max-age=31536000" not in resp.headers.get("Cache-Control", "")


def test_cover_version_detection_ignores_unrelated_query_args(monkeypatch, tmp_path):
    """Only the version param counts. Any other query string is just a
    different spelling of the same unversioned URL."""
    client = _cover_app(monkeypatch, tmp_path).test_client()
    resp = client.get("/cover/42?foo=bar")
    assert "no-cache" in resp.headers.get("Cache-Control", "")


def test_a_token_that_does_not_match_the_book_is_not_trusted(monkeypatch, tmp_path):
    """Presence of ``c=`` is not proof of anything.

    A stale token from an old page, a hand-typed one, or a third party's link
    names a version we are not serving. Honouring it would let anyone pin an
    arbitrary URL to a year-long copy of whatever the server happens to answer
    with today — including the classic *series* cover URL, whose ``c=`` is a
    rolling calendar stamp and which falls through to a book cover when no
    series thumbnail exists.
    """
    client = _cover_app(monkeypatch, tmp_path).test_client()
    for token in ("garbage", "0", "1", str(int(LAST_MODIFIED_EPOCH) + 1)):
        resp = client.get("/cover/42?c=%s" % token)
        assert "no-cache" in resp.headers.get("Cache-Control", ""), token


def test_immutable_cover_varies_on_cookie(monkeypatch, tmp_path):
    """One browser profile must not reuse user A's cover for user B.

    ``private`` keeps shared caches out, but a browser cache keys on the URL
    plus whatever ``Vary`` names — and logging out does not clear it. The cover
    a request resolves to depends on the session (hidden/archived books are
    per-user), so the session cookie has to be part of the key.
    """
    client = _cover_app(monkeypatch, tmp_path).test_client()
    resp = client.get("/cover/42?c=%s" % LAST_MODIFIED_EPOCH)
    assert "Cookie" in resp.headers.get("Vary", "")


def test_two_replacements_inside_one_second_get_different_tokens():
    """Second resolution is not enough.

    ``mark_book_modified`` stamps ``datetime.now(timezone.utc)``, so two cover
    writes inside one wall-clock second are ordinary (a picker retry, a script).
    At second resolution both would produce the SAME URL, and with a year-long
    lifetime the first image would stay on screen indefinitely.
    """
    from cps.cover_version import cover_version_token

    first = SimpleNamespace(last_modified=datetime.datetime(
        2026, 8, 18, 12, 0, 0, 100000, tzinfo=datetime.timezone.utc))
    second = SimpleNamespace(last_modified=datetime.datetime(
        2026, 8, 18, 12, 0, 0, 900000, tzinfo=datetime.timezone.utc))

    assert cover_version_token(first) != cover_version_token(second)


def test_repeated_stamping_always_advances_the_token():
    """Microsecond RESOLUTION is not microsecond UNIQUENESS.

    ``datetime.now()`` is free to return the same value on successive calls, so
    two cover writes really can land on one timestamp — and with this column
    doubling as a cache key, one timestamp means one URL for two different
    images, cached for a year. This drives the real stamping function rather
    than hand-built datetimes, which is the only way the collision shows up.
    """
    from cps import helper
    from cps.cover_version import cover_version_token

    book = SimpleNamespace(id=42, last_modified=None)
    tokens = []
    for _ in range(200):
        helper.mark_book_modified(book, set_dirty=False)
        tokens.append(cover_version_token(book))

    assert len(set(tokens)) == len(tokens), "two stampings shared a cover version"
    assert tokens == sorted(tokens, key=int), "the token went backwards"


def test_stamping_does_not_chain_off_a_bogus_future_timestamp():
    """The collision nudge is bounded. A row whose timestamp is far in the
    future is bad data; adding a microsecond to it every time would keep the
    book in the future forever — and this column also drives Kobo's
    ``last_modified > sync_token`` selection."""
    from cps import helper

    far_future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
    book = SimpleNamespace(id=42, last_modified=far_future)
    helper.mark_book_modified(book, set_dirty=False)

    assert book.last_modified < far_future


def test_token_arithmetic_is_exact_at_the_extremes():
    """``int(timestamp() * 1_000_000)`` rounds through a float and makes
    adjacent microseconds collide at the edges of the datetime range. A cache
    key cannot be approximately right."""
    from cps.cover_version import cover_version_token

    base = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    tokens = {cover_version_token(SimpleNamespace(
        last_modified=base + datetime.timedelta(microseconds=us))) for us in range(240, 260)}
    assert len(tokens) == 20


def test_naive_timestamps_are_read_as_utc():
    """Calibre stores UTC wall time. Letting ``timestamp()`` apply the process's
    local zone would make the same row produce different tokens on two servers,
    or after a TZ change — a cache miss at best, a drifting version at worst."""
    from cps.cover_version import cover_version_token

    naive = SimpleNamespace(last_modified=datetime.datetime(2026, 8, 1, 12, 0, 0))
    aware = SimpleNamespace(last_modified=datetime.datetime(
        2026, 8, 1, 12, 0, 0, tzinfo=datetime.timezone.utc))

    assert cover_version_token(naive) == cover_version_token(aware)


def _thumbnail_app(monkeypatch, tmp_path, have_webp, have_jpg, generated_at=LAST_MODIFIED):
    """Serve `md` through the real helper with a controllable thumbnail state."""
    from cps import helper

    cache_dir = tmp_path / "thumbs"
    cache_dir.mkdir()
    (cache_dir / "book_42_r2.webp").write_bytes(b"RIFF____WEBPVP8 webp-ish")
    (cache_dir / "book_42_r2.jpg").write_bytes(b"\xff\xd8\xff\xe0jpeg-ish")
    _library_with_cover(tmp_path)

    monkeypatch.setattr(helper.config, "get_book_path", lambda: str(tmp_path), raising=False)
    monkeypatch.setattr(helper.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(helper, "use_IM", False, raising=False)

    thumbs = {
        "webp": SimpleNamespace(filename="book_42_r2.webp", generated_at=generated_at),
        "jpg": SimpleNamespace(filename="book_42_r2.jpg", generated_at=generated_at),
    }
    present = {"webp": have_webp, "jpg": have_jpg}
    monkeypatch.setattr(
        helper,
        "get_book_cover_thumbnails_by_formats",
        lambda book, res, formats: {fmt: thumbs[fmt] for fmt in formats},
        raising=False,
    )

    class _Cache:
        def get_cache_file_exists(self, filename, _type):
            return present["webp" if filename.endswith(".webp") else "jpg"]

        def get_cache_file_dir(self, filename, _type):
            return str(cache_dir)

    monkeypatch.setattr(helper.fs, "FileSystem", _Cache, raising=False)

    book = SimpleNamespace(id=42, has_cover=1, path="Some Author/Some Book (42)",
                           last_modified=LAST_MODIFIED)
    app = flask.Flask(__name__)

    @app.route("/cover/<int:book_id>/md")
    def cover(book_id):
        return helper.get_book_cover_internal(book, resolution=2)

    return app


def test_both_thumbnail_formats_present_is_cacheable(monkeypatch, tmp_path):
    """Nothing further will be generated, so the selection is stable."""
    app = _thumbnail_app(monkeypatch, tmp_path, have_webp=True, have_jpg=True)
    resp = app.test_client().get("/cover/42/md?c=%s" % LAST_MODIFIED_EPOCH)
    assert resp.status_code == 200
    assert "immutable" in resp.headers.get("Cache-Control", "")


def test_serving_the_alternate_thumbnail_format_is_not_immutable(monkeypatch, tmp_path):
    """Only the JPEG exists, so a web request is served JPEG as a STAND-IN while
    the WebP is generated. The same URL will select WebP minutes later, so
    pinning the stand-in would freeze the wrong representation for a year."""
    app = _thumbnail_app(monkeypatch, tmp_path, have_webp=False, have_jpg=True)
    resp = app.test_client().get("/cover/42/md?c=%s" % LAST_MODIFIED_EPOCH)
    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("Cache-Control", "")


def test_a_thumbnail_with_no_generation_time_is_not_immutable(monkeypatch, tmp_path):
    """Without ``generated_at`` the staleness check cannot prove the file belongs
    to the current cover — that is exactly the CWA #1339 shape, a thumbnail left
    behind by a previous book at the same id. Unprovable is not cacheable."""
    app = _thumbnail_app(monkeypatch, tmp_path, have_webp=True, have_jpg=True,
                         generated_at=None)
    resp = app.test_client().get("/cover/42/md?c=%s" % LAST_MODIFIED_EPOCH)
    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("Cache-Control", "")


def test_a_resized_request_falling_back_to_the_original_is_not_immutable(monkeypatch, tmp_path):
    """The other half of the invariant: same URL, same bytes, forever.

    When an ``sm``/``md``/``lg`` thumbnail does not exist yet, the request is
    answered from the full-size ``cover.jpg`` while generation is queued in the
    background — so that same URL will serve the real thumbnail minutes later.
    Marking the stand-in immutable would pin the full-size original for a year
    and defeat the resizing this change exists to deliver.
    """
    from cps import helper

    _library_with_cover(tmp_path)
    monkeypatch.setattr(helper.config, "get_book_path", lambda: str(tmp_path), raising=False)
    monkeypatch.setattr(helper.config, "config_use_google_drive", False, raising=False)
    # No thumbnail rows, and no ImageMagick, so nothing is queued either.
    monkeypatch.setattr(
        helper,
        "get_book_cover_thumbnails_by_formats",
        lambda *args, **kwargs: {},
        raising=False,
    )
    monkeypatch.setattr(helper, "use_IM", False, raising=False)

    book = SimpleNamespace(id=42, has_cover=1, path="Some Author/Some Book (42)",
                           last_modified=LAST_MODIFIED)
    app = flask.Flask(__name__)

    @app.route("/cover/<int:book_id>/<resolution>")
    def cover(book_id, resolution):
        # 2 == constants.COVER_THUMBNAIL_MEDIUM, i.e. the `md` the detail page asks for.
        return helper.get_book_cover_internal(book, resolution=2)

    resp = app.test_client().get("/cover/42/md?c=%s" % LAST_MODIFIED_EPOCH)
    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("Cache-Control", ""), (
        "the full-size stand-in was pinned: %r" % resp.headers.get("Cache-Control"))


# --------------------------------------------------------------------------- #
# 2. What the SPA asks for                                                      #
# --------------------------------------------------------------------------- #

def _book(has_cover=1, last_modified=LAST_MODIFIED):
    return SimpleNamespace(
        id=42,
        title="Cover test",
        series_index=None,
        has_cover=has_cover,
        last_modified=last_modified,
        authors=[],
        series=[],
        data=[],
        tags=[],
        ratings=[],
        comments=[],
        languages=[],
        publishers=[],
        identifiers=[],
        pubdate=None,
    )


def test_detail_payload_does_not_point_at_the_unresized_original():
    """Red before the fix: ``/cover/42/og``, i.e. the raw library file.

    ``og`` maps to COVER_THUMBNAIL_ORIGINAL == 0, which is falsy, so the
    thumbnail branch is skipped entirely and cover.jpg is served whole.
    """
    from cps.api.serializers import serialize_book_detail

    cover_url = serialize_book_detail(_book())["cover_url"]

    assert cover_url is not None
    assert not cover_url.startswith("/cover/42/og"), (
        "detail page still requests the unresized original: %r" % cover_url)
    assert cover_url.startswith("/cover/42/md")


def test_detail_payload_offers_density_candidates_but_never_lg():
    """`sm` at 1x and `md` at 2x cover the detail column's fixed 280px at every
    realistic density. `lg` must never appear: its target height is 4x the base
    and the thumbnail task only downscales when the source is taller, so for
    most covers `lg` IS the original dimensions re-encoded — offering it to a
    280px column would undo the fix this change exists to make."""
    from cps.api.serializers import serialize_book_detail

    payload = serialize_book_detail(_book())
    assert payload["cover_srcset"] == (
        "/cover/42/sm?c=%s 1x, /cover/42/md?c=%s 2x"
        % (LAST_MODIFIED_EPOCH, LAST_MODIFIED_EPOCH))
    assert "/lg" not in payload["cover_srcset"]
    assert "/og" not in payload["cover_srcset"]


def test_cover_urls_are_versioned_by_last_modified():
    """Both SPA serializers must version their cover URLs, or the server-side
    cache policy above can never apply to them."""
    from cps.api.serializers import serialize_book_detail, serialize_book_list_item

    assert serialize_book_list_item(_book())["cover_url"] == (
        "/cover/42/sm?c=%s" % LAST_MODIFIED_EPOCH)
    assert serialize_book_detail(_book())["cover_url"] == (
        "/cover/42/md?c=%s" % LAST_MODIFIED_EPOCH)


def test_cover_urls_degrade_without_a_last_modified():
    """A row with no usable timestamp yields an unversioned URL — which the
    server then refuses to cache long. Never a broken or half-built URL."""
    from cps.api.serializers import serialize_book_detail, serialize_book_list_item

    book = _book(last_modified=None)
    assert serialize_book_list_item(book)["cover_url"] == "/cover/42/sm"
    assert serialize_book_detail(book)["cover_url"] == "/cover/42/md"


def test_coverless_book_has_no_cover_url_or_srcset():
    from cps.api.serializers import serialize_book_detail, serialize_book_list_item

    book = _book(has_cover=0)
    assert serialize_book_list_item(book)["cover_url"] is None
    detail = serialize_book_detail(book)
    assert detail["cover_url"] is None
    assert detail["cover_srcset"] is None


# --------------------------------------------------------------------------- #
# 3. Content-hashed SPA bundle                                                  #
# --------------------------------------------------------------------------- #

def _static_app(tmp_path):
    """A throwaway app serving a static tree through the real after_request."""
    from cps import web as web_mod

    assets = tmp_path / "app" / "assets"
    assets.mkdir(parents=True)
    (assets / "index-CylFqGDm.js").write_text("console.log(1)")
    (assets / "index-BXgU7XrR.css").write_text("body{}")
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "main.js").write_text("console.log(2)")

    app = flask.Flask(__name__, static_folder=str(tmp_path), static_url_path="/static")
    app.after_request(web_mod.add_static_asset_cache_headers)
    return app


@pytest.mark.parametrize("asset", ["index-CylFqGDm.js", "index-BXgU7XrR.css"])
def test_hashed_spa_assets_are_immutable(tmp_path, asset):
    """Red before the fix: ``Cache-Control: no-cache`` on a 640 KB bundle whose
    filename already changes with its content."""
    resp = _static_app(tmp_path).test_client().get("/static/app/assets/%s" % asset)

    assert resp.status_code == 200
    cache_control = resp.headers.get("Cache-Control", "")
    assert "no-cache" not in cache_control, cache_control
    assert "max-age=31536000" in cache_control, cache_control
    assert "immutable" in cache_control, cache_control


def test_a_missing_hashed_asset_is_not_pinned(tmp_path):
    """Caches store negative responses too. A hashed-looking 404 — from a partial
    deploy, a volume that was not mounted yet, or a rollback — must not get a
    year, or the white page outlives the deployment problem that caused it."""
    resp = _static_app(tmp_path).test_client().get("/static/app/assets/missing-AbCd1234.js")

    assert resp.status_code == 404
    assert "max-age=31536000" not in resp.headers.get("Cache-Control", "")


def test_unhashed_static_files_keep_revalidating(tmp_path):
    """The negative control, and the reason the rule is narrow: an upgrade
    changes /static/js/main.js WITHOUT changing its URL, so a long-lived copy
    would pin a user to the previous release's JavaScript."""
    resp = _static_app(tmp_path).test_client().get("/static/js/main.js")

    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("Cache-Control", "")


def test_immutable_asset_predicate_rejects_paths_outside_the_bundle():
    from cps.web import is_immutable_static_asset

    assert is_immutable_static_asset("/static/app/assets/index-CylFqGDm.js")
    assert is_immutable_static_asset("/static/app/assets/Reader-w_2_g-Ap.js")
    # The prefix is ANCHORED, not searched for: request.path is always
    # mount-relative (a reverse-proxy prefix lives in script_root), so a path
    # that merely CONTAINS the bundle directory is not the bundle directory.
    assert not is_immutable_static_asset("/cwa/static/app/assets/Reader-w_2_g-Ap.js")
    # Not content-addressed, or not in the bundle directory.
    assert not is_immutable_static_asset("/static/app/assets/index.js")
    assert not is_immutable_static_asset("/static/js/main.js")
    assert not is_immutable_static_asset("/static/app/index.html")
    assert not is_immutable_static_asset("/static/app/assets/nested/index-CylFqGDm.js")
    assert not is_immutable_static_asset("")
    assert not is_immutable_static_asset(None)


def test_spa_shell_is_not_cached(monkeypatch, tmp_path):
    """The shell NAMES the immutable assets and the next build deletes them
    (Vite emptyOutDir), so a heuristically-cached shell would ask for a filename
    that no longer exists — a white page after an upgrade."""
    import cps.spa as spa_mod

    (tmp_path / "index.html").write_text(
        '<!doctype html><html><head></head><body><div id="root"></div></body></html>')
    monkeypatch.setattr(spa_mod, "_SPA_DIR", str(tmp_path))
    monkeypatch.setenv("CWNG_SPA", "1")

    app = flask.Flask(__name__)
    app.register_blueprint(spa_mod.spa)
    resp = app.test_client().get("/app")

    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("Cache-Control", "")
