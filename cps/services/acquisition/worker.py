# SPDX-License-Identifier: GPL-3.0-or-later
"""One durable acquisition step, using the existing scheduler and ingest service.

No scheduler is registered on import. Runtime supplies current account access,
feature availability and configured paths; tests can drive the same state
machine against an owned local catalog without a Flask request or fake user.
"""
from pathlib import Path
import re
import secrets
import shutil
import time

from .catalog import policy
from .http import TransportError, run_transfer
from .staging import StagingError, digest, persist_capability, publish, validate_book, cleanup_completed
from .storage import Conflict


class Paused(Exception):
    pass


class Cancelled(Exception):
    pass


class AcquisitionWorker:
    def __init__(self, repository, staging_dir, ingest_dir, *, allowed,
                 enabled=lambda: True, execution_allowed=None, media_allowed=lambda media: True,
                 transfer=run_transfer, max_bytes=100 * 1024 * 1024):
        self.repository = repository
        self.staging_dir, self.ingest_dir = Path(staging_dir), Path(ingest_dir)
        self.allowed, self.enabled, self.transfer = allowed, enabled, transfer
        self.max_bytes = max_bytes
        self.media_allowed = media_allowed
        self.execution_allowed = execution_allowed or (lambda job: self.allowed(job.owner_id))

    def _directory(self, job_id, key):
        if not re.fullmatch(r'[0-9a-f-]{36}', job_id) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key):
            raise StagingError('invalid_staging_identity')
        for path in (self.staging_dir, self.staging_dir / job_id, self.staging_dir / job_id / key):
            if path.is_symlink():
                raise StagingError('staging_directory_unavailable')
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not path.is_dir():
                raise StagingError('staging_directory_unavailable')
        return self.staging_dir / job_id / key

    def cleanup_completed(self):
        if not self.staging_dir.is_dir() or self.staging_dir.is_symlink():
            return
        for directory in self.staging_dir.iterdir():
            if not re.fullmatch(r'[0-9a-f-]{36}', directory.name):
                continue
            if self.repository.completed_job(directory.name) is not None:
                self._cleanup(directory.name)

    def run_once(self):
        self.cleanup_completed()
        if not self.enabled():
            return None
        claim = self.repository.claim(lease_seconds=60, max_active=1)
        if claim is None:
            return None
        repo, job, token = self.repository, claim.job, claim.token
        state = job.state
        private = None
        last_heartbeat = 0.0
        media_type = None

        def checkpoint():
            nonlocal last_heartbeat
            current = repo.get_job(job.owner_id, job.id)
            if current.state == 'imported':
                raise Conflict('Import already acknowledged')
            if not self.enabled():
                raise Paused()
            if current.cancel_requested and state not in ('publishing', 'importing'):
                raise Cancelled()
            if state not in ('publishing', 'importing') and not self.execution_allowed(job):
                raise TransportError('access_revoked')
            if state not in ('publishing', 'importing') and media_type is not None and not self.media_allowed(media_type):
                raise TransportError('format_not_allowed')
            now = time.monotonic()
            if now - last_heartbeat >= 5:
                repo.heartbeat(job.id, token, lease_seconds=60)
                # Includes current connection enabled/revision and cancel fences.
                repo.material(job.id, token)
                last_heartbeat = now

        def advance(next_state, **kwargs):
            nonlocal state
            repo.advance(job.id, token, state, next_state, **kwargs)
            state = next_state

        def settle(operation):
            # A lease may expire between observing cancellation/error and its
            # final transition. Keep the replacement worker authoritative.
            try:
                operation()
            except Conflict:
                pass

        try:
            checkpoint()
            material = repo.material(job.id, token)
            offer, config = material.offer, material.config
            if offer.get('kind') != 'acquisition' or offer.get('media_type') not in ('application/epub+zip', 'application/pdf'):
                raise TransportError('unsupported_offer')
            media_type = offer['media_type']
            checkpoint()
            extension = 'epub' if media_type == 'application/epub+zip' else 'pdf'
            if state == 'queued':
                advance('resolving')
            if state == 'resolving':
                advance('downloading')
            if state == 'downloading':
                private = self._directory(job.id, token)
                source = private / 'source.part'
                # The new claim always gets a fresh private attempt. No Range
                # requests or blind reuse of an interrupted download are implied.
                if shutil.disk_usage(private).free < self.max_bytes + 64 * 1024 * 1024:
                    raise TransportError('insufficient_storage')
                downloaded = self.transfer(offer['href'], policy(config, download=True),
                    destination=source, max_bytes=self.max_bytes, checkpoint=checkpoint)
                validate_book(source, offer['media_type'], max_bytes=self.max_bytes)
                if digest(source) != downloaded.sha256:
                    raise StagingError('source_changed')
                checkpoint()
                advance('staged', source_sha256=downloaded.sha256, staging_key=token)
            source_hash, staging_key = repo.staged_identity(job.id, token)
            private = self._directory(job.id, staging_key)
            source = private / 'source.part'
            if digest(source) != source_hash:
                raise StagingError('source_changed')
            token_path = private / 'publication.token'
            if token_path.exists() or token_path.is_symlink():
                if token_path.is_symlink() or not token_path.is_file() or token_path.stat().st_size > 128:
                    raise StagingError('publication_token_conflict')
                publication_token = token_path.read_text()
            elif state != 'staged':
                # A previously issued capability may already be in the watcher.
                # Do not manufacture another token and repeat publication.
                raise StagingError('publication_token_missing')
            else:
                publication_token = secrets.token_urlsafe(32)
                persist_capability(token_path, publication_token)
            checkpoint()
            permit = repo.prepare_publication(job.id, token, publication_token)
            if state == 'staged':
                state = 'publishing'
            publish(source, self.ingest_dir, permit, extension, checkpoint=checkpoint)
            if state == 'publishing':
                advance('importing')
            repo.release(job.id, token, delay_seconds=5)
        except Cancelled:
            settle(lambda: advance('cancelled'))
        except Paused:
            settle(lambda: repo.release(job.id, token, delay_seconds=5))
        except (TransportError, StagingError) as error:
            code = error.code if isinstance(error, TransportError) else str(error)
            if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', code):
                code = 'acquisition_failed'
            # HTTP parsing bounds valid Retry-After to one day. Missing or
            # malformed hints get a minute, without an automatic retry loop.
            retry_delay = 0
            if code == 'source_busy':
                hint = error.retry_after if isinstance(error, TransportError) else None
                retry_delay = min(86400, max(0, hint)) if type(hint) is int else 60
            settle(lambda: advance('failed', error_code=code, retry_delay_seconds=retry_delay))
        except Conflict:
            # A receipt or another valid claimant may have won. Never overwrite
            # its state to make this stale attempt look authoritative.
            pass
        except (OSError, ValueError):
            settle(lambda: advance('failed', error_code='acquisition_failed'))
        current = repo.get_job(job.owner_id, job.id)
        if current.state in ('failed', 'cancelled') and private is not None and not repo.attempt_is_staged(job.id, private.name):
            if private.is_dir() and not private.is_symlink():
                shutil.rmtree(private)
                try:
                    private.parent.rmdir()
                except OSError:
                    pass
        if current.state == 'imported' and repo.get_receipt(job.owner_id, job.id) is not None:
            self._cleanup(job.id)
        return current

    def _cleanup(self, job_id):
        cleanup_completed(self.repository, self.staging_dir, job_id)
