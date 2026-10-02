# SPDX-License-Identifier: GPL-3.0-or-later
"""Real SQLite transactions for unregistered acquisition persistence.

The injected finalizer's test effect is not a substitute user/membership model.
Runtime integration must bind the same callback to real Calibre IDs and ub.
"""
import importlib
import importlib.util
import secrets
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import MetaData, create_engine, event, select, text
from sqlalchemy.exc import IntegrityError

_package_path = Path(__file__).resolve().parents[2] / "cps/services/acquisition"
_spec = importlib.util.spec_from_file_location("_cwng_acquisition_storage_tests",
    _package_path / "__init__.py", submodule_search_locations=[str(_package_path)])
_package = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _package
_spec.loader.exec_module(_package)
s = importlib.import_module(_spec.name + ".storage")
k = importlib.import_module(_spec.name + ".secrets")


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "app.db"
    engine = create_engine("sqlite:///" + str(path), connect_args={"timeout": 10})
    @event.listens_for(engine, "connect")
    def sqlite_options(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
    metadata = MetaData()
    tables = s.define_tables(metadata)
    metadata.create_all(engine)
    now = [1000.0]
    box = k.SecretBox(b"x" * 32)
    repo = s.Repository(engine, tables, box, clock=lambda: now[0])
    yield repo, tables, now, path
    engine.dispose()


def connection_offer(repo, owner=1):
    connection = repo.create_connection("Books", "opds", {
        "endpoint": "https://catalog.example/?key=CATALOG_SECRET",
        "credential": "PRIVATE_CREDENTIAL"}, enabled=True)
    offer = repo.create_offer(owner, connection.id, {
        "href": "https://files.example/book.epub?token=DOWNLOAD_SECRET", "title": "Book"})
    return connection, offer


def importing(repo, offer, owner=1):
    job = repo.create_job(owner, offer, "request", requires_approval=False)
    claim = repo.claim()
    assert claim.job.id == job.id
    repo.advance(job.id, claim.token, "queued", "resolving")
    repo.advance(job.id, claim.token, "resolving", "downloading")
    repo.advance(job.id, claim.token, "downloading", "staged", source_sha256="a" * 64, staging_key="owned_file")
    publication = repo.prepare_publication(job.id, claim.token, secrets.token_urlsafe(32))
    repo.advance(job.id, claim.token, "publishing", "importing")
    return job, claim, publication


def test_first_boot_key_creation_is_atomic_and_existing_corruption_is_not_replaced(tmp_path):
    path = tmp_path / "key"
    barrier = Barrier(4)
    def load():
        barrier.wait()
        return k.load_or_create_key(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = list(pool.map(lambda _: load(), range(4)))
    assert len(set(keys)) == 1 and len(keys[0]) == 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    valid = path.read_bytes().strip()
    corrupt = valid + b" " * 212 + b"CORRUPT-TRAILER"
    path.write_bytes(corrupt)
    with pytest.raises(k.SecretError):
        k.load_or_create_key(path)
    assert path.read_bytes() == corrupt
    path.write_bytes(b"broken-key")
    with pytest.raises(k.SecretError):
        k.load_or_create_key(path)
    assert path.read_bytes() == b"broken-key"
    assert not list(tmp_path.glob(".acquisition-key-*"))


def test_key_symlink_is_not_followed_or_overwritten(tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"private-existing-data")
    link = tmp_path / "key"
    link.symlink_to(original)
    with pytest.raises(k.SecretError):
        k.load_or_create_key(link)
    assert original.read_bytes() == b"private-existing-data" and link.is_symlink()


@pytest.mark.parametrize("binding", [
    {"scope": "user:2", "identity": "connection-a", "field_name": "key"},
    {"scope": "user:1", "identity": "connection-b", "field_name": "key"},
    {"scope": "user:1", "identity": "connection-a", "field_name": "offer"},
])
def test_ciphertext_cannot_be_rebound_to_another_account_connection_or_field(binding):
    box = k.SecretBox(b"x" * 32)
    sealed = box.seal("PRIVATE", scope="user:1", identity="connection-a", field_name="key")
    assert "PRIVATE" not in repr(sealed)
    with pytest.raises(k.SecretError):
        box.open(sealed, **binding)


def test_connections_and_offers_persist_only_encrypted_private_material(store):
    repo, tables, _, path = store
    connection, offer = connection_offer(repo)
    job = repo.create_job(1, offer, "request", requires_approval=False)
    claim = repo.claim()
    material = repo.material(job.id, claim.token)
    assert material.config["credential"] == "PRIVATE_CREDENTIAL"
    assert material.offer["href"].endswith("DOWNLOAD_SECRET")
    for secret in ("PRIVATE_CREDENTIAL", "CATALOG_SECRET", "DOWNLOAD_SECRET"):
        assert secret not in repr((connection, job, claim, material))
        assert secret.encode() not in path.read_bytes()
    with repo.engine.connect() as conn:
        row = conn.execute(select(tables.offers)).mappings().one()
    # Moving the encrypted row to another owner breaks AAD even if a caller
    # somehow bypassed the repository's ownership check.
    with pytest.raises(k.SecretError):
        repo.box.open(k.SealedValue(row["payload_ciphertext"], row["payload_nonce"]),
            scope=f"user:2:connection:{connection.id}:revision:1", identity=offer, field_name="offer")


def test_owner_scoping_and_approval_precede_claimable_work(store):
    repo, _, _, _ = store
    _, offer = connection_offer(repo)
    with pytest.raises(s.NotFound):
        repo.create_job(2, offer, "stolen")
    job = repo.create_job(1, offer, "request")
    assert repo.claim() is None
    for operation in (lambda: repo.get_job(2, job.id), lambda: repo.request_cancel(2, job.id), lambda: repo.retry(2, job.id)):
        with pytest.raises(s.NotFound):
            operation()
    assert repo.list_jobs(2) == ()
    repo.approve(job.id, admin_actor=99)
    claim = repo.claim()
    assert claim.job.id == job.id and claim.job.state == "queued"
    with pytest.raises(s.Conflict):
        repo.approve(job.id, admin_actor=99)


def test_concurrent_duplicate_clicks_persist_one_intent_and_different_payload_conflicts(store):
    repo, tables, _, _ = store
    connection, offer = connection_offer(repo)
    barrier = Barrier(4)
    def submit():
        barrier.wait()
        return repo.create_job(1, offer, "same-request", requires_approval=False)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: submit(), range(4)))
    assert len({job.id for job in jobs}) == 1
    with repo.engine.connect() as conn:
        assert len(conn.execute(select(tables.jobs)).all()) == 1
    other = repo.create_offer(1, connection.id, {"href": "https://files.example/other.epub"})
    with pytest.raises(s.Conflict):
        repo.create_job(1, other, "same-request")
    with pytest.raises(s.Conflict):
        repo.create_job(1, offer, "same-request", add_to_my_library=False)
    second_user_offer = repo.create_offer(2, connection.id, {"href": "https://files.example/other.epub"})
    assert repo.create_job(2, second_user_offer, "same-request").id != jobs[0].id


def test_concurrent_claims_have_one_winner_and_expired_tokens_cannot_act(store):
    repo, _, now, _ = store
    _, offer = connection_offer(repo)
    job = repo.create_job(1, offer, "request", requires_approval=False)
    barrier = Barrier(4)
    def claim():
        barrier.wait()
        return repo.claim(lease_seconds=10)
    with ThreadPoolExecutor(max_workers=4) as pool:
        winners = [result for result in pool.map(lambda _: claim(), range(4)) if result is not None]
    assert len(winners) == 1
    first = winners[0]
    now[0] += 11
    second = repo.claim()
    assert second.job.id == job.id and second.token != first.token
    assert second.job.claim_count == 2
    for operation in (lambda: repo.heartbeat(job.id, first.token), lambda: repo.material(job.id, first.token),
                      lambda: repo.advance(job.id, first.token, "queued", "resolving"), lambda: repo.release(job.id, first.token)):
        with pytest.raises(s.Conflict):
            operation()
    repo.advance(job.id, second.token, "queued", "resolving")
    assert repo.get_job(1, job.id).state == "resolving"


def test_cancel_fences_active_worker_and_cannot_relabel_an_import_as_cancelled(store):
    repo, _, _, _ = store
    _, offer = connection_offer(repo)
    job = repo.create_job(1, offer, "request", requires_approval=False)
    claim = repo.claim()
    repo.request_cancel(1, job.id)
    with pytest.raises(s.Conflict):
        repo.advance(job.id, claim.token, "queued", "resolving")
    with pytest.raises(s.Conflict):
        repo.material(job.id, claim.token)
    repo.advance(job.id, claim.token, "queued", "cancelled")
    assert repo.get_job(1, job.id).state == "cancelled"
    with pytest.raises(s.Conflict):
        repo.retry(1, job.id)


def test_restart_rehydrates_due_work_without_any_network_effect(store):
    repo, tables, now, path = store
    _, offer = connection_offer(repo)
    job = repo.create_job(1, offer, "request", requires_approval=False)
    claim = repo.claim()
    repo.release(job.id, claim.token, delay_seconds=30)
    repo.engine.dispose()
    restarted_engine = create_engine("sqlite:///" + str(path))
    restarted = s.Repository(restarted_engine, tables, k.SecretBox(b"x" * 32), clock=lambda: now[0])
    try:
        assert restarted.claim() is None
        now[0] += 31
        assert restarted.claim().job.id == job.id
    finally:
        restarted_engine.dispose()


def test_failed_receipt_write_rolls_back_finalizer_effect_and_is_recoverable(store):
    repo, tables, _, _ = store
    _, offer = connection_offer(repo)
    job, claim, publication = importing(repo, offer)
    # This is a generic transaction effect, deliberately not a fake user table.
    with repo.engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE finalizer_effect (job_id TEXT PRIMARY KEY, book_id INTEGER)")
        conn.exec_driver_sql("CREATE TRIGGER reject_receipt BEFORE INSERT ON acquisition_import_receipt BEGIN SELECT RAISE(ABORT, 'receipt unavailable'); END")
    calls = []
    def finalizer(conn, finalized_job, outcome):
        assert outcome.book_ids == (42,), "Integration callback validates authoritative result IDs"
        calls.append(finalized_job.id)
        conn.execute(text("INSERT INTO finalizer_effect VALUES (:job, :book)"), {"job": finalized_job.id, "book": outcome.book_ids[0]})
    outcome = s.ImportOutcome("a" * 64, "b" * 64, (42,))
    with pytest.raises(IntegrityError):
        repo.finalize_import(job.id, publication.token, outcome, staging_key=publication.staging_key, finalize_membership=finalizer)
    assert repo.get_job(1, job.id).state == "importing"
    with repo.engine.begin() as conn:
        assert conn.execute(select(tables.receipts)).all() == []
        assert conn.execute(text("SELECT * FROM finalizer_effect")).all() == []
        conn.exec_driver_sql("DROP TRIGGER reject_receipt")
    repo.finalize_import(job.id, publication.token, outcome, staging_key=publication.staging_key, finalize_membership=finalizer)
    repo.finalize_import(job.id, publication.token, outcome, staging_key=publication.staging_key, finalize_membership=finalizer)
    assert calls == [job.id, job.id]  # failed transaction and exactly one committed effect
    assert repo.get_job(1, job.id).state == "imported"
    with repo.engine.connect() as conn:
        assert len(conn.execute(select(tables.receipts)).all()) == 1
        assert conn.execute(text("SELECT book_id FROM finalizer_effect")).scalar_one() == 42
    with pytest.raises(s.Conflict):
        repo.request_cancel(1, job.id)


def test_import_receipt_requires_matching_digest_exact_ids_and_callback_acceptance(store):
    repo, tables, _, _ = store
    _, offer = connection_offer(repo)
    job, claim, publication = importing(repo, offer)
    with pytest.raises(s.StorageError):
        s.ImportOutcome("a" * 64, "b" * 64, (0,))
    with pytest.raises(s.StorageError):
        s.ImportOutcome("a" * 64, "b" * 64, ())
    with pytest.raises(s.Conflict):
        repo.finalize_import(job.id, publication.token, s.ImportOutcome("c" * 64, "b" * 64, (42,)), staging_key=publication.staging_key, finalize_membership=lambda *args: None)
    def reject_unknown_books(conn, job, outcome):
        raise ValueError("Book ID was not verified against the actual Calibre result")
    with pytest.raises(ValueError, match="not verified"):
        repo.finalize_import(job.id, publication.token, s.ImportOutcome("a" * 64, "b" * 64, (42,)), staging_key=publication.staging_key, finalize_membership=reject_unknown_books)
    assert repo.get_job(1, job.id).state == "importing"
    with repo.engine.connect() as conn:
        assert conn.execute(select(tables.receipts)).all() == []


def test_disabled_connection_and_expired_offer_do_not_create_new_work(store):
    repo, _, now, _ = store
    connection, offer = connection_offer(repo)
    now[0] += 901
    with pytest.raises(s.NotFound):
        repo.create_job(1, offer, "expired")
    offer = repo.create_offer(1, connection.id, {"href": "https://files.example/book.epub"})
    job = repo.create_job(1, offer, "request", requires_approval=False)
    repo.set_connection_enabled(connection.id, False)
    assert repo.claim() is None
    with pytest.raises(s.NotFound):
        repo.create_job(1, offer, "new-request", requires_approval=False)
    assert repo.get_job(1, job.id).state == "queued"


def test_legitimate_import_receipt_survives_worker_lease_expiry_and_reclaim(store):
    repo, _, now, _ = store
    _, offer = connection_offer(repo)
    job, claim, publication = importing(repo, offer)
    now[0] += 61
    replacement = repo.claim()
    assert replacement.job.id == job.id and replacement.token != claim.token
    repo.advance(job.id, replacement.token, "importing", "failed", error_code="receipt_delayed")
    outcome = s.ImportOutcome("a" * 64, "b" * 64, (42,))
    calls = []
    repo.finalize_import(job.id, publication.token, outcome, staging_key=publication.staging_key, finalize_membership=lambda *args: calls.append("done"))
    assert repo.get_job(1, job.id).state == "imported" and calls == ["done"]
    assert repo.get_job(1, job.id).error_code is None
    with pytest.raises(s.Conflict):
        repo.advance(job.id, replacement.token, "importing", "failed", error_code="late_worker")


def test_publication_capability_is_bound_to_intent_and_cannot_be_rotated_on_retry(store):
    repo, tables, now, path = store
    _, offer = connection_offer(repo)
    job, claim, publication = importing(repo, offer)
    wrong = secrets.token_urlsafe(32)
    with pytest.raises(s.Conflict):
        repo.prepare_publication(job.id, claim.token, wrong)
    owner_job, expected = repo.publication_intent(job.id, publication.token, publication.staging_key)
    assert owner_job.owner_id == 1 and expected.source_sha256 == "a" * 64
    assert publication.token not in repr(publication)
    assert publication.token.encode() not in path.read_bytes()
    for token, staging in ((wrong, publication.staging_key), (publication.token, "other_file")):
        with pytest.raises(s.NotFound):
            repo.publication_intent(job.id, token, staging)
        with pytest.raises(s.Conflict):
            repo.finalize_import(job.id, token, s.ImportOutcome("a" * 64, "b" * 64, (42,)),
                staging_key=staging, finalize_membership=lambda *args: pytest.fail("Untrusted receipt reached finalizer"))
    repo.advance(job.id, claim.token, "importing", "failed", error_code="receipt_delayed")
    repo.retry(1, job.id)
    assert repo.get_job(1, job.id).state == "publishing"
    retry_claim = repo.claim()
    same = repo.prepare_publication(job.id, retry_claim.token, publication.token)
    assert same == publication
    with pytest.raises(s.Conflict):
        repo.prepare_publication(job.id, retry_claim.token, wrong)


def test_concurrent_receipt_replays_commit_finalizer_once_and_remain_owner_scoped(store):
    repo, _, _, _ = store
    _, offer = connection_offer(repo)
    job, claim, publication = importing(repo, offer)
    barrier = Barrier(2)
    outcome = s.ImportOutcome("a" * 64, "b" * 64, (42,))
    calls = []
    def acknowledge():
        barrier.wait()
        return repo.finalize_import(job.id, publication.token, outcome, staging_key=publication.staging_key,
                                    finalize_membership=lambda *args: calls.append("committed"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: acknowledge(), range(2))) == [outcome, outcome]
    assert calls == ["committed"]
    assert repo.get_receipt(1, job.id) == outcome
    with pytest.raises(s.NotFound):
        repo.get_receipt(2, job.id)
    with pytest.raises(s.Conflict):
        repo.finalize_import(job.id, publication.token, s.ImportOutcome("a" * 64, "b" * 64, (43,)),
            staging_key=publication.staging_key, finalize_membership=lambda *args: pytest.fail("Receipt changed"))


def test_secret_error_boundary_suppresses_sensitive_dependency_exception_chain(monkeypatch):
    import traceback
    secret = "sensitive" + "-dependency-value"
    box = k.SecretBox(b"x" * 32)
    class RejectCipher:
        def decrypt(self, *args):
            raise ValueError(secret)
    monkeypatch.setattr(box, "_cipher", RejectCipher())
    try:
        box.open(k.SealedValue(b"ciphertext", b"n" * 12),
                 scope="user:1", identity="record", field_name="key")
    except k.SecretError:
        assert secret not in traceback.format_exc()
    else:
        pytest.fail("Secret boundary did not normalize dependency failure")


def test_missing_key_for_existing_encrypted_database_does_not_create_replacement(tmp_path):
    path = tmp_path / "configuration" / "key"
    with pytest.raises(k.SecretError):
        k.load_or_create_key(path, allow_create=False)
    assert not path.exists() and not path.parent.exists()
    original = k.load_or_create_key(path)
    assert k.load_or_create_key(path, allow_create=False) == original


def test_global_worker_limit_allows_only_one_live_transfer_across_jobs(store):
    repo, _, now, _ = store
    _, offer = connection_offer(repo)
    first = repo.create_job(1, offer, 'first', requires_approval=False)
    second = repo.create_job(1, offer, 'second', requires_approval=False)
    barrier = Barrier(4)
    def claim():
        barrier.wait()
        return repo.claim(max_active=1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: claim(), range(4)))
    winners = [value for value in results if value is not None]
    assert len(winners) == 1
    assert repo.claim(max_active=1) is None
    now[0] += 61
    replacement = repo.claim(max_active=1)
    assert replacement is not None and replacement.token != winners[0].token


def test_expired_selection_cleanup_is_bounded_owner_scoped_and_preserves_job_material(store):
    repo, tables, now, _ = store
    connection, protected = connection_offer(repo)
    job = repo.create_job(1, protected, 'durable-intent', requires_approval=False)
    removable = [repo.create_offer(1, connection.id, {'page': i}) for i in range(5)]
    other_owner = repo.create_offer(2, connection.id, {'page': 'private'})
    now[0] += 901
    assert repo.cleanup_expired_offers(1, limit=2) == 2
    with repo.engine.connect() as conn:
        remaining = set(conn.execute(select(tables.offers.c.id)).scalars())
    assert protected in remaining and other_owner in remaining
    assert len(remaining.intersection(removable)) == 3
    assert repo.cleanup_expired_offers(1, limit=2) == 2
    assert repo.cleanup_expired_offers(1, limit=2) == 1
    assert repo.cleanup_expired_offers(1, limit=2) == 0
    assert repo.create_job(1, protected, 'durable-intent').id == job.id
    claim = repo.claim()
    assert repo.material(job.id, claim.token).offer['title'] == 'Book'
    assert repo.get_job(1, job.id).id == job.id


@pytest.mark.parametrize('journal_mode', ['delete', 'wal'])
def test_owner_selection_cap_is_atomic_across_connections_and_recovers_on_expiry(store, monkeypatch, journal_mode):
    repo, tables, now, _ = store
    with repo.engine.connect() as conn:
        assert conn.exec_driver_sql('PRAGMA journal_mode=' + journal_mode).scalar_one() == journal_mode
    monkeypatch.setattr(s, 'MAX_ACTIVE_OFFERS_PER_OWNER', 2, raising=False)
    first = repo.create_connection('First', 'opds', {}, enabled=True)
    second = repo.create_connection('Second', 'opds', {}, enabled=True)
    repo.create_offer(1, first.id, {'page': 'existing'}, lifetime=10)
    barrier = Barrier(4)
    def add(index):
        barrier.wait()
        try:
            return repo.create_offer(1, (first, second)[index % 2].id, {'page': index}, lifetime=10)
        except s.SelectionLimit as error:
            assert error.code == 'selections_full'
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        created = list(pool.map(add, range(4)))
    assert len([value for value in created if value is not None]) == 1
    assert repo.create_offer(2, second.id, {'page': 'another-owner'})
    with repo.engine.connect() as conn:
        assert len(conn.execute(select(tables.offers.c.id).where(tables.offers.c.owner_id == 1)).all()) == 2
    now[0] += 11
    replacement = repo.create_offer(1, second.id, {'page': 'fresh'})
    with repo.engine.connect() as conn:
        assert set(conn.execute(select(tables.offers.c.id).where(tables.offers.c.owner_id == 1)).scalars()) == {replacement}


def test_busy_delay_fences_connection_siblings_new_jobs_and_cancelled_retry(store):
    repo, tables, now, _ = store
    connection, offer = connection_offer(repo)
    first = repo.create_job(1, offer, 'first', requires_approval=False)
    claim = repo.claim()
    repo.advance(first.id, claim.token, 'queued', 'failed', error_code='source_busy', retry_delay_seconds=120)
    repo.retry(1, first.id)
    repo.request_cancel(1, first.id)
    sibling = repo.create_job(1, offer, 'sibling', requires_approval=False)
    other_connection, other_offer = connection_offer(repo)
    other = repo.create_job(1, other_offer, 'other', requires_approval=False)
    assert repo.claim(lease_seconds=3600).job.id == other.id
    assert repo.claim() is None
    now[0] += 119.99
    assert repo.claim() is None
    now[0] += .01
    assert repo.claim().job.id == sibling.id


def test_stale_busy_failure_cannot_delay_replacement_claim(store):
    repo, _, now, _ = store
    _, offer = connection_offer(repo)
    job = repo.create_job(1, offer, 'request', requires_approval=False)
    first = repo.claim(lease_seconds=1)
    now[0] += 2
    second = repo.claim()
    with pytest.raises(s.Conflict):
        repo.advance(job.id, first.token, 'queued', 'failed', error_code='source_busy', retry_delay_seconds=120)
    repo.advance(job.id, second.token, 'queued', 'resolving')
    assert repo.get_job(1, job.id).state == 'resolving'
