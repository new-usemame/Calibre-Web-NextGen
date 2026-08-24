# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Kobo annotation containment at the checkforchanges trigger boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, make_response

import cps.readingservices as rs


OWNED = "9e5251ad-d530-4e58-9121-8b8336099fdd"
FOREIGN = "kobo-store-content"


@pytest.fixture
def app():
    return Flask(__name__)


def _request_entries(*content_ids):
    return [{"ContentId": content_id, "etag": f"etag-{content_id}"}
            for content_id in content_ids]


def _view(function):
    # Strip the auth/config decorator; functools.wraps exposes the original.
    return function.__wrapped__


def test_owned_id_is_stripped_from_request():
    entries = _request_entries(OWNED)
    assert rs._filter_check_for_changes_entries(entries, {OWNED}) == []


def test_not_owned_id_survives_request_filter():
    entries = _request_entries(FOREIGN)
    assert rs._filter_check_for_changes_entries(entries, set()) == entries


def test_mixed_request_keeps_only_not_owned_id():
    entries = _request_entries(OWNED, FOREIGN)
    assert rs._filter_check_for_changes_entries(entries, {OWNED}) == [entries[1]]


def test_unparseable_request_body_is_not_recognized():
    assert rs._parse_check_for_changes_request(b"not-json") is None
    assert rs._parse_check_for_changes_request(json.dumps({"ContentId": OWNED})) is None
    assert rs._parse_check_for_changes_request(json.dumps([{"etag": "missing-id"}])) is None


def test_unrecognized_response_shape_is_not_recognized():
    assert rs._check_for_changes_response_content_ids({"ContentId": OWNED}) is None
    assert rs._check_for_changes_response_content_ids([{"unexpected": OWNED}]) is None


def test_response_filter_handles_bare_ids_and_content_id_objects():
    entries = [OWNED, FOREIGN, {"ContentId": OWNED}, {"ContentId": FOREIGN}]
    assert rs._check_for_changes_response_content_ids(entries) == [
        OWNED, FOREIGN, OWNED, FOREIGN,
    ]
    assert rs._filter_check_for_changes_entries(entries, {OWNED}) == [
        FOREIGN, {"ContentId": FOREIGN},
    ]


def test_filter_requires_prevalidated_entries():
    with pytest.raises(KeyError):
        rs._filter_check_for_changes_entries([{"unexpected": OWNED}], {OWNED})


def test_ownership_unknown_is_treated_as_owned():
    assert rs._check_for_changes_ownership_is_filtered(rs.OWNERSHIP_UNKNOWN) is True
    assert rs._check_for_changes_ownership_is_filtered(None) is False


def test_all_owned_batch_short_circuits_without_outbound_call(app, monkeypatch):
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda _content_id: SimpleNamespace(id=347),
    )
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("all-owned batch must not contact Kobo"),
    )
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=_request_entries(OWNED),
    ):
        response = _view(rs.handle_check_for_changes)()

    assert response.status_code == 200
    assert response.get_json() == []


def test_duplicate_foreign_ids_are_preserved_in_outbound_batch(app, monkeypatch):
    outbound = []

    def _proxy(*, data):
        outbound.extend(json.loads(data))
        return jsonify([])

    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: None)
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    entries = _request_entries(FOREIGN, FOREIGN)
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=entries,
    ):
        response = _view(rs.handle_check_for_changes)()

    assert outbound == entries
    assert response.status_code == 200
    assert response.get_json() == []


def test_null_content_id_short_circuits_full_handler(app, monkeypatch):
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda _content_id: pytest.fail("null ContentId must not reach ownership lookup"),
    )
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("null ContentId must not contact Kobo"),
    )
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST",
        json=[{"ContentId": None, "etag": "etag-null"}],
    ):
        response = _view(rs.handle_check_for_changes)()

    assert response.status_code == 200
    assert response.get_json() == []


def test_case_variant_owned_uuid_is_normalized_only_for_lookup(app, monkeypatch):
    looked_up = []
    original = f"  {{{OWNED.upper()}}}  "

    def _get_book_by_uuid(content_id):
        looked_up.append(content_id)
        return SimpleNamespace(id=347) if content_id == OWNED else None

    monkeypatch.setattr(rs.calibre_db, "get_book_by_uuid", _get_book_by_uuid)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("normalized owned UUID must not contact Kobo"),
    )
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=_request_entries(original),
    ):
        response = _view(rs.handle_check_for_changes)()

    assert looked_up == [OWNED]
    assert response.status_code == 200
    assert response.get_json() == []


def test_foreign_content_id_is_forwarded_in_its_original_representation(app, monkeypatch):
    outbound = []
    original = "  {KOBO-Store-Content}  "

    monkeypatch.setattr(rs.calibre_db, "get_book_by_uuid", lambda _content_id: None)

    def _proxy(*, data):
        outbound.extend(json.loads(data))
        return jsonify([])

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    entries = _request_entries(original)
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=entries,
    ):
        response = _view(rs.handle_check_for_changes)()

    assert outbound == entries
    assert response.get_json() == []


def test_case_variant_owned_uuid_is_filtered_from_upstream_response(app, monkeypatch):
    variant = f"{{{OWNED.upper()}}}"
    looked_up = []

    def _get_book_by_uuid(content_id):
        looked_up.append(content_id)
        return SimpleNamespace(id=347) if content_id == OWNED else None

    monkeypatch.setattr(rs.calibre_db, "get_book_by_uuid", _get_book_by_uuid)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services", lambda **_kwargs: jsonify([variant]),
    )
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=_request_entries(FOREIGN),
    ):
        response = _view(rs.handle_check_for_changes)()

    assert looked_up == [FOREIGN, OWNED]
    assert response.get_json() == []


def test_unparseable_request_short_circuits_without_outbound_call(app, monkeypatch):
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("unparseable batch must not contact Kobo"),
    )
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST",
        data=b"not-json", content_type="application/json",
    ):
        response = _view(rs.handle_check_for_changes)()

    assert response.status_code == 200
    assert response.get_json() == []


def test_mixed_handler_filters_outbound_request_and_defensive_response(app, monkeypatch):
    outbound = []

    def _ownership(content_id):
        return SimpleNamespace(id=347) if content_id == OWNED else None

    def _proxy(*, data):
        outbound.extend(json.loads(data))
        # Include an owned id Kobo was not asked about to exercise defense in depth.
        return jsonify([FOREIGN, {"ContentId": OWNED}])

    monkeypatch.setattr(rs, "resolve_entitlement_ownership", _ownership)
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    entries = _request_entries(OWNED, FOREIGN)
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=entries,
    ):
        response = _view(rs.handle_check_for_changes)()

    assert outbound == [entries[1]]
    assert response.status_code == 200
    assert response.get_json() == [FOREIGN]


def test_unrecognized_upstream_response_becomes_safe_empty_array(app, monkeypatch):
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services", lambda **_kwargs: jsonify({"unknown": "shape"}),
    )
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=_request_entries(FOREIGN),
    ):
        response = _view(rs.handle_check_for_changes)()

    assert response.status_code == 200
    assert response.get_json() == []


@pytest.mark.parametrize("status", [401, 403])
def test_upstream_auth_failure_with_json_list_is_propagated(app, monkeypatch, status):
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: make_response(jsonify([OWNED]), status),
    )
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=_request_entries(FOREIGN),
    ):
        response = _view(rs.handle_check_for_changes)()

    assert response.status_code == status
    assert response.get_json() == [OWNED]


def test_annotation_get_proxies_even_for_owned_content(app, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda _content_id: pytest.fail("annotation GET must not expose ownership"),
    )
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations?limit=100", method="GET",
    ):
        assert _view(rs.handle_annotations)(OWNED) is sentinel


def test_unauthenticated_annotation_get_does_not_resolve_ownership(app, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(rs.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(is_authenticated=False, id=None),
    )
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda _content_id: pytest.fail("pre-auth annotation GET must not expose ownership"),
    )
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)
    decorated = rs.requires_reading_services_auth_and_config(
        lambda: pytest.fail("unauthenticated request must not reach handler")
    )
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations?limit=100", method="GET",
    ):
        assert decorated() is sentinel


def test_unauthenticated_annotation_patch_is_not_acknowledged_upstream(app, monkeypatch):
    monkeypatch.setattr(rs.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(is_authenticated=False, id=None),
    )
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda: pytest.fail("an uncaptured PATCH must not be acknowledged upstream"),
    )
    decorated = rs.requires_reading_services_auth_and_config(
        lambda: pytest.fail("unauthenticated request must not reach the handler")
    )
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH",
        json={"updatedAnnotations": [{"id": "annotation-1"}]},
    ):
        response = decorated()

    assert response.status_code == 401
    assert response.get_json() == {"error": "Authentication required"}


def test_empty_patch_for_unowned_content_still_proxies(app, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: None)
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH",
        json={"updatedAnnotations": []},
    ):
        assert _view(rs.handle_annotations)(OWNED) is sentinel


def test_patch_captures_updated_annotations_before_proxying(app, monkeypatch):
    from cps.services import annotation_sync

    sentinel = object()
    book = SimpleNamespace(id=347, title="Flatland", identifiers=[])
    user = SimpleNamespace(id=7, name="test-user", is_authenticated=True)
    dispatched = []
    annotation = {"id": "annotation-1", "type": "highlight"}
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: book)
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rs, "current_user", user)
    monkeypatch.setattr(
        annotation_sync, "dispatch_annotation_sync",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH",
        json={"updatedAnnotations": [annotation]},
    ):
        assert _view(rs.handle_annotations)(OWNED) is sentinel

    assert dispatched == [(([annotation], book, user), {"origin_device_id": None})]


def test_patch_persistence_failure_is_not_acknowledged_upstream(
    app, monkeypatch, caplog,
):
    from cps.services import annotation_sync

    book = SimpleNamespace(id=347, title="Flatland", identifiers=[])
    user = SimpleNamespace(id=7, name="test-user", is_authenticated=True)
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: book)
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rs, "current_user", user)
    monkeypatch.setattr(annotation_sync, "dispatch_annotation_sync", lambda *_a, **_k: False)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda: pytest.fail("a failed local write must not be acknowledged upstream"),
    )

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH",
        json={"updatedAnnotations": [{"id": "annotation-1"}]},
    ), caplog.at_level("ERROR"):
        response = _view(rs.handle_annotations)(OWNED)

    assert response.status_code == 503
    assert response.get_json() == {"error": "Annotation capture temporarily unavailable"}
    assert "not fully persisted" in caplog.text


def test_patch_declares_kobo_delete_authority_before_proxying(app, monkeypatch):
    from cps.services import annotation_sync

    sentinel = object()
    book = SimpleNamespace(id=347, title="Flatland", identifiers=[])
    user = SimpleNamespace(id=7, name="test-user", is_authenticated=True)
    dispatched = []
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: book)
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rs, "current_user", user)
    monkeypatch.setattr(
        annotation_sync, "dispatch_annotation_deletes",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH",
        json={"deletedAnnotationIds": ["annotation-1"]},
    ):
        assert _view(rs.handle_annotations)(OWNED) is sentinel

    assert dispatched == [(
        (["annotation-1"], user),
        {"book_id": book.id, "deletable_sources": {"kobo"}},
    )]


def test_patch_ownership_unknown_is_visible_and_not_proxied(app, monkeypatch, caplog):
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership", lambda _content_id: rs.OWNERSHIP_UNKNOWN,
    )
    monkeypatch.setattr(
        rs, "log_annotation_data",
        lambda *_args, **_kwargs: pytest.fail("unknown ownership must stop capture"),
    )
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda: pytest.fail("uncaptured PATCH must not be acknowledged to Kobo"),
    )
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH",
        json={"updatedAnnotations": [{"id": "annotation-1"}]},
    ), caplog.at_level("ERROR"):
        response = _view(rs.handle_annotations)(OWNED)

    assert response.status_code == 503
    assert response.get_json() == {"error": "Annotation capture temporarily unavailable"}
    assert OWNED in caplog.text


def test_patch_non_object_body_is_refused_without_upstream_acknowledgement(
    app, monkeypatch, caplog,
):
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda _content_id: SimpleNamespace(id=347, title="Flatland", identifiers=[]),
    )
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda: pytest.fail("a non-object PATCH must not be acknowledged upstream"),
    )
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(id=7, name="test-user", is_authenticated=True),
    )
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH",
        json=["not", "an", "object"],
    ), caplog.at_level("WARNING"):
        response = _view(rs.handle_annotations)(OWNED)

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "Annotation capture temporarily unavailable",
    }
    assert "PATCH body is not a JSON object" in caplog.text


def test_patch_non_object_body_for_confirmed_unowned_content_still_proxies(
    app, monkeypatch,
):
    sentinel = object()
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: None)
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)

    with app.test_request_context(
        f"/api/v3/content/{FOREIGN}/annotations", method="PATCH",
        json=["not", "an", "object"],
    ):
        assert _view(rs.handle_annotations)(FOREIGN) is sentinel


def test_patch_non_list_annotation_batch_reaches_dispatcher_and_is_refused(
    app, monkeypatch, caplog,
):
    from cps.services import annotation_sync

    dispatched = []
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda _content_id: SimpleNamespace(id=347, title="Flatland", identifiers=[]),
    )
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda: pytest.fail("a non-list annotation batch must not be acknowledged upstream"),
    )
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(id=7, name="test-user", is_authenticated=True),
    )
    monkeypatch.setattr(
        annotation_sync, "dispatch_annotation_sync",
        lambda batch, *_args, **_kwargs: dispatched.append(batch) or False,
    )
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH",
        json={"updatedAnnotations": {"id": "not-a-list"}},
    ), caplog.at_level("ERROR"):
        response = _view(rs.handle_annotations)(OWNED)

    assert dispatched == [{"id": "not-a-list"}]
    assert response.status_code == 503
    assert response.get_json() == {
        "error": "Annotation capture temporarily unavailable",
    }
    assert "not fully persisted" in caplog.text


@pytest.mark.parametrize(
    "raw_body",
    [
        b'["not","an","object"]',
        b'{"updatedAnnotations":{"id":"not-a-list"}}',
    ],
)
def test_registered_annotation_patch_route_returns_retryable_refusal(
    monkeypatch, raw_body,
):
    app = Flask(__name__)
    app.register_blueprint(rs.readingservices_api_v3)
    book = SimpleNamespace(id=347, title="Flatland", identifiers=[])
    monkeypatch.setattr(rs.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(
            id=7, name="test-user", is_authenticated=True,
        ),
    )
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: book)
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "cps.services.device_registry.register_kobo_device_best_effort",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("malformed local data reached the upstream proxy"),
    )

    response = app.test_client().patch(
        f"/api/v3/content/{OWNED}/annotations",
        data=raw_body,
        content_type="application/json",
    )

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "Annotation capture temporarily unavailable",
    }


@pytest.mark.parametrize(
    "raw_body",
    [
        b"",
        b" \r\n\t",
        b"{}",
        b'{"updatedAnnotations":[],"deletedAnnotationIds":[]}',
    ],
)
def test_empty_and_object_noop_patch_bodies_still_proxy(
    app, monkeypatch, raw_body,
):
    sentinel = object()
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda _content_id: SimpleNamespace(id=347, title="Flatland", identifiers=[]),
    )
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(id=7, name="test-user", is_authenticated=True),
    )

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH",
        data=raw_body, content_type="application/json",
    ):
        assert _view(rs.handle_annotations)(OWNED) is sentinel
