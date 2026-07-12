"""#832 — exact Kobo discovery/removal protocol contract.

The device first discovers ``Resources.library_sync`` from initialization, then
round-trips the sync token through that endpoint.  These tests pin both links
and the hard-delete entity used by fully-local and stock-store-proxy modes.
"""

from datetime import datetime, timezone
import inspect
from types import SimpleNamespace

import pytest
from flask import Flask

from cps.kobo import (
    generate_sync_response,
    HandleInitRequest,
    HandleSyncRequest,
    create_deleted_book_entitlement,
    create_deleted_book_metadata,
)


pytestmark = pytest.mark.unit


def test_initialization_discovers_local_library_sync_before_removal():
    """Protocol link 1: both init branches replace Kobo's store URL with the
    authenticated CWNG sync handler the device must call next."""
    source = inspect.getsource(HandleInitRequest)
    assert 'kobo_resources["library_sync"] = calibre_web_url + url_for(' in source
    assert 'kobo_resources["library_sync"] = url_for(' in source
    assert "HandleSyncRequest" in source


def test_discovered_sync_handler_emits_changed_not_deleted_entitlement():
    """Protocol link 2: the discovered handler consumes retained tombstones
    and emits only the archive-shaped message, in both proxy modes because the
    block is outside the store-proxy branch."""
    source = inspect.getsource(HandleSyncRequest)
    tombstone_block = source[source.index("pending_deletions"):source.index(
        "# If there are MORE pending deletions")]
    assert '"ChangedEntitlement"' in tombstone_block
    assert "create_deleted_book_entitlement" in tombstone_block
    assert "create_deleted_book_metadata" in tombstone_block
    assert '"DeletedEntitlement"' not in tombstone_block
    assert "config_kobo_proxy" not in tombstone_block


def test_side_loaded_hard_delete_payload_is_removed_hidden_and_not_downloadable():
    deleted_at = datetime(2026, 7, 12, 14, 30, tzinfo=timezone.utc)
    entitlement = create_deleted_book_entitlement("uuid-old-copy", deleted_at)
    metadata = create_deleted_book_metadata("uuid-old-copy")

    assert entitlement["Id"] == "uuid-old-copy"
    assert entitlement["RevisionId"] == "uuid-old-copy"
    assert entitlement["CrossRevisionId"] == "uuid-old-copy"
    assert entitlement["IsRemoved"] is True
    assert entitlement["IsHiddenFromArchive"] is True
    assert entitlement["OriginCategory"] == "Imported"
    assert entitlement["Status"] == "Active"
    assert metadata["EntitlementId"] == "uuid-old-copy"
    assert metadata["RevisionId"] == "uuid-old-copy"
    assert metadata["DownloadUrls"] == []


def test_stock_store_proxy_results_remain_after_local_removal(monkeypatch):
    """Stock-Kobo invariant: local results are extended with store results,
    never replaced, and only after local continuation has drained."""
    handler_source = inspect.getsource(HandleSyncRequest)
    response_source = inspect.getsource(generate_sync_response)
    assert handler_source.index("pending_deletions") < handler_source.index(
        "return generate_sync_response")
    assert "if config.config_kobo_proxy and not set_cont" in response_source
    assert "sync_results += store_sync_results" in response_source

    class Token:
        merged = False

        def merge_from_store_response(self, _response):
            self.merged = True

        def to_headers(self, headers):
            headers["x-kobo-synctoken"] = "round-tripped-wrapper"

    store_response = SimpleNamespace(
        json=lambda: [{"NewEntitlement": {"source": "official-store"}}],
        headers={"x-kobo-sync-mode": "store-mode"},
    )
    from cps import kobo as kobo_module
    monkeypatch.setattr(kobo_module.config, "config_kobo_proxy", True, raising=False)
    monkeypatch.setattr("cps.kobo.make_request_to_kobo_store",
                        lambda _token: store_response)
    monkeypatch.setattr("scripts.cwa_db.CWA_DB", lambda: SimpleNamespace(
        log_activity=lambda **_kwargs: None))
    local = [{"ChangedEntitlement": {"source": "local-hard-delete"}}]
    token = Token()
    app = Flask(__name__)
    with app.test_request_context("/"):
        response = generate_sync_response(token, local)

    assert response.get_json() == [
        {"ChangedEntitlement": {"source": "local-hard-delete"}},
        {"NewEntitlement": {"source": "official-store"}},
    ]
    assert token.merged is True
    assert response.headers["x-kobo-synctoken"] == "round-tripped-wrapper"
    assert response.headers["x-kobo-sync-mode"] == "store-mode"


def test_local_continuation_does_not_contact_official_store(monkeypatch):
    class Token:
        def to_headers(self, headers):
            headers["x-kobo-synctoken"] = "local-page-token"

    called = []
    from cps import kobo as kobo_module
    monkeypatch.setattr(kobo_module.config, "config_kobo_proxy", True, raising=False)
    monkeypatch.setattr("cps.kobo.make_request_to_kobo_store",
                        lambda _token: called.append(True))
    monkeypatch.setattr("scripts.cwa_db.CWA_DB", lambda: SimpleNamespace(
        log_activity=lambda **_kwargs: None))
    app = Flask(__name__)
    with app.test_request_context("/"):
        response = generate_sync_response(Token(), [
            {"ChangedEntitlement": {"source": "local-hard-delete"}}
        ], set_cont=True)
    assert called == []
    assert response.headers["x-kobo-sync"] == "continue"
