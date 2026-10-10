# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Book-centric reading-source contract and optional Storyteller adapter."""

from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from zipfile import ZipFile

import pytest


pytestmark = pytest.mark.unit


def _clock(day, hour):
    return datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc)


@pytest.fixture
def patient_chapter_resume(monkeypatch):
    """Let the chapter worker finish, however loaded the machine is.

    The production budget is 50 ms, so a slow disk can never stall the hub.
    These tests assert what the worker decides; under that budget a busy
    runner times the worker out, which fails the fallback test and makes the
    refusal test pass without deciding anything.
    """
    from cps.services import kobo_resume

    monkeypatch.setattr(kobo_resume, "RESUME_TIMEOUT_SECONDS", 10.0)


def test_device_sources_remain_distinct_and_are_last_reports(monkeypatch):
    from cps.services import reading_sources

    devices = [
        SimpleNamespace(
            id=1, public_id="browser-public", kind="webreader",
            display_name="Browser", active=True,
        ),
        SimpleNamespace(
            id=2, public_id="clara-public", kind="kobo",
            display_name="Kobo Clara", active=True,
        ),
    ]
    positions = [
        SimpleNamespace(
            device_id=1, progress_percent=10.0478, cfi="epubcfi(/6/4!/4/2:4)",
            location_source=None, location_type=None, location_value=None,
            content_source_progress_percent=None,
            client_modified_at=_clock(13, 20), server_modified_at=_clock(13, 20),
            rehydrate_needed=False,
        ),
        SimpleNamespace(
            device_id=2, progress_percent=8.0, cfi=None,
            location_source="OEBPS/chapter001.xhtml",
            location_type="KoboSpan", location_value="kobo.44.10",
            content_source_progress_percent=56.0,
            client_modified_at=_clock(2, 3), server_modified_at=_clock(2, 3),
            rehydrate_needed=False,
        ),
    ]
    monkeypatch.setattr(
        reading_sources, "exact_resume",
        lambda *_args: {
            "cfi": "epubcfi(/6/6!/4/44:10)", "epub_sha256": "a" * 64,
        },
    )

    rows = reading_sources.device_source_rows(devices, positions, book_id=540)

    assert [row["id"] for row in rows] == [
        "device:browser-public", "device:clara-public",
    ]
    assert rows[0]["observation"] == "last_reported"
    assert rows[0]["resume"] == {
        "percentage": 10.0478,
        "cfi": "epubcfi(/6/4!/4/2:4)",
        "exact": True,
    }
    assert rows[1]["progress_percent"] == 8.0
    assert rows[1]["chapter_progress_percent"] == 56.0
    assert rows[1]["resume"]["epub_sha256"] == "a" * 64
    assert rows[1]["writeback"] == "read_only"


def test_resolved_source_does_not_invent_device_provenance():
    from cps.services.reading_sources import resolved_source_row

    row = resolved_source_row(SimpleNamespace(
        progress_percent=29.0,
        last_modified=_clock(13, 19),
        location_source="OEBPS/chapter001.xhtml",
        location_type="KoboSpan",
        location_value="kobo.44.10",
    ), book_id=540, exact={
        "cfi": "epubcfi(/6/6!/4/44:10)", "epub_sha256": "b" * 64,
    })

    assert row["label"] == "Other saved position"
    assert row["kind"] == "resolved"
    assert row["provenance"] == "unknown"
    assert "device" not in row
    assert row["resume"]["exact"] is True


def test_kobo_span_falls_back_to_fingerprinted_chapter_progress(
        monkeypatch, tmp_path, patient_chapter_resume):
    from cps.services import reading_sources

    epub = tmp_path / "book.epub"
    with ZipFile(epub, "w") as archive:
        archive.writestr("OEBPS/chapter001.xhtml", "<html><body>passage</body></html>")
    monkeypatch.setattr(reading_sources, "exact_resume", lambda *_args: None)
    position = SimpleNamespace(
        device_id=2, progress_percent=8.0, cfi=None,
        location_source="OEBPS/chapter001.xhtml",
        location_type="KoboSpan", location_value="kobo.44.10",
        content_source_progress_percent=56.0,
        client_modified_at=_clock(15, 23), server_modified_at=_clock(15, 23),
        rehydrate_needed=False,
    )
    device = SimpleNamespace(
        id=2, public_id="clara-public", kind="kobo",
        display_name="Kobo Clara", active=True,
    )

    row = reading_sources.device_source_rows(
        [device], [position], book_id=540, epub_path=epub,
    )[0]

    assert row["resume"] == {
        "percentage": 8.0,
        "exact": False,
        "chapter_href": "OEBPS/chapter001.xhtml",
        "chapter_progression": 0.56,
        "epub_sha256": hashlib.sha256(epub.read_bytes()).hexdigest(),
    }


def test_kobo_chapter_fallback_rejects_unmatched_or_unsafe_href(
        monkeypatch, tmp_path, patient_chapter_resume):
    from cps.services import reading_sources

    epub = tmp_path / "book.epub"
    with ZipFile(epub, "w") as archive:
        archive.writestr("OEBPS/chapter001.xhtml", "chapter")
    monkeypatch.setattr(reading_sources, "exact_resume", lambda *_args: None)
    base = dict(
        device_id=2, progress_percent=8.0, cfi=None,
        location_type="KoboSpan", location_value="kobo.44.10",
        content_source_progress_percent=56.0,
        client_modified_at=_clock(15, 23), server_modified_at=_clock(15, 23),
        rehydrate_needed=False,
    )
    device = SimpleNamespace(
        id=2, public_id="clara-public", kind="kobo",
        display_name="Kobo Clara", active=True,
    )
    def resume_for(source):
        position = SimpleNamespace(location_source=source, **base)
        return reading_sources.device_source_rows(
            [device], [position], book_id=540, epub_path=epub,
        )[0]["resume"]

    # The same archive and worker do admit the chapter that exists, so the
    # refusals below are decisions about the href, not an idle worker.
    assert resume_for("OEBPS/chapter001.xhtml")["chapter_href"] == "OEBPS/chapter001.xhtml"
    for source in ("OEBPS/missing.xhtml", "../OEBPS/chapter001.xhtml"):
        assert resume_for(source) == {"percentage": 8.0, "exact": False}


def test_storyteller_source_requires_exact_archive_hash(monkeypatch, tmp_path):
    from cps.services import storyteller_source

    epub = tmp_path / "book.epub"
    with ZipFile(epub, "w") as archive:
        archive.writestr(
            "OEBPS/chapter001.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml">'
            '<p id="chapter001-s352">Saved paragraph</p></html>',
        )
    digest = storyteller_source.sha256_file(epub)

    responses = {
        "/api/books": [{
            "id": 91, "title": "The Heat Will Kill You First: Life and Death",
            "authors": [{"name": "Jeff Goodell"}],
        }],
        "/api/v2/books/91/files?format=ebook": SimpleNamespace(
            status_code=206,
            headers={
                "X-Storyteller-Hash": digest,
                "Content-Range": "bytes 0-0/12",
            },
            content=b"x",
        ),
        "/api/books/91/positions": {
            "locator": {
                "href": "/OEBPS/chapter001.xhtml",
                "locations": {
                    "fragments": ["chapter001-s352"],
                    "progression": 0.7385,
                    "totalProgression": 0.118343,
                },
            },
            "timestamp": 1789078431330,
        },
    }

    class Client:
        def get_json(self, path):
            return responses[path]

        def get(self, path, **_kwargs):
            return responses[path]

    row = storyteller_source.read_source(
        Client(), title="The Heat Will Kill You First",
        authors=["Jeff Goodell"], epub_path=epub,
    )

    assert row["label"] == "Storyteller"
    assert row["progress_percent"] == pytest.approx(11.8343)
    assert row["edition"]["match"] == "sha256"
    assert row["resume"] == {
        "percentage": pytest.approx(11.8343),
        "href": "OEBPS/chapter001.xhtml#chapter001-s352",
        "epub_sha256": digest,
        "exact": True,
    }


def test_storyteller_synthetic_fragment_is_honest_approximate_resume(tmp_path):
    from cps.services import storyteller_source

    epub = tmp_path / "book.epub"
    with ZipFile(epub, "w") as archive:
        archive.writestr(
            "OEBPS/chapter001.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><p>Paragraph</p></html>',
        )
    digest = storyteller_source.sha256_file(epub)

    class Client:
        def get_json(self, path):
            if path == "/api/books":
                return [{"id": 9, "title": "Book", "authors": [{"name": "A"}]}]
            return {
                "locator": {
                    "href": "/OEBPS/chapter001.xhtml",
                    "locations": {
                        "fragments": ["chapter001-s352"],
                        "progression": 0.7385,
                        "totalProgression": 0.12,
                    },
                },
                "timestamp": 1789078431330,
            }

        def get(self, _path, **_kwargs):
            return SimpleNamespace(
                status_code=206, headers={"X-Storyteller-Hash": digest},
            )

    row = storyteller_source.read_source(
        Client(), title="Book", authors=["A"], epub_path=epub,
    )

    assert row["edition"]["match"] == "sha256"
    assert row["resume"] == {
        "percentage": 12.0,
        "exact": False,
        "chapter_href": "OEBPS/chapter001.xhtml",
        "chapter_progression": pytest.approx(0.7385),
        "epub_sha256": digest,
    }


def test_storyteller_hash_mismatch_keeps_honest_percentage_only(tmp_path):
    from cps.services import storyteller_source

    epub = tmp_path / "book.epub"
    epub.write_bytes(b"edition-a")

    class Client:
        def get_json(self, path):
            if path == "/api/books":
                return [{"id": 7, "title": "Book", "authors": [{"name": "A"}]}]
            return {
                "locator": {
                    "href": "/chapter.xhtml",
                    "locations": {"totalProgression": 0.25},
                },
                "timestamp": 1789078431330,
            }

        def get(self, _path, **_kwargs):
            return SimpleNamespace(
                status_code=206,
                headers={"X-Storyteller-Hash": "f" * 64,
                         "Content-Range": "bytes 0-0/9"},
                content=b"x",
            )

    row = storyteller_source.read_source(
        Client(), title="Book", authors=["A"], epub_path=epub,
    )

    assert row["edition"]["match"] == "different"
    assert row["resume"] == {"percentage": 25.0, "exact": False}


def test_storyteller_connection_is_user_scoped_and_disables_redirects(
        monkeypatch, tmp_path):
    from cps.services import storyteller_source

    config = tmp_path / "connections.json"
    config.write_text(json.dumps({
        "1": {"url": "http://storyteller-one.test", "token": "one"},
        "2": {"url": "http://storyteller-two.test", "token": "two"},
    }))
    monkeypatch.setenv("CWNG_STORYTELLER_CONNECTIONS_FILE", str(config))
    monkeypatch.delenv("CWNG_STORYTELLER_CONNECTIONS_JSON", raising=False)

    client = storyteller_source.configured_client(1)
    assert client is not None
    assert client.base_url == "http://storyteller-one.test"
    assert storyteller_source.configured_client(3) is None

    response = MagicMock()
    session = MagicMock()
    session.get.return_value = response
    client = storyteller_source.StorytellerClient(
        "http://storyteller-one.test", "one", session=session,
    )
    assert client.get("/api/books") is response
    session.get.assert_called_once_with(
        "http://storyteller-one.test/api/books",
        headers={"Authorization": "Bearer one"},
        timeout=storyteller_source.REQUEST_TIMEOUT,
        allow_redirects=False,
    )
