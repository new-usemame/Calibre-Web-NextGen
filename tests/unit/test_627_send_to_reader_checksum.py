# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #627 — "Send to Reader" never registered a
KOReader sync fingerprint, so progress from an emailed book went nowhere.

The reporter's whole workflow is emailing books to a PocketBook. KOReader
identifies a book by hashing the file it holds and asks the server about that
hash; the server had never seen the emailed file, so the position was stored
orphaned and never surfaced in the library. Their logs showed it plainly:
`No book found for checksum: 2069bab8…`. Books pulled over OPDS synced fine,
because the download path *does* register the exact file it serves.

Two things had to be true and neither was:

1. The CONTENT digest must cover the bytes actually attached. With metadata
   embedding on, the email carries a freshly-staged export whose bytes differ
   from the library file — hashing the library file would still not match what
   the device holds.
2. The FILENAME digest must cover the name the RECIPIENT sees, i.e. the MIME
   attachment name — not the basename of our temp export, which is a name no
   device will ever know about. This matters because the reporter was told to
   put the kosync plugin in "filename" matching mode.

Seam: the real hashing runs; only `store_checksum` (the DB write) is captured.
So these assert the digests that would actually be persisted, computed from a
real file on disk — not that "some call happened".

RED on main: test_content_digest_covers_the_emailed_bytes,
test_filename_digest_uses_the_attachment_name_not_the_temp_export,
test_raw_library_file_is_registered_when_metadata_embedding_is_off.
Green already (guards against regression): the returned-bytes tests, the
sync-disabled test, and the never-breaks-the-email test.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


LIBRARY_BYTES = b"PK\x03\x04library-copy-of-the-book" + b"\x00" * 2048
EXPORT_BYTES = b"PK\x03\x04metadata-embedded-export" + b"\x00" * 4096
ATTACHMENT = "Some Author - Some Title.epub"


@pytest.fixture
def mail_env(tmp_path, monkeypatch):
    """A book in a library, plus a staged export with different bytes.

    Returns the pieces a test needs plus `stored`, the list of
    (book_id, book_format, checksum, version) tuples that would have been
    written to book_format_checksums.
    """
    from cps.tasks import mail as mail_mod
    from cps.progress_syncing.checksums import manager as manager_mod

    library_root = tmp_path / "library"
    book_dir = library_root / "Some Author" / "Some Title (1)"
    book_dir.mkdir(parents=True)
    (book_dir / ATTACHMENT).write_bytes(LIBRARY_BYTES)

    export_dir = tmp_path / "staged"
    export_dir.mkdir()
    export_stem = "a3f1c0de-0000-4000-8000-000000000000"
    (export_dir / f"{export_stem}.epub").write_bytes(EXPORT_BYTES)

    class FakeConfig:
        config_use_google_drive = False
        config_binariesdir = "/usr/bin"
        config_embed_metadata = True

        def get_book_path(self):
            return str(library_root)

    monkeypatch.setattr(mail_mod, "config", FakeConfig())
    monkeypatch.setattr(
        mail_mod, "do_calibre_export",
        lambda book_id, extension: (str(export_dir), export_stem))

    monkeypatch.setattr(
        "cps.progress_syncing.settings.is_koreader_sync_enabled", lambda: True)

    stored = []
    monkeypatch.setattr(
        manager_mod, "store_checksum",
        lambda book_id, book_format, checksum, version=None, db_connection=None: (
            stored.append((book_id, book_format, checksum, version)) or True))

    task = mail_mod.TaskEmail(
        subject="Send to eReader", filepath="Some Author/Some Title (1)",
        attachment=ATTACHMENT,
        settings={"mail_from": "lib@example.com", "mail_server_type": 0},
        recipient="reader@example.com", task_message="", text="", id=42)

    return {
        "task": task, "stored": stored, "config": FakeConfig,
        "book_path": "Some Author/Some Title (1)",
        "export_file": str(export_dir / f"{export_stem}.epub"),
        "library_file": str(book_dir / ATTACHMENT),
        "export_stem": export_stem,
        "mail_mod": mail_mod,
    }


def _digests(stored):
    return {row[2] for row in stored}


def test_the_email_carries_the_staged_export_not_the_library_file(mail_env):
    """Baseline for everything else: the bytes we send are the export's.

    If this ever stops holding, the fingerprint tests below are testing the
    wrong file and would pass while sync stayed broken.
    """
    data = mail_env["task"]._get_attachment(mail_env["book_path"], ATTACHMENT)
    assert data == EXPORT_BYTES
    assert data != LIBRARY_BYTES


def test_content_digest_covers_the_emailed_bytes(mail_env):
    """RED on main — nothing was registered at all.

    The expected digest is computed independently, from the export file, with
    the same function KOReader's counterpart uses.
    """
    from cps.progress_syncing.checksums.koreader import calculate_koreader_partial_md5

    # Computed BEFORE the call: _get_attachment deletes the staged export once
    # it has read the bytes, so afterwards there is nothing left to hash. That
    # deletion is also exactly why the fingerprint has to be registered inside
    # the send path rather than reconstructed later.
    expected = calculate_koreader_partial_md5(mail_env["export_file"])
    library_digest = calculate_koreader_partial_md5(mail_env["library_file"])
    assert expected, "fixture produced no hashable export file"

    mail_env["task"]._get_attachment(mail_env["book_path"], ATTACHMENT)

    assert expected in _digests(mail_env["stored"]), (
        "the device hashes the file it received; that exact digest must be "
        "registered or its progress push matches nothing (#627)")

    # And specifically NOT the library file's digest, which is what a naive
    # fix (hash whatever is in the library) would have stored.
    assert library_digest != expected, "fixture bytes must differ"
    assert library_digest not in _digests(mail_env["stored"])


def test_filename_digest_uses_the_attachment_name_not_the_temp_export(mail_env):
    """RED on main. Guards the subtlety that makes filename-matching work.

    The recipient's device sees the MIME attachment name. Our staged export is
    a uuid-ish temp name that exists for milliseconds and that no device will
    ever hold, so registering it would be both useless and pollution.
    """
    from cps.progress_syncing.checksums.koreader import calculate_koreader_filename_md5

    mail_env["task"]._get_attachment(mail_env["book_path"], ATTACHMENT)

    digests = _digests(mail_env["stored"])
    assert calculate_koreader_filename_md5(ATTACHMENT) in digests

    temp_basename = os.path.basename(mail_env["export_file"])
    assert calculate_koreader_filename_md5(temp_basename) not in digests, (
        "registered the temp export's name — no device will ever hold that "
        "file, and it maps a meaningless name onto this book")


def test_colon_in_the_attachment_name_registers_the_name_a_device_can_store(mail_env):
    """#2755 keeps ':' in the e-mailed attachment name so the recipient sees the
    real title. A reader's storage (FAT, Android shared storage) cannot hold
    ':', so the file KOReader hashes by name is not called that. The filename
    digest keeps the colon-free name the send path registered before #2755.
    """
    from cps.progress_syncing.checksums.koreader import calculate_koreader_filename_md5

    mail_env["task"].attachment_name = "Dune: Messiah.epub"
    mail_env["task"]._get_attachment(mail_env["book_path"], ATTACHMENT)

    digests = _digests(mail_env["stored"])
    assert calculate_koreader_filename_md5("Dune_ Messiah.epub") in digests
    assert calculate_koreader_filename_md5("Dune: Messiah.epub") not in digests


def test_raw_library_file_is_registered_when_metadata_embedding_is_off(mail_env, monkeypatch):
    """RED on main. The common configuration must work too.

    With embedding off the library file itself is emailed, so that is the file
    to fingerprint — and the attachment name still governs filename matching.
    """
    from cps.progress_syncing.checksums.koreader import (
        calculate_koreader_filename_md5, calculate_koreader_partial_md5)

    monkeypatch.setattr(mail_env["mail_mod"].config, "config_embed_metadata", False)

    data = mail_env["task"]._get_attachment(mail_env["book_path"], ATTACHMENT)
    assert data == LIBRARY_BYTES

    digests = _digests(mail_env["stored"])
    assert calculate_koreader_partial_md5(mail_env["library_file"]) in digests
    assert calculate_koreader_filename_md5(ATTACHMENT) in digests


def test_nothing_is_registered_when_koreader_sync_is_disabled(mail_env, monkeypatch):
    """The checksum table does not exist unless sync was enabled; writing to it
    raises `no such table` on every send (upstream CWA #1183)."""
    monkeypatch.setattr(
        "cps.progress_syncing.settings.is_koreader_sync_enabled", lambda: False)

    data = mail_env["task"]._get_attachment(mail_env["book_path"], ATTACHMENT)

    assert data == EXPORT_BYTES
    assert mail_env["stored"] == []


def test_a_failing_checksum_never_costs_the_user_the_email(mail_env, monkeypatch):
    """Losing the book is worse than losing the sync hint, so registration is
    strictly best-effort."""
    def boom(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(
        "cps.progress_syncing.checksums.manager.store_checksum", boom)

    data = mail_env["task"]._get_attachment(mail_env["book_path"], ATTACHMENT)
    assert data == EXPORT_BYTES


def test_existing_callers_keep_the_basename_behaviour(tmp_path, monkeypatch):
    """`filename_for_matching` is additive: every call site that does not pass
    it must behave exactly as before, or the download path's fingerprints move
    and previously-synced devices stop matching."""
    from cps.progress_syncing.checksums import manager as manager_mod
    from cps.progress_syncing.checksums.koreader import calculate_koreader_filename_md5

    book = tmp_path / "Title - Author.epub"
    book.write_bytes(EXPORT_BYTES)

    stored = []
    monkeypatch.setattr(
        manager_mod, "store_checksum",
        lambda book_id, book_format, checksum, version=None, db_connection=None: (
            stored.append(checksum) or True))

    manager_mod.calculate_and_store_checksum(
        book_id=7, book_format="EPUB", file_path=str(book))

    assert calculate_koreader_filename_md5("Title - Author.epub") in stored
