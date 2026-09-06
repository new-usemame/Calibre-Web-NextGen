"""What the real-app fixture may and may not claim Docker parity for.

The Docker comparison runs only where a matching image exists, so the rule that
decides which probe rows it compares would otherwise never be exercised on a
developer machine or in the fast lane. These cases pin the decision itself.
"""
import pytest

from tests.integration.test_real_app import _backend_parity_rows

pytestmark = pytest.mark.unit


def _row(blueprint, path, **extra):
    row = {"blueprint": blueprint, "path": path, "status": 200,
           "client_address_sensitive": False}
    row.update(extra)
    return row


BACKEND = [_row("web", "/ajax/listbooks"), _row("opds", "/opds")]
BUNDLE = _row("spa", "/app/")
ADDRESS_SENSITIVE = _row("cwa_internal", "/cwa-internal/duplicate-scan-status",
                         client_address_sensitive=True)


def _paths(excluded):
    return [row["path"] for row, _reason in excluded]


def test_bundle_present_compares_every_row():
    wire = BACKEND + [BUNDLE]
    compared, excluded = _backend_parity_rows(wire, bundle_built=True)
    assert compared == wire
    assert excluded == []


def test_bundle_absent_excludes_only_the_bundle_served_row():
    compared, excluded = _backend_parity_rows(BACKEND + [BUNDLE], bundle_built=False)
    assert compared == BACKEND
    assert _paths(excluded) == ["/app/"]


def test_backend_rows_are_never_dropped_when_the_bundle_is_absent():
    compared, _ = _backend_parity_rows(BACKEND, bundle_built=False)
    assert compared == BACKEND


def test_an_unclaimed_bundle_served_blueprint_fails_rather_than_disappearing():
    # A new blueprint mounted under the SPA prefix must not join the exclusion
    # by accident: that would delete parity coverage while the suite stayed green.
    wire = BACKEND + [BUNDLE, _row("reader", "/app/reader")]
    with pytest.raises(AssertionError):
        _backend_parity_rows(wire, bundle_built=False)


def test_a_route_that_answers_a_stranger_differently_is_not_compared():
    # Werkzeug presents REMOTE_ADDR 127.0.0.1; a container's published port does
    # not. Comparing such a route reports the probe's transport as a difference
    # in the application.
    compared, excluded = _backend_parity_rows(
        BACKEND + [ADDRESS_SENSITIVE], bundle_built=True)
    assert compared == BACKEND
    assert _paths(excluded) == ["/cwa-internal/duplicate-scan-status"]
    assert excluded[0][1] == "answers a non-loopback client differently"


def test_address_sensitivity_is_excluded_even_with_the_bundle_missing():
    compared, excluded = _backend_parity_rows(
        BACKEND + [ADDRESS_SENSITIVE, BUNDLE], bundle_built=False)
    assert compared == BACKEND
    assert sorted(_paths(excluded)) == [
        "/app/", "/cwa-internal/duplicate-scan-status"]


def test_a_route_not_flagged_sensitive_stays_in_the_comparison():
    # The flag is measured by cases.py, not asserted here: a row without it must
    # be compared, so a classifier that silently stopped flagging cannot quietly
    # widen the exclusion.
    wire = BACKEND + [_row("cwa_internal", "/cwa-internal/duplicate-scan-status")]
    compared, excluded = _backend_parity_rows(wire, bundle_built=True)
    assert compared == wire
    assert excluded == []
