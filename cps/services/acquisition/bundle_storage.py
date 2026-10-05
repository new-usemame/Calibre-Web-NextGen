# SPDX-License-Identifier: GPL-3.0-or-later
"""Owner-bound artifact choices; no filesystem or remote I/O in transactions."""
import json
import re
import uuid
from pathlib import PurePosixPath

from sqlalchemy import select

from .contracts import DIRECT_FORMATS


def _storage():
    # Repository inherits this mixin; resolving exceptions lazily avoids a
    # module-import cycle without creating another persistence model.
    from . import storage
    return storage


class BundleChoicesMixin:
    def _bundle_fence(self, conn, row):
        s = _storage()
        c, offers = self.tables.connections, self.tables.offers
        source = conn.execute(select(c.c.id).join(offers, offers.c.connection_id == c.c.id).where(
            offers.c.id == row['offer_id'], offers.c.owner_id == row['owner_id'],
            offers.c.connection_revision == c.c.revision, c.c.enabled.is_(True), c.c.deleted.is_(False))).first()
        client = conn.execute(select(c.c.id).where(c.c.id == row['client_id'],
            c.c.revision == row['client_revision'], c.c.enabled.is_(True), c.c.deleted.is_(False))).first()
        if source is None or client is None:
            raise s.ConnectionChanged('Bundle connection changed or is unavailable')

    def _bundle(self, conn, owner_id, job_id):
        s, jobs = _storage(), self.tables.jobs
        row = conn.execute(select(jobs).where(jobs.c.id == job_id,
            jobs.c.owner_id == s._user(owner_id))).mappings().first()
        if row is None:
            raise s.NotFound('Bundle is unavailable')
        parent = conn.execute(select(jobs).where(jobs.c.id == (row['bundle_parent_id'] or row['id']),
            jobs.c.owner_id == row['owner_id'])).mappings().first()
        table = self.tables.manifests
        record = conn.execute(select(table).where(table.c.job_id == parent['id'])).mappings().first() if parent else None
        if record is None:
            raise s.NotFound('Bundle is unavailable')
        self._bundle_fence(conn, parent)
        manifest = json.loads(self.box.open(s._sealed(record, 'payload'),
            scope=f"user:{parent['owner_id']}:bundle:{parent['id']}", identity=parent['id'],
            field_name='bundle-manifest:' + record['generation']))
        return parent, manifest

    @staticmethod
    def _validate_manifest(manifest):
        s = _storage()
        if not isinstance(manifest, dict) or set(manifest) != {'generation', 'candidates'}:
            raise s.StorageError('Invalid bundle manifest')
        if not isinstance(manifest['generation'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', manifest['generation']):
            raise s.StorageError('Invalid bundle generation')
        candidates = manifest['candidates']
        if not isinstance(candidates, list) or not 2 <= len(candidates) <= 20:
            raise s.StorageError('Invalid bundle candidate count')
        ids, paths, total = set(), set(), 0
        for c in candidates:
            if not isinstance(c, dict) or set(c) != {'id', 'name', 'relative_path', 'media_type', 'size', 'sha256'}:
                raise s.StorageError('Invalid bundle candidate')
            s._digest(c['id']); s._digest(c['sha256'])
            p = c['relative_path']
            if (not isinstance(p, str) or not p or len(p) > 4096 or '\\' in p or '\x00' in p
                    or PurePosixPath(p).is_absolute() or any(x in ('', '.', '..') for x in p.split('/'))):
                raise s.StorageError('Invalid bundle file identity')
            if (not isinstance(c['name'], str) or not c['name'] or len(c['name']) > 240
                    or any(ord(x) < 32 or ord(x) == 127 for x in c['name'])):
                raise s.StorageError('Invalid bundle display name')
            if (not isinstance(c['media_type'], str) or c['media_type'] not in DIRECT_FORMATS
                    or type(c['size']) is not int or not 0 < c['size'] <= 100*1024*1024):
                raise s.StorageError('Invalid bundle file')
            if c['id'] in ids or p in paths:
                raise s.StorageError('Duplicate bundle candidate')
            ids.add(c['id']); paths.add(p); total += c['size']
        if total > 512*1024*1024:
            raise s.StorageError('Bundle exceeds fingerprint limit')

    def await_choices(self, job_id, token, manifest):
        """Commit a snapshot and release the lease; waiting is never polled."""
        s, jobs, table, now = _storage(), self.tables.jobs, self.tables.manifests, self._now()
        self._validate_manifest(manifest)
        with self.engine.begin() as conn:
            s._serialize_write(conn)
            row = conn.execute(select(jobs).where(self._live(job_id, token, now),
                jobs.c.state == 'downloading', jobs.c.cancel_requested.is_(False),
                jobs.c.selected_artifact_id.is_(None))).mappings().first()
            if row is None:
                raise s.Conflict('Bundle discovery is no longer actionable')
            self._bundle_fence(conn, row)
            if conn.execute(select(table.c.job_id).where(table.c.job_id == job_id)).first():
                raise s.Conflict('Bundle already has a snapshot')
            values = dict(job_id=job_id, generation=manifest['generation'], created_at=now)
            values.update(s._sealed_values('payload', self._encode(manifest,
                scope=f"user:{row['owner_id']}:bundle:{job_id}", identity=job_id,
                field_name='bundle-manifest:' + manifest['generation'])))
            conn.execute(table.insert().values(**values))
            conn.execute(jobs.update().where(self._live(job_id, token, now)).values(
                state='awaiting_selection', bundle_parent_id=job_id,
                lease_token=None, lease_expires=None, updated_at=now))

    def bundle_choices(self, owner_id, job_id):
        """Only safe display descriptions and this owner's selected job IDs."""
        with self.engine.connect() as conn:
            parent, manifest = self._bundle(conn, owner_id, job_id)
            jobs = self.tables.jobs
            chosen = {r['selected_artifact_id']:r for r in conn.execute(select(jobs.c.id, jobs.c.state,
                jobs.c.selected_artifact_id).where(jobs.c.owner_id == parent['owner_id'],
                jobs.c.bundle_parent_id == parent['id'])).mappings() if r['selected_artifact_id']}
            choices = []
            for c in manifest['candidates']:
                item = dict(id=c['id'], name=c['name'], format=DIRECT_FORMATS[c['media_type']][0], size=c['size'])
                if c['id'] in chosen:
                    item.update(job_id=chosen[c['id']]['id'], state=chosen[c['id']]['state'])
                choices.append(item)
            return dict(generation=manifest['generation'], candidates=choices)

    def select_book(self, owner_id, job_id, generation, candidate_id, *, requires_approval=True, authorize_selection=None):
        """Serialize first/additional choice; never mint another remote identity.

        The application checks current account policy. Its optional pure callback
        runs under the same write reservation and returns current approval policy.
        """
        s, jobs, now = _storage(), self.tables.jobs, self._now()
        with self.engine.begin() as conn:
            s._serialize_write(conn)
            parent, manifest = self._bundle(conn, owner_id, job_id)
            if parent['selected_artifact_id'] is None and (parent['cancel_requested'] or parent['state'] in ('cancelled', 'rejected')):
                raise s.Conflict('Bundle request was cancelled')
            candidate = next((c for c in manifest['candidates'] if c['id'] == candidate_id), None)
            if manifest['generation'] != generation or candidate is None:
                raise s.Conflict('Bundle choice changed; refresh its files')
            if authorize_selection is not None:
                requires_approval = bool(authorize_selection(conn, parent, candidate))
            existing = conn.execute(select(jobs).where(jobs.c.owner_id == parent['owner_id'],
                jobs.c.bundle_parent_id == parent['id'], jobs.c.selected_artifact_id == candidate_id)).mappings().first()
            if existing is not None:
                return s._job(existing)
            if parent['selected_artifact_id'] is None:
                if parent['state'] != 'awaiting_selection':
                    raise s.Conflict('Bundle is no longer awaiting a choice')
                state = 'awaiting_approval' if requires_approval and parent['approved_by'] is None else 'queued'
                conn.execute(jobs.update().where(jobs.c.id == parent['id'], jobs.c.state == 'awaiting_selection').values(
                    selected_artifact_id=candidate_id, title=candidate['name'], state=state, next_attempt_at=now, updated_at=now))
                return s._job(conn.execute(select(jobs).where(jobs.c.id == parent['id'])).mappings().one())
            # Nullable download key preserves the old owner/release anchor and
            # its UNIQUE constraint. Sibling import identity is purpose-separated.
            identity = self.box.display_identity(json.dumps(['bundle-artifact-import-v1', parent['id'], candidate_id]))
            values = dict(parent)
            values.update(id=str(uuid.uuid4()), idempotency_key='bundle:' + identity, release_key=identity,
                download_release_key=parent['download_release_key'] or parent['release_key'],
                bundle_parent_id=parent['id'], selected_artifact_id=candidate_id, title=candidate['name'],
                state='awaiting_approval' if requires_approval else 'queued', approved_by=None,
                source_sha256=None, staging_key=None, publication_proof_hash=None, importing_since=None,
                cancel_requested=False, error_code=None, claim_count=0, lease_token=None, lease_expires=None,
                next_attempt_at=now, created_at=now, updated_at=now)
            conn.execute(jobs.insert().values(**values))
            return s._job(values)

    def selected_artifact(self, job_id, token):
        """Private worker-only exact artifact identity under its live lease."""
        s, jobs = _storage(), self.tables.jobs
        with self.engine.connect() as conn:
            row = conn.execute(select(jobs).where(self._live(job_id, token, self._now()),
                jobs.c.cancel_requested.is_(False))).mappings().first()
            if row is None:
                raise s.Conflict('Worker lease is no longer actionable')
            if row['selected_artifact_id'] is None:
                return None
            parent, manifest = self._bundle(conn, row['owner_id'], row['id'])
            candidate = next((c for c in manifest['candidates'] if c['id'] == row['selected_artifact_id']), None)
            if candidate is None:
                raise s.StorageError('Selected artifact identity is unavailable')
            return candidate
