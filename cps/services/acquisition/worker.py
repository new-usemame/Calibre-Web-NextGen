# SPDX-License-Identifier: GPL-3.0-or-later
"""One durable acquisition step, using the existing scheduler and ingest service.

No scheduler is registered on import. Runtime supplies current account access,
feature availability and configured paths; tests can drive the same state
machine against an owned local catalog without a Flask request or fake user.
"""
from pathlib import Path
import re
import os
from dataclasses import replace
import secrets
import shutil
import time

from .catalog import policy
from .contracts import DIRECT_FORMATS, MOBI_MEDIA_TYPE, direct_format_allowed
from .http import TransportError, run_transfer
from .staging import (StagingError, cleanup_settled, digest, discard_publication, persist_capability,
                      publish, publication_state, validate_book)
from .storage import Conflict
from .clients import CLIENTS, USENET_KINDS, TORRENT_KINDS, torrent_book, torrent_books
from .torrent import magnet_identities, validate_torrent
from .sabnzbd import SABClient, ClientError, completed_book, completed_books, open_completed_file, validate_nzb, _safe_root
from .bundle_files import fingerprint_candidates

# A conversion of a large book can legitimately run for a long time, and a
# terminal processor result is reported explicitly, so this bound only exists
# to catch a processor that died without saying anything at all.
IMPORT_DEADLINE_SECONDS = 6 * 3600
# An `importing` job is waiting on another service. Re-examining it every few
# seconds is what turned a stuck import into a hot loop.
IMPORT_RECHECK_SECONDS = 30


DOWNLOAD_DEADLINE_SECONDS = 7 * 24 * 60 * 60


class Paused(Exception):
    pass


class Cancelled(Exception):
    pass


class AcquisitionWorker:
    def __init__(self, repository, staging_dir, ingest_dir, *, allowed,
                 enabled=lambda: True, execution_allowed=None, media_allowed=lambda media: True,
                 transfer=run_transfer, max_bytes=100 * 1024 * 1024,
                 import_deadline_seconds=IMPORT_DEADLINE_SECONDS,
                 import_recheck_seconds=IMPORT_RECHECK_SECONDS, client_factory=None,
                 download_deadline_seconds=DOWNLOAD_DEADLINE_SECONDS):
        self.repository = repository
        self.client_factory = client_factory
        self.download_deadline_seconds = download_deadline_seconds
        self.staging_dir, self.ingest_dir = Path(staging_dir), Path(ingest_dir)
        self.allowed, self.enabled, self.transfer = allowed, enabled, transfer
        self.max_bytes = max_bytes
        self.media_allowed = media_allowed
        self.execution_allowed = execution_allowed or (lambda job: self.allowed(job.owner_id))
        self.import_deadline_seconds = import_deadline_seconds
        self.import_recheck_seconds = import_recheck_seconds

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
            # Every settled job, so a request cancelled while nothing held it
            # — which is never claimed again — still releases its source.
            self._cleanup(directory.name)

    def run_once(self):
        self.cleanup_completed()
        if not self.enabled():
            return None
        claim = self.repository.claim(lease_seconds=60, max_active=1)
        if claim is None:
            return None
        if claim.job.state == 'importing':
            return self._reconcile_import(claim)
        repo, job, token = self.repository, claim.job, claim.token
        state = job.state
        private = None
        last_heartbeat = 0.0
        media_type = None
        usenet = False

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
            if state not in ('publishing', 'importing') and usenet and media_type == MOBI_MEDIA_TYPE:
                _, current_client = self._client_material(offer)
                if current_client.get('allow_mobi') is not True:
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
            if (offer.get('kind') != 'acquisition'
                    or offer.get('media_type') not in tuple(DIRECT_FORMATS) + ('application/x-nzb', 'application/x-bittorrent')):
                raise TransportError('unsupported_offer')
            usenet = offer.get('transport') in ('nzb', 'torrent')
            media_type = None if usenet else offer['media_type']
            if not usenet and not direct_format_allowed(media_type, config):
                raise TransportError('unsupported_offer')
            checkpoint()
            extension = DIRECT_FORMATS.get(media_type, (None, None))[1]
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
                if usenet:
                    completed = self._download_client(job, token, offer, config, source, checkpoint)
                    if completed is None:
                        current = repo.get_job(job.owner_id, job.id)
                        if current.state == 'awaiting_selection':
                            # Snapshot discovery wrote no source. Its lease is
                            # already released and this state is not claimable.
                            shutil.rmtree(private)
                            private.parent.rmdir()
                            return current
                        repo.release(job.id, token, delay_seconds=30)
                        shutil.rmtree(private)
                        return repo.get_job(job.owner_id, job.id)
                    media_type = completed
                    checkpoint()
                    validate_book(source, media_type, max_bytes=self.max_bytes)
                    source_hash = digest(source)
                else:
                    downloaded = self.transfer(offer['href'], policy(config, download=True),
                        destination=source, max_bytes=self.max_bytes, checkpoint=checkpoint)
                    validate_book(source, offer['media_type'], max_bytes=self.max_bytes)
                    if digest(source) != downloaded.sha256:
                        raise StagingError('source_changed')
                    source_hash = downloaded.sha256
                checkpoint()
                advance('staged', source_sha256=source_hash, staging_key=token)
            source_hash, staging_key = repo.staged_identity(job.id, token)
            private = self._directory(job.id, staging_key)
            source = private / 'source.part'
            if digest(source) != source_hash:
                raise StagingError('source_changed')
            if usenet:
                with source.open('rb') as stream:
                    signature = stream.read(68)
                # A staged client source is already hash-bound, but its format
                # must survive a new worker. Magic selects the existing strict
                # byte validator; it never grants admission on its own.
                media_type = ('application/pdf' if signature[:5] == b'%PDF-' else
                              MOBI_MEDIA_TYPE if signature[60:68] == b'BOOKMOBI' else
                              'application/epub+zip')
                if signature[:5] == b'%PDF-' and signature[60:68] == b'BOOKMOBI':
                    # A legal PalmDB name can begin with the PDF marker, and a
                    # legal PDF can contain BOOKMOBI at this offset. Resolve
                    # that ambiguity with the existing full validators.
                    try:
                        validate_book(source, MOBI_MEDIA_TYPE, max_bytes=self.max_bytes)
                    except StagingError:
                        pass  # Still requires the PDF validator below.
                    else:
                        media_type = MOBI_MEDIA_TYPE
                validate_book(source, media_type, max_bytes=self.max_bytes)
                extension = DIRECT_FORMATS[media_type][1]
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
            def authorize_publication():
                # Pure reads while the repository holds the capability's write
                # reservation: settings/grants cannot change between this
                # check and issuance. Do not heartbeat or perform file I/O here.
                if not self.enabled():
                    raise Paused()
                if not self.execution_allowed(job):
                    raise TransportError('access_revoked')
                if not self.media_allowed(media_type):
                    raise TransportError('format_not_allowed')
                if usenet:
                    _, current_client = self._client_material(offer)
                    if media_type == MOBI_MEDIA_TYPE and current_client.get('allow_mobi') is not True:
                        raise TransportError('format_not_allowed')
                elif not direct_format_allowed(media_type, repo.connection_config(job.connection_id).config):
                    raise TransportError('unsupported_offer')
            permit = repo.prepare_publication(job.id, token, publication_token,
                                              authorize=authorize_publication)
            if state == 'staged':
                state = 'publishing'
            publish(source, self.ingest_dir, permit, extension, checkpoint=checkpoint)
            if state == 'publishing':
                advance('importing')
            # The ingest service owns it from here; the next claim only
            # reconciles, so there is nothing to gain from a fast re-poll.
            repo.release(job.id, token,
                delay_seconds=self.import_recheck_seconds if state == 'importing' else 5)
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
        # Terminal without a live attempt directory of its own still has to
        # release whatever earlier attempts left behind.
        if current.state in ('imported', 'failed', 'cancelled'):
            self._cleanup(job.id)
        return current

    def _client_material(self, offer):
        clients = [row for row in self.repository.list_connections() if row.id == offer.get('client_id')
            and row.adapter in (USENET_KINDS if offer['transport'] == 'nzb' else TORRENT_KINDS) and row.revision == offer.get('client_revision')]
        if not clients:
            raise ClientError('download_client_unavailable')
        material = self.repository.connection_config(clients[0].id)
        if material.revision != clients[0].revision:
            raise ClientError('download_client_unavailable')
        return clients[0], material.config

    def _download_client(self, job, token, offer, config, source, checkpoint):
        repo = self.repository
        client_row, client_config = self._client_material(offer)
        client = (self.client_factory or CLIENTS[client_row.adapter])(client_config, transfer=self.transfer)
        direct_magnet = offer['transport'] == 'torrent' and offer['href'].startswith('magnet:')
        if direct_magnet:
            # The private durable offer, never a refetched mutable source or a
            # shortened external ID, carries both original full hashes on every
            # poll/restart/shared-attempt adoption, even after an uncertain add.
            hashes = magnet_identities(offer['href'], tracker_origins=config.get('tracker_origins', []), secret=config['secret'])
            prepare = getattr(client, 'prepare_submission', None)
            if callable(prepare):
                prepare(offer['href'], checkpoint=checkpoint)
            elif hashes.v2 is not None:
                raise ClientError('unsupported_client_version')
        def submission_name(key):
            identity = [offer['release_key'], client_row.id, client_row.revision]
            # Nullable upgrade preserves existing remote names. New attempts
            # have their own durable key, including a retry after definite failure.
            if key:
                identity.append(key)
            return 'cwng-' + repo.box.display_identity(str(identity))
        external_id, started, key = repo.submission_identity(job.id, token)
        if started is None:
            repo.begin_submission(job.id, token, create_if_missing=False)
            external_id, started, key = repo.submission_identity(job.id, token)
        if started is None:
            # Fetch/validate before issuing the durable POST fence. A bad key or
            # descriptor here is safely retryable without an uncertain submit.
            if direct_magnet:
                descriptor = offer['href']
            else:
                document = self.transfer(offer['href'], replace(policy(config), query_secrets=(config['secret'],) if config['secret'] else (),
                    allow_magnet_redirect=offer['transport'] == 'torrent'), max_bytes=512 * 1024, checkpoint=checkpoint)
                descriptor = document.url if document.url.startswith('magnet:') else document.body
                if offer['transport'] == 'nzb': validate_nzb(descriptor)
                elif isinstance(descriptor, str):
                    hashes = magnet_identities(descriptor, tracker_origins=config.get('tracker_origins', []), secret=config['secret'])
                    # No durable original full-topic/pair authority exists for a mutable
                    # HTTP redirect. Preserve the established v1 redirect flow.
                    if hashes.v2 is not None:
                        raise ClientError('unsupported_magnet_redirect')
                else: validate_torrent(descriptor, tracker_origins=config.get('tracker_origins', []), secret=config['secret'])
            # A read-only client prerequisite must not fence a POST that was
            # never attempted. qBittorrent resolves hybrid engine identity here.
            prepare = getattr(client, 'prepare_submission', None)
            if callable(prepare):
                prepare(descriptor, checkpoint=checkpoint)
            fenced_submit = getattr(client, 'submit_fenced', None)
            fresh = False
            def before_submit():
                nonlocal fresh
                checkpoint()
                fresh = repo.begin_submission(job.id, token)
                _, _, attempt_key = repo.submission_identity(job.id, token)
                return submission_name(attempt_key) if fresh else None
            try:
                if callable(fenced_submit):
                    # Native torrent adapters perform their read-only collision
                    # check first, then invoke this callback immediately before
                    # the add operation. An expired preflight never issues a
                    # durable attempt; accepted/uncertain POSTs remain fenced.
                    external_id = fenced_submit(descriptor, before_submit, checkpoint=checkpoint)
                else:
                    name = before_submit()
                    if fresh:
                        external_id = client.submit(name, descriptor, checkpoint=checkpoint)
            except TransportError as error:
                if fresh and error.code in ('needs_auth', 'client_error', 'torrent_already_exists'):
                    repo.clear_rejected_submission(job.id, token, error_code=error.code)
                raise
            if fresh:
                repo.record_external(job.id, token, external_id)
            external_id, started, key = repo.submission_identity(job.id, token)
        checkpoint()
        remote = client.find(submission_name(key), external_id, checkpoint=checkpoint)
        if remote is None:
            raise ClientError('client_job_missing' if external_id else 'submission_ambiguous')
        if not external_id:
            repo.record_external(job.id, token, remote.get('nzo_id'))
        if remote.get('status') == 'Failed':
            repo.fail_submission_adopters(job.id, token)
            raise ClientError('client_job_failed')
        if remote.get('status') != 'Completed' or remote.get('loaded') not in (False, None):
            if repo.submission_age(job.id, token) >= self.download_deadline_seconds:
                raise ClientError('client_job_stalled')
            return None
        try:
            selected = repo.selected_artifact(job.id, token)
            books = (torrent_books(client_config, remote.get('directory'), remote.get('files'), max_bytes=self.max_bytes)
                if offer['transport'] == 'torrent' else completed_books(client_config, remote.get('storage'), max_bytes=self.max_bytes))
            if not books and selected is None:
                raise ClientError('no_usable_book')
            if selected is None and len(books) > 1:
                manifest = fingerprint_candidates(client_config, books, repo.box, job.id, checkpoint, max_bytes=self.max_bytes)
                checkpoint()
                repo.await_choices(job.id, token, manifest)
                return None
            return self._copy_client_book(client_config, remote, source, checkpoint,
                torrent=offer['transport'] == 'torrent', books=books, selected=selected)
        except FileNotFoundError:
            if repo.selected_artifact(job.id, token) is not None:
                raise ClientError('artifact_unavailable') from None
            if offer['transport'] != 'torrent': raise
            # Download completion can precede the final directory move, in
            # either client. Preserve the owned submission and poll again.
            if repo.submission_age(job.id, token) >= self.download_deadline_seconds:
                raise ClientError('client_job_stalled')
            return None

    def _copy_client_book(self, client_config, remote, source, checkpoint, *, torrent, books=None, selected=None):
        if selected is not None:
            root = _safe_root(client_config)
            found = next(((p,m) for p,m in books if p.relative_to(root).as_posix() == selected['relative_path']), None)
            if found is None:
                raise ClientError('artifact_unavailable')
            book, media = found
            if media != selected['media_type'] or book.stat().st_size != selected['size']:
                raise ClientError('artifact_changed')
        elif books is not None:
            book, media = books[0]
        elif torrent:
            book, media = torrent_book(client_config, remote.get('directory'), remote.get('files'), max_bytes=self.max_bytes)
        else:
            book, media = completed_book(client_config, remote.get('storage'), max_bytes=self.max_bytes)
        if not self.media_allowed(media):
            raise ClientError('format_not_allowed')
        # O_NOFOLLOW + inode/stat comparison fences a completed file changing
        # while it is copied. Only private staging is ever written by CWNG.
        before = book.stat()
        fd = open_completed_file(client_config, book)
        try:
            with os.fdopen(fd, 'rb') as input_file, source.open('xb') as output:
                opened = os.fstat(input_file.fileno())
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise ClientError('completed_file_changed')
                count = 0
                for chunk in iter(lambda: input_file.read(256 * 1024), b''):
                    checkpoint()
                    count += len(chunk)
                    if count > self.max_bytes:
                        raise ClientError('file_too_large')
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
                after = os.fstat(input_file.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ClientError('completed_file_changed')
        except BaseException:
            source.unlink(missing_ok=True)
            raise
        try:
            if selected is not None and digest(source) != selected['sha256']:
                raise ClientError('artifact_changed')
            validate_book(source, media, max_bytes=self.max_bytes)
        except StagingError:
            raise ClientError('no_usable_book') from None
        return media

    def _reconcile_import(self, claim):
        """Read what the ingest service did with a published book.

        A job in `importing` is never published again and never re-downloaded:
        the previous behaviour fell through the whole publication path on every
        claim, re-hashing the source and recreating a book the processor had
        already taken away, every few seconds, forever.
        """
        repo, job, token = self.repository, claim.job, claim.token
        try:
            staging_key = repo.staged_identity(job.id, token)[1]
            if not staging_key:
                raise StagingError('publication_token_missing')
            book, sidecar, marker = publication_state(self.ingest_dir, staging_key)
            if book is None:
                # The safety timeout copies the book to the failed folder and
                # removes it. Recreating it here is what filled that folder.
                reason = 'import_failed'
            elif marker is not None:
                reason = 'import_failed'   # terminal processor result, source retained
            else:
                waited = repo.importing_watch(job.id, token)
                reason = 'import_failed' if waited is not None and waited >= self.import_deadline_seconds else None
            if reason is None:
                repo.release(job.id, token, delay_seconds=self.import_recheck_seconds)
            else:
                # app.db first: dropping the capability makes a late receipt
                # lose cleanly, so no receipt can name bytes now being deleted.
                repo.advance(job.id, token, 'importing', 'failed',
                             error_code=reason, abandon_publication=True)
                discard_publication(self.ingest_dir, staging_key)
        except Conflict:
            # A receipt won, or the lease expired. The winner stays authoritative.
            pass
        except (StagingError, OSError, ValueError):
            try:
                repo.advance(job.id, token, 'importing', 'failed', error_code='import_failed',
                             abandon_publication=True)
            except Conflict:
                pass
        current = repo.get_job(job.owner_id, job.id)
        if current.state in ('imported', 'failed', 'cancelled'):
            self._cleanup(job.id)
        return current

    def _cleanup(self, job_id):
        cleanup_settled(self.repository, self.staging_dir, job_id)
