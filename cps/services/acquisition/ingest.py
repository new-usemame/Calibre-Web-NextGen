# SPDX-License-Identifier: GPL-3.0-or-later
"""Trusted acquisition acknowledgment; runtime supplies an existing repository.

No schema, key, process or network access is created here. An untrusted sidecar
carries only a capability. Account and import intent are read from app.db.
"""
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text

from .storage import ImportOutcome
from .contracts import DIRECT_FORMATS

RESULT_TABLE = "cwng_acquisition_ingest_result"
NAME_PREFIX = "cwng-acquisition-"


class IngestIntentError(ValueError):
    """Safe error; caller preserves source and capability for reconciliation."""


@dataclass(frozen=True)
class Intent:
    job_id: str
    staging_key: str
    source_sha256: str
    publication_token: str = field(repr=False)
    source_path: Path = field(repr=False)


def source_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_intent(repo, source_path, ingest_dir, manifest):
    try:
        if not isinstance(manifest, dict) or manifest.get("action") != "acquisition_import":
            raise IngestIntentError("Invalid acquisition manifest")
        key = manifest.get("staging_key")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
            raise IngestIntentError("Invalid acquisition path identity")
        path = Path(source_path)
        root = Path(ingest_dir).resolve(strict=True)
        if (path.is_symlink() or not path.is_file() or path.parent.resolve(strict=True) != root
                or path.name not in tuple(NAME_PREFIX + key + '.' + extension
                    for _label, extension in DIRECT_FORMATS.values())):
            raise IngestIntentError("Acquisition source path does not match intent")
        job, permit = repo.publication_intent(manifest.get("job_id"), manifest.get("publication_token"), key)
        if source_digest(path) != permit.source_sha256:
            raise IngestIntentError("Acquisition source does not match intent")
        return Intent(job.id, key, permit.source_sha256, permit.token, path.resolve(strict=True))
    except Exception:
        raise IngestIntentError("Acquisition intent could not be verified") from None


def read_result(metadata_db, source_sha256, library_dir):
    """Read only a helper-committed acquisition result, never infer latest IDs."""
    try:
        uri = Path(metadata_db).resolve(strict=True).as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=30) as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (RESULT_TABLE,)).fetchone() is None:
                return None
            rows = connection.execute(
                "SELECT result_json FROM cwng_acquisition_ingest_result WHERE source_sha256=?", (source_sha256,)
            ).fetchall()
            if not rows:
                return None
            values = {row[0] for row in rows}
            if len(values) != 1:
                raise IngestIntentError("Conflicting acquisition provenance")
            result = json.loads(values.pop())
            outcome = ImportOutcome(result["source_sha256"], result["imported_sha256"],
                                    tuple(result["book_ids"]), result["disposition"])
            if outcome.source_sha256 != source_sha256:
                raise IngestIntentError("Invalid acquisition provenance")
            if (outcome.disposition == "existing_retained"
                    and result.get("artifact_identity_version") not in (1, 2)):
                # Do not let early processor recovery bypass the helper's
                # reinspection of legacy metadata-only retention results.
                return None
            library_root = Path(library_dir).resolve(strict=True)
            book_format = result.get("format")
            if not isinstance(book_format, str) or not re.fullmatch(r"[A-Za-z0-9]{1,16}", book_format):
                return None
            for book_id in outcome.book_ids:
                row = connection.execute(
                    "SELECT books.path, data.name FROM books JOIN data ON data.book=books.id "
                    "WHERE books.id=? AND UPPER(data.format)=?", (book_id, book_format.upper())
                ).fetchone()
                if row is None:
                    return None
                try:
                    stored = (library_root / row[0] / (row[1] + "." + book_format.lower())).resolve(strict=True)
                    if not stored.is_relative_to(library_root) or not stored.is_file() or source_digest(stored) != outcome.imported_sha256:
                        return None
                except OSError:
                    return None
            return outcome
    except Exception:
        raise IngestIntentError("Acquisition library result could not be verified") from None


def finalize(repo, intent, result, metadata_db, library_dir):
    """Receipt and current personal membership commit in the same app.db tx.

    Missing/deleted accounts receive no membership. Current account library mode
    is authoritative; a sidecar cannot create a user or set another user's ID.
    The caller cleans source/manifest only after this function returns.
    """
    try:
        outcome = ImportOutcome(result["source_sha256"], result["imported_sha256"],
                                tuple(result["book_ids"]), result["disposition"])
        if outcome.source_sha256 != intent.source_sha256:
            raise IngestIntentError("Acquisition result source mismatch")
        def membership(connection, job, committed):
            if read_result(metadata_db, intent.source_sha256, library_dir) != committed:
                raise IngestIntentError("Acquisition result lacks committed provenance")
            if job.add_to_my_library:
                for book_id in committed.book_ids:
                    connection.execute(text(
                        "INSERT INTO user_library_book (user_id, book_id, added_at) "
                        "SELECT id, :book_id, datetime('now') FROM user "
                        "WHERE id=:owner_id AND has_own_library=1 "
                        "ON CONFLICT(user_id, book_id) DO NOTHING"
                    ), {"book_id": book_id, "owner_id": job.owner_id})
        return repo.finalize_import(intent.job_id, intent.publication_token, outcome,
            staging_key=intent.staging_key, finalize_membership=membership)
    except Exception:
        raise IngestIntentError("Acquisition receipt could not be committed") from None
