# SPDX-License-Identifier: GPL-3.0-or-later
"""Unregistered acquisition persistence with explicit transaction ownership.

Call define_tables on the application's metadata during a future migration.
No engine, schema, user table, task or network request is created on import.
The application must authenticate callers, check admin/role permissions and
validate configured URLs. This repository enforces per-job/offer ownership.
"""
import hashlib
import json
import math
import re
import secrets as random_secrets
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import (Boolean, Column, Float, ForeignKey, Integer, LargeBinary,
                        String, Table, Text, UniqueConstraint, and_, or_, select, func)
from sqlalchemy.exc import IntegrityError

from .secrets import SealedValue
from .bundle_storage import BundleChoicesMixin


class StorageError(ValueError):
    pass


class NotFound(StorageError):
    """Unavailable and another user's records intentionally look identical."""


class Conflict(StorageError):
    pass


class ConnectionChanged(Conflict):
    code = 'connection_changed'


class SelectionLimit(Conflict):
    """Safe admission result: let existing catalog selections expire first."""
    code = "selections_full"

    def __init__(self):
        super().__init__("Too many active catalog selections; wait for older selections to expire and retry")


# Enough room for a full bounded catalog page plus navigation/search selections.
# Durable jobs retain their offers after expiry; only unexpired selections count.
MAX_ACTIVE_OFFERS_PER_OWNER = 10000
EXPIRED_OFFER_CLEANUP_BATCH = 100


WORK_STATES = ("queued", "resolving", "downloading", "staged", "publishing", "importing")
# `importing` is owned by the ingest service, not by this worker: a claim on it
# only reconciles what the processor did, and never repeats an external effect.
RECONCILE_STATES = ("importing",)
TRANSITIONS = {
    "queued": {"resolving", "failed", "cancelled"},
    "resolving": {"downloading", "failed", "cancelled"},
    "downloading": {"staged", "failed", "cancelled"},
    "staged": {"failed", "cancelled"},  # publication requires durable capability issuance
    "publishing": {"importing", "failed"},
    "importing": {"failed"},  # success only through receipt finalization
}


@dataclass(frozen=True)
class Tables:
    connections: Table = field(repr=False)
    offers: Table = field(repr=False)
    jobs: Table = field(repr=False)
    receipts: Table = field(repr=False)
    manifests: Table = field(repr=False)


def _sealed_columns(prefix):
    return (Column(prefix + "_ciphertext", LargeBinary, nullable=False),
            Column(prefix + "_nonce", LargeBinary, nullable=False),
            Column(prefix + "_version", Integer, nullable=False))


def define_tables(metadata):
    """Register additive tables only; caller explicitly runs its migration.

    User IDs reference the real application's users logically for now. Runtime
    integration must bind account validation/deletion to ub; tests do not
    create a parallel user model to pretend that integration is complete.
    """
    names = ("acquisition_connection", "acquisition_offer", "acquisition_job", "acquisition_import_receipt", "acquisition_bundle_manifest")
    existing = [metadata.tables.get(name) for name in names]
    if any(table is not None for table in existing):
        if not all(table is not None for table in existing):
            raise StorageError("Incomplete acquisition metadata registration")
        return Tables(*existing)
    connections = Table(names[0], metadata,
        Column("id", String(36), primary_key=True), Column("label", String(200), nullable=False),
        Column("adapter", String(64), nullable=False), Column("enabled", Boolean, nullable=False),
        Column("revision", Integer, nullable=False), Column("deleted", Boolean, nullable=False, default=False), *_sealed_columns("config"),
        Column("created_at", Float, nullable=False))
    offers = Table(names[1], metadata,
        Column("id", String(64), primary_key=True),
        Column("connection_id", String(36), ForeignKey(names[0] + ".id"), nullable=False),
        Column("connection_revision", Integer, nullable=False), Column("owner_id", Integer, nullable=False, index=True),
        *_sealed_columns("payload"), Column("expires_at", Float, nullable=False),
        Column("created_at", Float, nullable=False))
    jobs = Table(names[2], metadata,
        Column("id", String(36), primary_key=True), Column("owner_id", Integer, nullable=False, index=True),
        Column("offer_id", String(64), ForeignKey(names[1] + ".id"), nullable=False),
        Column("connection_id", String(36), ForeignKey(names[0] + ".id"), nullable=False),
        Column("idempotency_key", String(128), nullable=False),
        Column("state", String(32), nullable=False, index=True), Column("approved_by", Integer),
        Column("title", String(512)),
        Column("client_id", String(36)), Column("client_revision", Integer),
        Column("external_id", String(128)), Column("submission_started", Float),
        Column("submission_key", String(32)), Column("submission_invalid", Boolean),
        Column("release_key", String(64)),
        Column("download_release_key", String(64)),
        Column("bundle_parent_id", String(36)),
        Column("selected_artifact_id", String(64)),
        Column("add_to_my_library", Boolean, nullable=False), Column("cancel_requested", Boolean, nullable=False),
        Column("lease_token", String(64)), Column("lease_expires", Float),
        Column("next_attempt_at", Float, nullable=False, index=True), Column("claim_count", Integer, nullable=False),
        Column("source_sha256", String(64)), Column("staging_key", String(128)),
        Column("publication_proof_hash", String(64)),
        # When the ingest service took ownership. Bounds `importing` so a
        # processor that dies without any terminal signal cannot strand a job.
        Column("importing_since", Float),
        Column("error_code", String(64)), Column("created_at", Float, nullable=False),
        Column("updated_at", Float, nullable=False),
        UniqueConstraint("owner_id", "idempotency_key", name="uq_acquisition_job_owner_intent"),
        UniqueConstraint("owner_id", "release_key", name="uq_acquisition_job_owner_release"))
    receipts = Table(names[3], metadata,
        Column("job_id", String(36), ForeignKey(names[2] + ".id"), primary_key=True),
        Column("source_sha256", String(64), nullable=False), Column("imported_sha256", String(64), nullable=False),
        Column("book_ids_json", Text, nullable=False), Column("disposition", String(32), nullable=False),
        Column("proof_hash", String(64), nullable=False), Column("created_at", Float, nullable=False))
    manifests = Table(names[4], metadata,
        Column("job_id", String(36), ForeignKey(names[2] + ".id"), primary_key=True),
        Column("generation", String(64), nullable=False), *_sealed_columns("payload"),
        Column("created_at", Float, nullable=False))
    return Tables(connections, offers, jobs, receipts, manifests)


@dataclass(frozen=True)
class ConnectionStatus:
    id: str
    label: str
    adapter: str
    enabled: bool
    revision: int


@dataclass(frozen=True)
class Job:
    id: str
    owner_id: int
    offer_id: str = field(repr=False)
    connection_id: str
    state: str
    add_to_my_library: bool
    cancel_requested: bool
    error_code: str | None
    claim_count: int
    title: str | None = None
    bundle_parent_id: str | None = None
    bundle_selectable: bool = False


@dataclass(frozen=True)
class Claim:
    job: Job
    token: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True)
class PublicationPermit:
    job_id: str
    source_sha256: str
    staging_key: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class Material:
    config: dict = field(repr=False)
    offer: dict = field(repr=False)
    revision: int | None = None


@dataclass(frozen=True)
class ImportOutcome:
    source_sha256: str
    imported_sha256: str
    book_ids: tuple[int, ...]
    disposition: str = "imported"

    def __post_init__(self):
        _digest(self.source_sha256)
        _digest(self.imported_sha256)
        if (not isinstance(self.book_ids, tuple) or not self.book_ids
                or len(self.book_ids) > 1000
                or any(type(value) is not int or value < 1 for value in self.book_ids)
                or len(set(self.book_ids)) != len(self.book_ids)):
            raise StorageError("Import outcome needs exact positive unique book IDs")
        if self.disposition not in ("imported", "already_imported", "existing_retained"):
            raise StorageError("Unsupported import disposition")


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise StorageError("Invalid content digest")
    return value


def _user(value):
    if type(value) is not int or value < 1:
        raise StorageError("Invalid account ID")
    return value


def _text(value, maximum=128):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise StorageError("Invalid record value")
    return value


def _sealed_values(prefix, sealed):
    return {prefix + "_ciphertext": sealed.ciphertext, prefix + "_nonce": sealed.nonce,
            prefix + "_version": sealed.version}


def _sealed(row, prefix):
    return SealedValue(row[prefix + "_ciphertext"], row[prefix + "_nonce"], row[prefix + "_version"])


def _job(row):
    return Job(*(row[key] for key in ("id", "owner_id", "offer_id", "connection_id", "state",
                                     "add_to_my_library", "cancel_requested", "error_code", "claim_count", "title", "bundle_parent_id")),
               bundle_selectable=bool(row["bundle_parent_id"] == row["id"] and (
                   row["selected_artifact_id"] is not None or not row["cancel_requested"] and row["state"] not in ('cancelled','rejected'))))


def _serialize_write(conn):
    # sqlite3's legacy SELECT mode does not open a transaction. Acquire the
    # write reservation before reading admission/config state so another writer
    # cannot invalidate the decision between that read and its INSERT/UPDATE.
    if conn.dialect.name == 'sqlite' and not conn.connection.driver_connection.in_transaction:
        conn.exec_driver_sql('BEGIN IMMEDIATE')


class Repository(BundleChoicesMixin):
    def __init__(self, engine, tables, secret_box, *, clock=time.time):
        self.engine, self.tables, self.box, self.clock = engine, tables, secret_box, clock

    def _now(self):
        value = self.clock()
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise StorageError("Invalid acquisition clock")
        return float(value)

    def _encode(self, payload, *, scope, identity, field_name):
        if not isinstance(payload, dict):
            raise StorageError("Expected private payload object")
        try:
            text = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise StorageError("Invalid private payload") from None
        return self.box.seal(text, scope=scope, identity=identity, field_name=field_name)

    def create_connection(self, label, adapter, config, *, enabled=False):
        """Administrator-only integration seam; config includes endpoint/auth.

        Caller validates its adapter schema and transport security policy.
        No provider names or singleton deployment credentials are assumed.
        """
        identifier = str(uuid.uuid4())
        values = dict(id=identifier, label=_text(label, 200), adapter=_text(adapter, 64),
                      enabled=bool(enabled), revision=1, created_at=self._now())
        values.update(_sealed_values("config", self._encode(config, scope="deployment",
                            identity=identifier, field_name="connection-config:1")))
        with self.engine.begin() as conn:
            _serialize_write(conn)
            conn.execute(self.tables.connections.insert().values(**values))
        return ConnectionStatus(identifier, label, adapter, bool(enabled), 1)

    def list_connections(self, *, include_disabled=False):
        """Safe connection labels; caller authorizes deployment access."""
        table = self.tables.connections
        statement = select(table).where(table.c.deleted.is_(False)).order_by(table.c.label, table.c.id)
        if not include_disabled:
            statement = statement.where(table.c.enabled.is_(True))
        with self.engine.connect() as conn:
            return tuple(ConnectionStatus(*(row[key] for key in
                ("id", "label", "adapter", "enabled", "revision")))
                for row in conn.execute(statement).mappings())

    def connection_config(self, connection_id, *, include_disabled=False):
        """Private service-only configuration, never an API representation."""
        table = self.tables.connections
        statement = select(table).where(table.c.id == connection_id, table.c.deleted.is_(False))
        if not include_disabled:
            statement = statement.where(table.c.enabled.is_(True))
        with self.engine.connect() as conn:
            row = conn.execute(statement).mappings().first()
            if row is None:
                raise NotFound("Connection is unavailable")
            config = self.box.open(_sealed(row, "config"), scope="deployment",
                identity=row["id"], field_name=f"connection-config:{row['revision']}")
            return Material(json.loads(config), {}, row["revision"])

    def offer_payload(self, owner_id, offer_id, connection_id):
        """Owner-bound, expiring navigation/search/acquisition selection."""
        with self.engine.connect() as conn:
            offer = conn.execute(select(self.tables.offers).where(
                self.tables.offers.c.id == offer_id,
                self.tables.offers.c.owner_id == _user(owner_id),
                self.tables.offers.c.connection_id == connection_id,
                self.tables.offers.c.expires_at > self._now())).mappings().first()
            connection = conn.execute(select(self.tables.connections).where(
                self.tables.connections.c.id == connection_id,
                self.tables.connections.c.enabled.is_(True))).mappings().first()
            if offer is None or connection is None or offer["connection_revision"] != connection["revision"]:
                raise NotFound("Selection expired or is unavailable")
            scope = f"user:{owner_id}:connection:{connection_id}:revision:{offer['connection_revision']}"
            payload = self.box.open(_sealed(offer, "payload"), scope=scope,
                identity=offer_id, field_name="offer")
            return Material({}, json.loads(payload))

    def set_connection_enabled(self, connection_id, enabled, *, expected_revision=None):
        """Caller verifies administrator; disabling prevents new claims/submissions."""
        with self.engine.begin() as conn:
            _serialize_write(conn)
            if expected_revision is not None:
                revision = conn.execute(select(self.tables.connections.c.revision).where(
                    self.tables.connections.c.id == connection_id, self.tables.connections.c.deleted.is_(False))).scalar()
                if revision != expected_revision:
                    raise ConnectionChanged("Connection changed while saving")
            result = conn.execute(self.tables.connections.update().where(
                self.tables.connections.c.id == connection_id,
                self.tables.connections.c.deleted.is_(False)).values(enabled=bool(enabled)))
            if result.rowcount != 1:
                raise NotFound("Connection is unavailable")

    def _connection_idle(self, conn, connection_id):
        jobs = self.tables.jobs
        if conn.execute(select(jobs.c.id).where(
                or_(jobs.c.connection_id == connection_id, jobs.c.client_id == connection_id),
                jobs.c.state.not_in(('imported', 'failed', 'cancelled', 'rejected'))).limit(1)).first():
            raise Conflict("Finish or cancel outstanding requests before editing this connection")

    def update_connection(self, connection_id, *, label, config, expected_revision=None):
        """Rotate sealed config under CAS; old selections expire, never reroute work."""
        table = self.tables.connections
        with self.engine.begin() as conn:
            _serialize_write(conn)
            self._connection_idle(conn, connection_id)
            row = conn.execute(select(table).where(table.c.id == connection_id,
                table.c.deleted.is_(False))).mappings().first()
            if row is None:
                raise NotFound("Connection is unavailable")
            if expected_revision is not None and row['revision'] != expected_revision:
                raise ConnectionChanged("Connection changed; reload its settings before saving")
            revision = row['revision'] + 1
            values = dict(label=_text(label, 200), revision=revision, enabled=False)
            values.update(_sealed_values('config', self._encode(config, scope='deployment',
                identity=connection_id, field_name=f'connection-config:{revision}')))
            result = conn.execute(table.update().where(table.c.id == connection_id,
                table.c.revision == row['revision']).values(**values))
            if result.rowcount != 1:
                raise ConnectionChanged("Connection changed; refresh its settings")

    def delete_connection(self, connection_id, *, expected_revision=None):
        """Erase credentials; keep only the FK tombstone needed for job history."""
        table = self.tables.connections
        with self.engine.begin() as conn:
            _serialize_write(conn)
            self._connection_idle(conn, connection_id)
            row = conn.execute(select(table).where(table.c.id == connection_id,
                table.c.deleted.is_(False))).mappings().first()
            if row is None:
                raise NotFound("Connection is unavailable")
            if expected_revision is not None and row['revision'] != expected_revision:
                raise ConnectionChanged("Connection changed; reload its settings before saving")
            revision = row['revision'] + 1
            values = dict(deleted=True, enabled=False, revision=revision)
            values.update(_sealed_values('config', self._encode({}, scope='deployment',
                identity=connection_id, field_name=f'connection-config:{revision}')))
            changed = conn.execute(table.update().where(table.c.id == connection_id,
                table.c.revision == row['revision']).values(**values))
            if changed.rowcount != 1:
                raise ConnectionChanged("Connection changed; refresh its settings")

    def _cleanup_expired_offers(self, conn, owner_id, now, limit):
        offers, jobs = self.tables.offers, self.tables.jobs
        referenced = select(jobs.c.id).where(jobs.c.offer_id == offers.c.id).exists()
        candidates = select(offers.c.id).where(
            offers.c.owner_id == owner_id, offers.c.expires_at <= now,
            ~referenced).order_by(offers.c.expires_at, offers.c.id).limit(limit)
        return conn.execute(offers.delete().where(offers.c.id.in_(candidates))).rowcount

    def cleanup_expired_offers(self, owner_id, *, limit=EXPIRED_OFFER_CLEANUP_BATCH):
        """Bounded owner-only GC; every durable job keeps its private offer.

        This is also safe for completed/failed/cancelled jobs: expiry must never
        destroy their replay identity, worker material, or receipt provenance.
        """
        owner_id = _user(owner_id)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise StorageError("Invalid selection cleanup limit")
        with self.engine.begin() as conn:
            _serialize_write(conn)
            return self._cleanup_expired_offers(conn, owner_id, self._now(), limit)

    def create_offer(self, owner_id, connection_id, payload, *, lifetime=900, expected_revision=None):
        owner_id = _user(owner_id)
        now = self._now()
        if type(lifetime) not in (int, float) or not math.isfinite(lifetime) or not 0 < lifetime <= 86400:
            raise StorageError("Invalid offer lifetime")
        identifier = random_secrets.token_urlsafe(32)
        with self.engine.begin() as conn:
            _serialize_write(conn)
            # First statement is a write, even when there are no expired rows.
            # SQLite therefore serializes count + insertion across connections;
            # a SELECT-first limit check could admit concurrent excess offers.
            self._cleanup_expired_offers(conn, owner_id, now, EXPIRED_OFFER_CLEANUP_BATCH)
            active = conn.execute(select(func.count()).select_from(self.tables.offers).where(
                self.tables.offers.c.owner_id == owner_id,
                self.tables.offers.c.expires_at > now)).scalar_one()
            if active >= MAX_ACTIVE_OFFERS_PER_OWNER:
                raise SelectionLimit()
            connection = conn.execute(select(self.tables.connections).where(
                self.tables.connections.c.id == connection_id,
                self.tables.connections.c.enabled.is_(True))).mappings().first()
            if connection is None or expected_revision is not None and connection["revision"] != expected_revision:
                raise NotFound("Connection is unavailable")
            scope = f"user:{owner_id}:connection:{connection_id}:revision:{connection['revision']}"
            values = dict(id=identifier, owner_id=owner_id, connection_id=connection_id,
                          connection_revision=connection["revision"], created_at=now, expires_at=now + lifetime)
            values.update(_sealed_values("payload", self._encode(
                payload, scope=scope, identity=identifier, field_name="offer")))
            conn.execute(self.tables.offers.insert().values(**values))
        return identifier

    def create_job(self, owner_id, offer_id, idempotency_key, *, requires_approval=True, add_to_my_library=True, connection_id=None, validate_offer=None):
        """Commit local intent before a worker may perform an external effect.

        requires_approval must be derived from authenticated permissions by the
        application. It defaults to the safe request-only behavior.
        """
        owner_id, now = _user(owner_id), self._now()
        key = _text(idempotency_key)
        table = self.tables.jobs
        release_key = None

        def previous(conn):
            row = conn.execute(select(table).where(table.c.owner_id == owner_id,
                                  table.c.idempotency_key == key)).mappings().first()
            if row is not None:
                if (row["offer_id"] != offer_id or row["add_to_my_library"] != bool(add_to_my_library)
                        or connection_id is not None and row["connection_id"] != connection_id):
                    raise Conflict("Idempotency key belongs to a different request")
                return _job(row)
            if release_key is not None:
                row = conn.execute(select(table).where(table.c.owner_id == owner_id,
                    table.c.release_key == release_key)).mappings().first()
                if row is not None:
                    if row['add_to_my_library'] != bool(add_to_my_library):
                        raise Conflict("This release already has a request with different library options")
                    return _job(row)
            return None

        try:
            with self.engine.begin() as conn:
                _serialize_write(conn)
                prior = previous(conn)
                if prior is not None:
                    return prior
                offer = conn.execute(select(self.tables.offers).join(self.tables.connections).where(
                    self.tables.offers.c.id == offer_id, self.tables.offers.c.owner_id == owner_id,
                    self.tables.offers.c.expires_at > now, self.tables.connections.c.enabled.is_(True),
                    self.tables.offers.c.connection_revision == self.tables.connections.c.revision)).mappings().first()
                if offer is None or connection_id is not None and offer["connection_id"] != connection_id:
                    raise NotFound("Offer is unavailable")
                scope = f"user:{owner_id}:connection:{offer['connection_id']}:revision:{offer['connection_revision']}"
                payload = json.loads(self.box.open(_sealed(offer, "payload"), scope=scope,
                    identity=offer_id, field_name="offer"))
                if validate_offer is not None:
                    # Pure validation only; do not contact sources in a transaction.
                    validate_offer(payload)
                if payload.get('transport') in ('nzb', 'torrent'):
                    release_key = _text(payload.get('release_key'), 64)
                    prior = previous(conn)
                    if prior is not None:
                        return prior
                    client = conn.execute(select(self.tables.connections).where(
                        self.tables.connections.c.id == payload.get('client_id'),
                        self.tables.connections.c.adapter.in_(('sabnzbd', 'nzbget') if payload.get('transport') == 'nzb' else ('qbittorrent', 'transmission')),
                        self.tables.connections.c.enabled.is_(True),
                        self.tables.connections.c.revision == payload.get('client_revision'))).mappings().first()
                    if client is None:
                        raise NotFound("Download client is unavailable")
                title = payload.get("title")
                title = title[:512] if isinstance(title, str) else None
                row = dict(id=str(uuid.uuid4()), owner_id=owner_id, offer_id=offer_id, title=title,
                           connection_id=offer["connection_id"], idempotency_key=key,
                           state="awaiting_approval" if requires_approval else "queued", approved_by=None,
                           add_to_my_library=bool(add_to_my_library), cancel_requested=False,
                           lease_token=None, lease_expires=None, next_attempt_at=now, claim_count=0,
                           source_sha256=None, staging_key=None, publication_proof_hash=None,
                           importing_since=None, error_code=None, created_at=now, updated_at=now,
                           client_id=payload.get('client_id') if release_key else None,
                           client_revision=payload.get('client_revision') if release_key else None,
                           release_key=release_key, external_id=None, submission_started=None,
                           download_release_key=None, bundle_parent_id=None, selected_artifact_id=None)
                conn.execute(table.insert().values(**row))
                return _job(row)
        except IntegrityError:
            with self.engine.connect() as conn:
                prior = previous(conn)
                if prior is not None:
                    return prior
            raise Conflict("Request could not be recorded") from None

    def get_job(self, owner_id, job_id):
        with self.engine.connect() as conn:
            row = conn.execute(select(self.tables.jobs).where(self.tables.jobs.c.id == job_id,
                                self.tables.jobs.c.owner_id == _user(owner_id))).mappings().first()
            if row is None:
                raise NotFound("Job is unavailable")
            return _job(row)

    def list_jobs(self, owner_id, *, limit=50):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise StorageError("Invalid list limit")
        table = self.tables.jobs
        with self.engine.connect() as conn:
            return tuple(_job(row) for row in conn.execute(select(table).where(
                table.c.owner_id == _user(owner_id)).order_by(table.c.created_at.desc(), table.c.id).limit(limit)).mappings())

    def approve(self, job_id, *, admin_actor):
        """Application must verify administrator role before entering this seam."""
        table = self.tables.jobs
        with self.engine.begin() as conn:
            _serialize_write(conn)
            changed = conn.execute(table.update().where(table.c.id == job_id,
                table.c.state == "awaiting_approval", table.c.cancel_requested.is_(False),
                table.c.connection_id.in_(select(self.tables.connections.c.id).where(
                    self.tables.connections.c.enabled.is_(True)))).values(
                state="queued", approved_by=_user(admin_actor), updated_at=self._now()))
            if changed.rowcount != 1:
                raise Conflict("Request cannot be approved")

    def reject(self, job_id, *, admin_actor):
        table = self.tables.jobs
        with self.engine.begin() as conn:
            _serialize_write(conn)
            changed = conn.execute(table.update().where(table.c.id == job_id,
                table.c.state == 'awaiting_approval', table.c.cancel_requested.is_(False)).values(
                state='rejected', approved_by=_user(admin_actor), updated_at=self._now()))
            if changed.rowcount != 1:
                raise Conflict("Only requests awaiting approval can be rejected")

    def request_cancel(self, owner_id, job_id):
        table, now = self.tables.jobs, self._now()
        with self.engine.begin() as conn:
            _serialize_write(conn)
            row = conn.execute(select(table).where(table.c.id == job_id,
                table.c.owner_id == _user(owner_id))).mappings().first()
            if row is None:
                raise NotFound("Job is unavailable")
            if row["state"] == "cancelled":
                return
            if row["state"] not in ("awaiting_approval", "awaiting_selection", "queued", "resolving", "downloading", "staged"):
                raise Conflict("Job can no longer be cancelled")
            active = row["lease_token"] and row["lease_expires"] > now
            result = conn.execute(table.update().where(table.c.id == job_id,
                table.c.state == row["state"], table.c.lease_token == row["lease_token"]).values(
                cancel_requested=True, state=row["state"] if active else "cancelled", updated_at=now))
            if result.rowcount != 1:
                raise Conflict("Job changed; refresh its status")

    def retry(self, owner_id, job_id):
        table = self.tables.jobs
        with self.engine.begin() as conn:
            _serialize_write(conn)
            row = conn.execute(select(table).where(table.c.id == job_id,
                table.c.owner_id == _user(owner_id))).mappings().first()
            if row is None:
                raise NotFound("Job is unavailable")
            if row["state"] != "failed":
                raise Conflict("Only failed jobs can be retried")
            connection = conn.execute(select(self.tables.connections.c.revision).join(self.tables.offers,
                self.tables.offers.c.connection_id == self.tables.connections.c.id).where(
                self.tables.offers.c.id == row['offer_id'], self.tables.connections.c.enabled.is_(True),
                self.tables.connections.c.deleted.is_(False),
                self.tables.connections.c.revision == self.tables.offers.c.connection_revision)).first()
            client = row['client_id'] is None or conn.execute(select(self.tables.connections.c.id, self.tables.connections.c.adapter).where(
                self.tables.connections.c.id == row['client_id'], self.tables.connections.c.enabled.is_(True),
                self.tables.connections.c.deleted.is_(False), self.tables.connections.c.revision == row['client_revision'])).first()
            if not connection or not client:
                raise ConnectionChanged("Connection changed; make a new selection before retrying")
            submission = {}
            if (row['error_code'] == 'client_job_failed' and not row['publication_proof_hash']
                    and client is not True and client.adapter in ('sabnzbd', 'nzbget')):
                # Usenet jobs get a fresh attempt. Torrents retain their hash
                # and owned tag so a repaired job is reconciled without changing
                # seeding/data policy or adopting an unrelated pre-existing hash.
                # The remote job definitely failed. A manual retry gets a new
                # durable attempt/name; uncertain or missing jobs keep theirs.
                submission = dict(external_id=None, submission_started=None, submission_key=None, submission_invalid=False)
            elif (row['error_code'] == 'client_job_failed' and client is not True
                    and client.adapter in ('qbittorrent', 'transmission')):
                # Explicit retry reopens this retained, owned attempt to new
                # subscribers. Existing terminal subscribers keep their outcomes.
                submission = dict(submission_invalid=False)
            changed = conn.execute(table.update().where(table.c.id == job_id, table.c.state == "failed").values(
                **submission,
                state="publishing" if row["publication_proof_hash"] else "queued",
                error_code="source_busy" if row["error_code"] == "source_busy" else None,
                cancel_requested=False, lease_token=None,
                lease_expires=None, next_attempt_at=max(row["next_attempt_at"], self._now()), updated_at=self._now()))
            if changed.rowcount != 1:
                raise Conflict("Job changed; refresh its status")

    def claim(self, *, lease_seconds=60, max_active=None):
        """CAS claim; concurrent schedulers cannot own the same live lease."""
        if type(lease_seconds) not in (int, float) or not math.isfinite(lease_seconds) or not 1 <= lease_seconds <= 3600:
            raise StorageError("Invalid lease duration")
        now, token, table = self._now(), random_secrets.token_urlsafe(32), self.tables.jobs
        # Keep provider backoff across manual retry/cancellation and new jobs on
        # this connection. This does not automatically retry the failed job.
        cooling = select(table.c.connection_id).where(
            table.c.error_code == "source_busy", table.c.next_attempt_at > now)
        # Reconciling a job the ingest service already owns performs no request
        # against the source. Disabling the connection, or another job cooling
        # off on it, must not strand that job in `importing` forever with its
        # published file, sidecar and private staging left on disk.
        reconcile = table.c.state.in_(RECONCILE_STATES)
        eligible = and_(table.c.state.in_(WORK_STATES), table.c.next_attempt_at <= now,
                        or_(reconcile, table.c.connection_id.not_in(cooling)),
                        or_(table.c.lease_expires.is_(None), table.c.lease_expires <= now),
                        or_(reconcile, table.c.connection_id.in_(select(self.tables.connections.c.id).where(
                            self.tables.connections.c.enabled.is_(True)))))
        if max_active is not None:
            if type(max_active) is not int or not 1 <= max_active <= 10:
                raise StorageError("Invalid active acquisition limit")
            active = select(func.count()).select_from(table).where(
                table.c.state.in_(WORK_STATES), table.c.lease_expires > now).scalar_subquery()
            eligible = and_(eligible, active < max_active)
        with self.engine.begin() as conn:
            _serialize_write(conn)
            row = conn.execute(select(table).where(eligible).order_by(table.c.next_attempt_at, table.c.id).limit(1)).mappings().first()
            if row is None:
                return None
            result = conn.execute(table.update().where(table.c.id == row["id"], eligible).values(
                lease_token=token, lease_expires=now + lease_seconds,
                claim_count=table.c.claim_count + 1, error_code=None, updated_at=now))
            if result.rowcount != 1:
                return None
            updated = dict(row, claim_count=row["claim_count"] + 1, error_code=None)
            return Claim(_job(updated), token, now + lease_seconds)

    def _live(self, job_id, token, now):
        table = self.tables.jobs
        return and_(table.c.id == job_id, table.c.lease_token == token,
                    table.c.lease_expires > now, table.c.state.in_(WORK_STATES))

    def heartbeat(self, job_id, token, *, lease_seconds=60):
        if type(lease_seconds) not in (int, float) or not math.isfinite(lease_seconds) or not 1 <= lease_seconds <= 3600:
            raise StorageError("Invalid lease duration")
        now = self._now()
        with self.engine.begin() as conn:
            _serialize_write(conn)
            result = conn.execute(self.tables.jobs.update().where(self._live(job_id, token, now)).values(
                lease_expires=now + lease_seconds, updated_at=now))
            if result.rowcount != 1:
                raise Conflict("Worker lease is no longer valid")

    def attempt_is_staged(self, job_id, staging_key):
        """Never remove an attempt adopted into durable staged/publication state.

        A replacement can reuse only the DB-recorded staging key. An abandoned
        download attempt with another key cannot later be adopted by it.
        """
        with self.engine.connect() as conn:
            return conn.execute(select(self.tables.jobs.c.id).where(
                self.tables.jobs.c.id == job_id,
                self.tables.jobs.c.staging_key == staging_key)).first() is not None

    def completed_job(self, job_id):
        """Private cleanup seam: only a committed receipt authorizes removal."""
        with self.engine.connect() as conn:
            row = conn.execute(select(self.tables.jobs).join(self.tables.receipts).where(
                self.tables.jobs.c.id == job_id,
                self.tables.jobs.c.state == "imported")).mappings().first()
            return _job(row) if row is not None else None

    def settled_job(self, job_id):
        """Private cleanup seam for every terminal state, not only success.

        A cancelled request is never claimed again, and an abandoned import is
        restarted by re-downloading, so both release their private source. A
        failure that still holds its publication capability does NOT: `retry`
        resumes that publication and needs the staged bytes it already proved.
        """
        with self.engine.connect() as conn:
            row = conn.execute(select(self.tables.jobs).where(
                self.tables.jobs.c.id == job_id)).mappings().first()
            if row is None:
                return None
            if row["state"] == "cancelled":
                return _job(row)
            if row["state"] == "failed":
                return _job(row) if row["publication_proof_hash"] is None else None
            if row["state"] != "imported":
                return None
            receipt = conn.execute(select(self.tables.receipts.c.job_id).where(
                self.tables.receipts.c.job_id == job_id)).first()
            return _job(row) if receipt is not None else None

    def importing_watch(self, job_id, token):
        """Seconds this job has been owned by the ingest service, under lease.

        A row that reached `importing` without a recorded start — an upgrade
        from before this column, or a crash between the two writes — adopts the
        current time instead of being treated as instantly expired, so nothing
        legitimately mid-import is failed the moment the bound is introduced.
        """
        now, table = self._now(), self.tables.jobs
        with self.engine.begin() as conn:
            _serialize_write(conn)
            row = conn.execute(select(table).where(self._live(job_id, token, now))).mappings().first()
            if row is None:
                raise Conflict("Worker lease is no longer valid")
            if row["state"] != "importing":
                return None
            if row["importing_since"] is None:
                conn.execute(table.update().where(table.c.id == job_id,
                    table.c.importing_since.is_(None)).values(importing_since=now))
                return 0.0
            return max(0.0, now - row["importing_since"])

    def staged_identity(self, job_id, token):
        """Private worker filesystem identity, read only with the live lease."""
        with self.engine.connect() as conn:
            row = conn.execute(select(self.tables.jobs).where(
                self._live(job_id, token, self._now()))).mappings().first()
            if row is None:
                raise Conflict("Worker lease is no longer valid")
            return row["source_sha256"], row["staging_key"]

    def external_status(self, job_id, token):
        with self.engine.connect() as conn:
            row = conn.execute(select(self.tables.jobs).where(
                self._live(job_id, token, self._now()))).mappings().first()
            if row is None:
                raise Conflict("Worker lease is no longer valid")
            return row['external_id'], row['submission_started']

    def begin_submission(self, job_id, token, *, create_if_missing=True):
        """Commit uncertainty BEFORE POST; another account can subscribe safely.

        The runtime holds one global worker slot. BEGIN IMMEDIATE additionally
        serializes the read-before-write check if another scheduler races it.
        A lost response is reconciled by an exact, deterministic client job name;
        absence is never permission to blindly resend.
        """
        table, now = self.tables.jobs, self._now()
        with self.engine.begin() as conn:
            _serialize_write(conn)
            row = conn.execute(select(table).where(self._live(job_id, token, now),
                table.c.cancel_requested.is_(False))).mappings().first()
            if row is None or not row['client_id'] or not row['release_key']:
                raise Conflict("Submission is unavailable")
            if row['submission_started'] is not None:
                return False
            previous = conn.execute(select(table).where(func.coalesce(table.c.download_release_key, table.c.release_key) == (row['download_release_key'] or row['release_key']),
                table.c.client_id == row['client_id'], table.c.client_revision == row['client_revision'],
                table.c.submission_started.is_not(None),
                table.c.submission_invalid.is_not(True)).order_by(table.c.submission_started).limit(1)).mappings().first()
            if previous is None and not create_if_missing:
                return False
            conn.execute(table.update().where(self._live(job_id, token, now)).values(
                submission_started=previous['submission_started'] if previous else now,
                external_id=previous['external_id'] if previous else None,
                submission_key=previous['submission_key'] if previous else random_secrets.token_hex(16),
                submission_invalid=False))
            return previous is None

    def _same_submission(self, row):
        table = self.tables.jobs
        identity = table.c.submission_key == row['submission_key'] if row['submission_key'] else table.c.submission_started == row['submission_started']
        return and_(func.coalesce(table.c.download_release_key, table.c.release_key) == (row['download_release_key'] or row['release_key']), table.c.client_id == row['client_id'],
                    table.c.client_revision == row['client_revision'], identity)

    def clear_rejected_submission(self, job_id, token, *, error_code='client_error'):
        """A definite rejection resolves all adopters of this attempt as well."""
        if error_code not in ('needs_auth', 'client_error'):
            raise StorageError('Submission rejection is not definite')
        table, now = self.tables.jobs, self._now()
        with self.engine.begin() as conn:
            _serialize_write(conn)
            row = conn.execute(select(table).where(self._live(job_id, token, now),
                table.c.external_id.is_(None), table.c.submission_started.is_not(None))).mappings().first()
            if row is None:
                raise Conflict("Submission identity is already recorded")
            # Invalidity belongs to the shared attempt, including terminal
            # subscribers. Their cancelled/rejected/imported states stay intact.
            conn.execute(table.update().where(self._same_submission(row)).values(submission_invalid=True))
            conn.execute(table.update().where(self._same_submission(row), table.c.id != job_id,
                table.c.external_id.is_(None), table.c.state.in_(('queued', 'resolving', 'downloading', 'failed'))).values(
                state='failed', error_code=error_code, submission_started=None, submission_key=None,
                lease_token=None, lease_expires=None, next_attempt_at=now, updated_at=now))
            conn.execute(table.update().where(self._live(job_id, token, now)).values(
                submission_started=None, submission_key=None))

    def fail_submission_adopters(self, job_id, token):
        """A failed remote download is definite for every subscriber to it."""
        table, now = self.tables.jobs, self._now()
        with self.engine.begin() as conn:
            _serialize_write(conn)
            row = conn.execute(select(table).where(self._live(job_id, token, now))).mappings().first()
            if row is None or row['submission_started'] is None:
                raise Conflict('Submission is unavailable')
            conn.execute(table.update().where(self._same_submission(row)).values(submission_invalid=True))
            conn.execute(table.update().where(self._same_submission(row), table.c.id != job_id,
                table.c.state.in_(('queued', 'resolving', 'downloading', 'failed'))).values(
                state='failed', error_code='client_job_failed', lease_token=None,
                lease_expires=None, next_attempt_at=now, updated_at=now))

    def submission_identity(self, job_id, token):
        with self.engine.connect() as conn:
            row = conn.execute(select(self.tables.jobs).where(
                self._live(job_id, token, self._now()))).mappings().first()
            if row is None:
                raise Conflict("Worker lease is no longer valid")
            return row['external_id'], row['submission_started'], row['submission_key']

    def submission_age(self, job_id, token):
        _, started, _ = self.submission_identity(job_id, token)
        return max(0, self._now() - started) if started is not None else 0

    def record_external(self, job_id, token, external_id):
        if not isinstance(external_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', external_id):
            raise StorageError("Invalid external job identity")
        table = self.tables.jobs
        with self.engine.begin() as conn:
            _serialize_write(conn)
            result = conn.execute(table.update().where(self._live(job_id, token, self._now()),
                table.c.submission_started.is_not(None),
                or_(table.c.external_id.is_(None), table.c.external_id == external_id)).values(external_id=external_id))
            if result.rowcount != 1:
                raise Conflict("External job identity changed")

    def material(self, job_id, token):
        """Private worker-only payload; caller must not serialize/log it."""
        with self.engine.connect() as conn:
            row = conn.execute(select(self.tables.jobs).where(self._live(job_id, token, self._now()))).mappings().first()
            if row is None or row["cancel_requested"]:
                raise Conflict("Worker lease is no longer actionable")
            connection = conn.execute(select(self.tables.connections).where(
                self.tables.connections.c.id == row["connection_id"],
                self.tables.connections.c.enabled.is_(True))).mappings().first()
            offer = conn.execute(select(self.tables.offers).where(
                self.tables.offers.c.id == row["offer_id"], self.tables.offers.c.owner_id == row["owner_id"])).mappings().first()
            if connection is None or offer is None or connection["revision"] != offer["connection_revision"]:
                raise ConnectionChanged("Connection changed or is unavailable")
            config = self.box.open(_sealed(connection, "config"), scope="deployment",
                identity=connection["id"], field_name=f"connection-config:{connection['revision']}")
            scope = f"user:{row['owner_id']}:connection:{connection['id']}:revision:{offer['connection_revision']}"
            payload = self.box.open(_sealed(offer, "payload"), scope=scope, identity=offer["id"], field_name="offer")
            return Material(json.loads(config), json.loads(payload))

    def advance(self, job_id, token, expected_state, state, *, source_sha256=None, staging_key=None,
                error_code=None, retry_delay_seconds=0, abandon_publication=False):
        """Fenced transition; failed jobs may preserve a bounded earliest retry.

        abandon_publication additionally drops the issued capability and staged
        identity in the SAME transaction as the failure. That is what makes
        giving up on an import safe: a late acknowledgment can no longer match
        `publication_proof_hash`, so it loses cleanly instead of recording a
        receipt for bytes the worker is about to delete, and `retry` then
        restarts the request at `queued` rather than resuming a publication
        whose source is gone.
        """
        if (type(retry_delay_seconds) not in (int, float) or not math.isfinite(retry_delay_seconds)
                or not 0 <= retry_delay_seconds <= 86400
                or retry_delay_seconds and state != "failed"):
            raise StorageError("Invalid retry delay")
        if abandon_publication and state != "failed":
            raise StorageError("Only a failed job abandons its publication")
        if state not in TRANSITIONS.get(expected_state, ()):
            raise Conflict("Unsupported job transition")
        values = dict(state=state, updated_at=self._now())
        if state == "failed":
            values["next_attempt_at"] = values["updated_at"] + retry_delay_seconds
        if state == "importing":
            values["importing_since"] = values["updated_at"]
        if abandon_publication:
            values.update(publication_proof_hash=None, source_sha256=None,
                          staging_key=None, importing_since=None)
        if state == "staged":
            values["source_sha256"] = _digest(source_sha256)
            if not isinstance(staging_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", staging_key):
                raise StorageError("Invalid staging identity")
            values["staging_key"] = staging_key
        if error_code is not None:
            if not isinstance(error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code):
                raise StorageError("Invalid job error code")
            values["error_code"] = error_code
        if state in ("failed", "cancelled"):
            values.update(lease_token=None, lease_expires=None)
        table = self.tables.jobs
        conditions = [self._live(job_id, token, values["updated_at"]), table.c.state == expected_state]
        if state == "staged":
            conditions.append(table.c.publication_proof_hash.is_(None))
        if state != "cancelled":
            conditions.append(table.c.cancel_requested.is_(False))
        with self.engine.begin() as conn:
            _serialize_write(conn)
            result = conn.execute(table.update().where(*conditions).values(**values))
            if result.rowcount != 1:
                raise Conflict("Job changed or worker lease expired")

    def release(self, job_id, token, *, delay_seconds=0):
        if type(delay_seconds) not in (int, float) or not math.isfinite(delay_seconds) or not 0 <= delay_seconds <= 86400:
            raise StorageError("Invalid retry delay")
        now = self._now()
        with self.engine.begin() as conn:
            _serialize_write(conn)
            result = conn.execute(self.tables.jobs.update().where(self._live(job_id, token, now)).values(
                lease_token=None, lease_expires=None, next_attempt_at=now + delay_seconds, updated_at=now))
            if result.rowcount != 1:
                raise Conflict("Worker lease is no longer valid")

    @staticmethod
    def _publication_proof(token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", token):
            raise StorageError("Invalid publication capability")
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def prepare_publication(self, job_id, lease_token, publication_token, *, authorize=None):
        """Fence publication with durable proof independent of worker leases.

        The caller generates a cryptographically random token and persists it
        privately alongside the staged file BEFORE calling this method. Store
        only its hash in app.db. A retry must reuse that token; a lost token
        requires explicit reconciliation, never blind rotation/republication.
        No filesystem write occurs here. Caller checks proof before publishing.
        New capabilities recheck source/client identity and an optional pure
        authorization callback under the same write reservation. Already issued
        capabilities retain their existing receipt-reconciliation authority.
        """
        proof, now, table = self._publication_proof(publication_token), self._now(), self.tables.jobs
        with self.engine.begin() as conn:
            _serialize_write(conn)
            row = conn.execute(select(table).where(self._live(job_id, lease_token, now))).mappings().first()
            if row is None or row["cancel_requested"]:
                raise Conflict("Worker lease is not actionable")
            if row["publication_proof_hash"] is not None:
                if (not random_secrets.compare_digest(row["publication_proof_hash"], proof)
                        or row["state"] not in ("publishing", "importing")):
                    raise Conflict("Publication already has a different capability")
            else:
                if row["state"] != "staged" or not row["source_sha256"] or not row["staging_key"]:
                    raise Conflict("Job is not ready for publication")
                if row['client_id'] is not None:
                    self._bundle_fence(conn, row)
                else:
                    connections, offers = self.tables.connections, self.tables.offers
                    source = conn.execute(select(connections.c.id).join(offers,
                        offers.c.connection_id == connections.c.id).where(
                        offers.c.id == row['offer_id'], offers.c.owner_id == row['owner_id'],
                        offers.c.connection_revision == connections.c.revision,
                        connections.c.enabled.is_(True), connections.c.deleted.is_(False))).first()
                    if source is None:
                        raise ConnectionChanged('Publication connection changed or is unavailable')
                if authorize is not None:
                    authorize()
                updated = conn.execute(table.update().where(self._live(job_id, lease_token, now),
                    table.c.state == "staged", table.c.publication_proof_hash.is_(None),
                    table.c.cancel_requested.is_(False)).values(
                    publication_proof_hash=proof, state="publishing", updated_at=now))
                if updated.rowcount != 1:
                    raise Conflict("Job changed before publication")
            return PublicationPermit(job_id, row["source_sha256"], row["staging_key"], publication_token)

    def publication_intent(self, job_id, publication_token, staging_key):
        """Trusted sidecar seam: resolve DB identity, never accept sidecar user IDs.

        Integration must match its canonical generated path to staging_key and
        independently hash the actual source before importing. This function
        does not inspect the filesystem or authorize arbitrary paths.
        """
        proof = self._publication_proof(publication_token)
        with self.engine.connect() as conn:
            row = conn.execute(select(self.tables.jobs).where(self.tables.jobs.c.id == job_id)).mappings().first()
            if (row is None or row["publication_proof_hash"] is None
                    or not random_secrets.compare_digest(row["publication_proof_hash"], proof)
                    or row["staging_key"] != staging_key
                    or row["state"] not in ("publishing", "importing", "failed", "imported")):
                raise NotFound("Publication intent is unavailable")
            return _job(row), PublicationPermit(job_id, row["source_sha256"], row["staging_key"], publication_token)

    def finalize_import(self, job_id, publication_token, outcome, *, staging_key, finalize_membership):
        """Commit receipt and real membership work in ONE app.db transaction.

        Durable publication proof survives worker lease expiry/reclaim. The
        caller verifies actual source hash and canonical path/staging identity.
        finalize_membership(connection, job, outcome) MUST validate authoritative
        Calibre result/book IDs and write membership on this connection without
        committing independently. No fallback/inferred book IDs accepted.
        The ingest integration retains source until this method returns.
        """
        if not isinstance(outcome, ImportOutcome) or not callable(finalize_membership):
            raise StorageError("Import finalization requires an outcome and transaction callback")
        now, receipts, jobs = self._now(), self.tables.receipts, self.tables.jobs
        proof = self._publication_proof(publication_token)

        def existing(conn):
            old = conn.execute(select(receipts).where(receipts.c.job_id == job_id)).mappings().first()
            if old is None:
                return False
            identity = conn.execute(select(jobs.c.staging_key).where(jobs.c.id == job_id)).scalar_one()
            if (not random_secrets.compare_digest(old["proof_hash"], proof)
                    or identity != staging_key or old["source_sha256"] != outcome.source_sha256
                    or old["imported_sha256"] != outcome.imported_sha256
                    or tuple(json.loads(old["book_ids_json"])) != outcome.book_ids
                    or old["disposition"] != outcome.disposition):
                raise Conflict("Import receipt already records another result")
            return True

        with self.engine.begin() as conn:
            _serialize_write(conn)
            if existing(conn):
                return outcome
            # UPDATE first fences concurrent acknowledgments before membership.
            # A legitimate completed import can acknowledge even if its worker
            # subsequently timed out/failed: proof was issued before publication.
            result = conn.execute(jobs.update().where(jobs.c.id == job_id,
                jobs.c.publication_proof_hash == proof, jobs.c.staging_key == staging_key,
                jobs.c.state.in_(("publishing", "importing", "failed")),
                jobs.c.source_sha256 == outcome.source_sha256).values(
                state="imported", error_code=None, lease_token=None, lease_expires=None, updated_at=now))
            if result.rowcount != 1:
                if existing(conn):
                    return outcome
                raise Conflict("Import job is not ready for this receipt")
            row = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().one()
            finalize_membership(conn, _job(row), outcome)
            conn.execute(receipts.insert().values(job_id=job_id, source_sha256=outcome.source_sha256,
                imported_sha256=outcome.imported_sha256, book_ids_json=json.dumps(outcome.book_ids),
                disposition=outcome.disposition, proof_hash=proof, created_at=now))
        return outcome

    def get_receipt(self, owner_id, job_id):
        with self.engine.connect() as conn:
            job = conn.execute(select(self.tables.jobs).where(self.tables.jobs.c.id == job_id,
                self.tables.jobs.c.owner_id == _user(owner_id))).mappings().first()
            if job is None:
                raise NotFound("Job is unavailable")
            receipt = conn.execute(select(self.tables.receipts).where(
                self.tables.receipts.c.job_id == job_id)).mappings().first()
            if receipt is None:
                return None
            return ImportOutcome(receipt["source_sha256"], receipt["imported_sha256"],
                                 tuple(json.loads(receipt["book_ids_json"])), receipt["disposition"])
