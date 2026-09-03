from datetime import datetime, timedelta

import pytest
from flask import Flask

from tests.unit.test_1925_kobo_sync_dedownload import (
    _add_kobo_shelf,
    _add_reading_state,
    _changed_reading_states,
    _entitlements,
    _sync_through_flask_error_pipeline,
    sync_harness,
)
from tests.unit.test_kobo_entitlement_ledger_forensic import (
    _partial_token_with_book_cursor,
)


pytestmark = pytest.mark.unit


def test_production_sync_route_uses_pending_page_reset_boundary(monkeypatch):
    """The public Blueprint route must retain the reset exception boundary."""
    from cps import kobo

    app = Flask("kobo-production-dispatch-regression")
    app.register_blueprint(kobo.kobo)
    [sync_rule] = [
        rule for rule in app.url_map.iter_rules()
        if rule.endpoint == "kobo.HandleSyncRequest"
    ]
    assert sync_rule.rule == "/kobo/<auth_token>/v1/library/sync"

    production_view = app.view_functions[sync_rule.endpoint]
    assert production_view is kobo._dispatch_sync_request
    boundary_calls = []
    sentinel = object()

    def boundary(handler):
        boundary_calls.append(handler)
        return sentinel

    def bypassed_handler():
        pytest.fail("production sync dispatch bypassed reset boundary")

    monkeypatch.setattr(
        kobo, "_run_sync_with_pending_page_reset_boundary", boundary,
    )
    monkeypatch.setattr(kobo, "HandleSyncRequest", bypassed_handler)

    assert production_view() is sentinel
    assert boundary_calls == [bypassed_handler]


def _prepare_latched_state(sync_harness, monkeypatch):
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    modified = datetime(2026, 8, 28, 12, 30, 0)
    _add_reading_state(sync_harness, modified, progress=80.0)
    offered = sync_harness.sync()
    assert len(_entitlements(offered)) == 1
    assert _changed_reading_states(offered) == []
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True
    return modified


def _partial_after(cursor):
    return _partial_token_with_book_cursor(cursor)


def _degrade_outgoing_to_partial(outgoing):
    from cps.services import SyncToken

    parsed = SyncToken.SyncToken.from_headers({
        SyncToken.SyncToken.SYNC_TOKEN_HEADER: outgoing,
    })
    return SyncToken.b64encode_json({
        "version": "1-0-0",
        "data": {
            "raw_kobo_store_token": parsed.raw_kobo_store_token,
            "books_last_modified": SyncToken.to_epoch_timestamp(
                parsed.books_last_modified,
            ),
            "books_last_created": SyncToken.to_epoch_timestamp(
                parsed.books_last_created,
            ),
            # The archive cursor is the field observed to disappear.
            "reading_state_last_modified": SyncToken.to_epoch_timestamp(
                parsed.reading_state_last_modified,
            ),
            "tags_last_modified": SyncToken.to_epoch_timestamp(
                parsed.tags_last_modified,
            ),
            "books_last_id": parsed.books_last_id,
        },
    })


@pytest.mark.parametrize("incoming", ["partial", "foreign", "absent"])
def test_latch_arriving_with_non_cwng_token_repairs_then_echoes_once(
        sync_harness, monkeypatch, incoming):
    from cps import ub

    _prepare_latched_state(sync_harness, monkeypatch)
    token = {
        "partial": _partial_after(datetime(2027, 1, 1)),
        "foreign": "store.fragment",
        "absent": None,
    }[incoming]

    repair = sync_harness.sync(token, acknowledge=False)
    assert len(_changed_reading_states(repair)) == 1
    assert _entitlements(repair) == []
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True

    echo = sync_harness.sync(
        repair.headers[sync_harness.token_header], acknowledge=False,
    )
    assert len(_changed_reading_states(echo)) == 1
    assert _entitlements(echo) == []
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False

    terminal = sync_harness.sync(
        echo.headers[sync_harness.token_header], acknowledge=False,
    )
    assert _changed_reading_states(terminal) == []
    assert _entitlements(terminal) == []


@pytest.mark.parametrize("replacement", ["partial", "foreign"])
def test_non_cwng_token_cannot_ack_pending_repair_and_regenerates_it(
        sync_harness, monkeypatch, replacement):
    from cps import kobo_sync_status, ub

    _prepare_latched_state(sync_harness, monkeypatch)
    first_partial = _partial_after(datetime(2027, 1, 1))
    repair = sync_harness.sync(first_partial, acknowledge=False)
    old_pending = kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    )
    old_outgoing_token = old_pending.outgoing_token
    assert len(_changed_reading_states(repair)) == 1

    token = {
        "partial": _partial_after(datetime(2027, 1, 2)),
        "foreign": "different.fragment",
    }[replacement]
    regenerated = sync_harness.sync(token, acknowledge=False)
    new_pending = kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    )
    assert new_pending.outgoing_token != old_outgoing_token
    assert len(_changed_reading_states(regenerated)) == 1
    assert _entitlements(regenerated) == []
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True

    echo = sync_harness.sync(
        regenerated.headers[sync_harness.token_header], acknowledge=False,
    )
    assert len(_changed_reading_states(echo)) == 1
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False


def test_repeated_partial_request_replays_pending_repair_byte_identically(
        sync_harness, monkeypatch):
    from cps import ub

    _prepare_latched_state(sync_harness, monkeypatch)
    partial = _partial_after(datetime(2027, 1, 1))
    repair = sync_harness.sync(partial, acknowledge=False)
    replay = sync_harness.sync(partial, acknowledge=False)

    assert replay.status_code == repair.status_code == 200
    assert replay.get_data() == repair.get_data()
    assert replay.headers[sync_harness.token_header] == repair.headers[
        sync_harness.token_header
    ]
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True


@pytest.mark.parametrize("classification", ["changed", "missing"])
def test_partial_reoffer_defers_repair_without_disarming_latch(
        sync_harness, monkeypatch, classification):
    from cps import ub

    modified = _prepare_latched_state(sync_harness, monkeypatch)
    if classification == "changed":
        sync_harness.book.title = "Composition mutation"
        sync_harness.book.sort = "Composition mutation"
        sync_harness.book.last_modified = modified + timedelta(minutes=1)
        expected = "ChangedEntitlement"
        cursor = modified
    else:
        sync_harness.session.query(
            ub.KoboDeviceBookEntitlement,
        ).delete(synchronize_session=False)
        expected = "NewEntitlement"
        cursor = datetime(2027, 1, 1)
    sync_harness.session.commit()

    offered = sync_harness.sync(_partial_after(cursor), acknowledge=False)
    [entitlement] = _entitlements(offered)
    assert set(entitlement) == {expected}
    assert _changed_reading_states(offered) == []
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True

    repair = sync_harness.sync(
        offered.headers[sync_harness.token_header], acknowledge=False,
    )
    assert _entitlements(repair) == []
    assert len(_changed_reading_states(repair)) == 1
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True

    echo = sync_harness.sync(
        repair.headers[sync_harness.token_header], acknowledge=False,
    )
    assert len(_changed_reading_states(echo)) == 1
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False


@pytest.mark.parametrize("replacement", ["partial", "foreign"])
def test_unacknowledged_echo_survives_non_cwng_reset(
        sync_harness, monkeypatch, replacement):
    from cps import kobo_sync_status, ub

    _prepare_latched_state(sync_harness, monkeypatch)
    repair = sync_harness.sync(
        _partial_after(datetime(2027, 1, 1)), acknowledge=False,
    )
    echo = sync_harness.sync(
        repair.headers[sync_harness.token_header], acknowledge=False,
    )
    assert len(_changed_reading_states(echo)) == 1
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False
    echo_pending = kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    )
    assert echo_pending is not None

    token = {
        "partial": _degrade_outgoing_to_partial(
            echo_pending.outgoing_token,
        ),
        "foreign": "reset.fragment",
    }[replacement]
    recovered = sync_harness.sync(token, acknowledge=False)

    assert len(_changed_reading_states(recovered)) == 1
    assert _entitlements(recovered) == []
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True


@pytest.mark.parametrize("classification", ["changed", "missing"])
def test_partial_reset_reoffer_defers_owed_echo_to_fresh_repair(
        sync_harness, monkeypatch, classification):
    from cps import kobo_sync_status, ub

    _prepare_latched_state(sync_harness, monkeypatch)
    repair = sync_harness.sync(
        _partial_after(datetime(2027, 1, 1)), acknowledge=False,
    )
    echo = sync_harness.sync(
        repair.headers[sync_harness.token_header], acknowledge=False,
    )
    echo_pending = kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    )
    assert len(_changed_reading_states(echo)) == 1
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False

    if classification == "changed":
        sync_harness.book.title = "Pending echo mutation"
        sync_harness.book.sort = "Pending echo mutation"
        sync_harness.book.last_modified = datetime(2027, 1, 2)
        expected = "ChangedEntitlement"
    else:
        sync_harness.session.query(
            ub.KoboDeviceBookEntitlement,
        ).delete(synchronize_session=False)
        expected = "NewEntitlement"
    sync_harness.session.commit()

    reoffered = sync_harness.sync(
        _degrade_outgoing_to_partial(echo_pending.outgoing_token),
        acknowledge=False,
    )
    [entitlement] = _entitlements(reoffered)
    assert set(entitlement) == {expected}
    assert _changed_reading_states(reoffered) == []
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True

    fresh_repair = sync_harness.sync(
        reoffered.headers[sync_harness.token_header], acknowledge=False,
    )
    assert _entitlements(fresh_repair) == []
    assert len(_changed_reading_states(fresh_repair)) == 1
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True

    final_echo = sync_harness.sync(
        fresh_repair.headers[sync_harness.token_header], acknowledge=False,
    )
    assert len(_changed_reading_states(final_echo)) == 1
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False


def test_echo_rearm_failure_retains_pending_page_and_returns_503(
        sync_harness, monkeypatch):
    from cps import kobo_sync_status, ub
    from cps.services import device_reading_position as positions

    _prepare_latched_state(sync_harness, monkeypatch)
    repair = sync_harness.sync(
        _partial_after(datetime(2027, 1, 1)), acknowledge=False,
    )
    echo = sync_harness.sync(
        repair.headers[sync_harness.token_header], acknowledge=False,
    )
    pending = kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    )
    pending_token = pending.outgoing_token
    assert len(_changed_reading_states(echo)) == 1
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False

    def fail_rearm(*_args, **_kwargs):
        raise RuntimeError("injected echo rearm failure")

    monkeypatch.setattr(positions, "mark_rehydrate_needed", fail_rearm)
    failed = _sync_through_flask_error_pipeline(
        sync_harness,
        token=_degrade_outgoing_to_partial(pending_token),
    )

    assert failed.status_code == 503
    assert b"Entitlement" not in failed.get_data()
    sync_harness.session.expire_all()
    retained = kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    )
    assert retained is not None
    assert retained.outgoing_token == pending_token
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False


@pytest.mark.parametrize("replacement", ["partial", "foreign"])
def test_pending_echo_reset_does_not_cross_entitlement_removal(
        sync_harness, monkeypatch, replacement):
    from cps import kobo_sync_status, ub

    sync_harness.user.kobo_only_shelves_sync = True
    _shelf, link = _add_kobo_shelf(
        sync_harness,
        date_added=datetime(2026, 8, 28, 12, 5, 0),
    )
    _prepare_latched_state(sync_harness, monkeypatch)
    repair = sync_harness.sync(
        _partial_after(datetime(2027, 1, 1)), acknowledge=False,
    )
    echo = sync_harness.sync(
        repair.headers[sync_harness.token_header], acknowledge=False,
    )
    pending_echo = kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    )
    assert len(_changed_reading_states(echo)) == 1
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False

    sync_harness.session.delete(link)
    sync_harness.session.commit()
    reset_token = {
        "partial": _degrade_outgoing_to_partial(
            pending_echo.outgoing_token,
        ),
        "foreign": "removal.reset.fragment",
    }[replacement]
    removed = sync_harness.sync(reset_token, acknowledge=False)

    [envelope] = _entitlements(removed)
    assert set(envelope) == {"ChangedEntitlement"}
    assert envelope["ChangedEntitlement"]["BookEntitlement"][
        "IsRemoved"
    ] is True
    assert _changed_reading_states(removed) == []

    after_removal_ack = sync_harness.sync(
        removed.headers[sync_harness.token_header], acknowledge=False,
    )
    assert _entitlements(after_removal_ack) == []
    assert _changed_reading_states(after_removal_ack) == []
    assert sync_harness.session.query(
        ub.DeviceReadingPosition,
    ).count() == 0

    terminal = sync_harness.sync(
        after_removal_ack.headers[sync_harness.token_header],
        acknowledge=False,
    )
    assert _entitlements(terminal) == []
    assert _changed_reading_states(terminal) == []


def test_query_failure_after_pending_echo_reset_restores_page_before_503(
        sync_harness, monkeypatch):
    from cps import kobo, kobo_sync_status, ub

    _prepare_latched_state(sync_harness, monkeypatch)
    repair = sync_harness.sync(
        _partial_after(datetime(2027, 1, 1)), acknowledge=False,
    )
    echo = sync_harness.sync(
        repair.headers[sync_harness.token_header], acknowledge=False,
    )
    pending_echo = kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    )
    pending_token = pending_echo.outgoing_token
    pending_snapshot = (
        pending_echo.device_id,
        pending_echo.incoming_token_hash,
        pending_echo.outgoing_token,
        pending_echo.response_body,
        pending_echo.response_headers_json,
        pending_echo.confirmation_json,
        pending_echo.created_at,
    )
    partial = _degrade_outgoing_to_partial(pending_token)
    assert len(_changed_reading_states(echo)) == 1
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False

    original_capture = kobo._capture_query_identities

    def fail_candidate_snapshot(*_args, **_kwargs):
        raise RuntimeError("injected post-reset candidate query failure")

    monkeypatch.setattr(
        kobo, "_capture_query_identities", fail_candidate_snapshot,
    )
    failed = _sync_through_flask_error_pipeline(
        sync_harness, token=partial,
    )

    assert failed.status_code == 503
    assert b"Entitlement" not in failed.get_data()
    sync_harness.session.expire_all()
    retained = kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    )
    assert retained is not None
    assert (
        retained.device_id,
        retained.incoming_token_hash,
        retained.outgoing_token,
        retained.response_body,
        retained.response_headers_json,
        retained.confirmation_json,
        retained.created_at,
    ) == pending_snapshot
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False

    monkeypatch.setattr(kobo, "_capture_query_identities", original_capture)
    recovered = sync_harness.sync(partial, acknowledge=False)
    assert recovered.status_code == 200
    assert _entitlements(recovered) == []
    assert len(_changed_reading_states(recovered)) == 1
    assert sync_harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is True
    replacement = kobo_sync_status.get_pending_sync_page(
        sync_harness.device.id,
    )
    assert replacement is not None
    assert replacement.outgoing_token != pending_token


def test_query_failure_without_pending_reset_keeps_http_500(
        sync_harness, monkeypatch):
    from cps import kobo

    def fail_candidate_snapshot(*_args, **_kwargs):
        raise RuntimeError("injected ordinary candidate query failure")

    monkeypatch.setattr(
        kobo, "_capture_query_identities", fail_candidate_snapshot,
    )
    failed = _sync_through_flask_error_pipeline(sync_harness)

    assert failed.status_code == 500
