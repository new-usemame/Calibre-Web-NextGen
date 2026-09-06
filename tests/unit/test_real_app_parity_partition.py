"""What the real-app fixture may and may not claim parity for.

The Docker comparison runs only where a matching image exists, so the rule that
decides which probe rows it compares would otherwise never be exercised on a
developer machine or in the fast lane. These cases pin the decision itself.
"""
import pytest

from tests.integration.test_real_app import _backend_parity_rows

pytestmark = pytest.mark.unit


def _row(blueprint, path):
    return {"blueprint": blueprint, "path": path, "status": 200}


BACKEND = [_row("web", "/ajax/listbooks"), _row("opds", "/opds")]
BUNDLE = _row("spa", "/app/")


def test_bundle_present_compares_every_row():
    wire = BACKEND + [BUNDLE]
    compared, excluded = _backend_parity_rows(wire, bundle_built=True)
    assert compared == wire
    assert excluded == []


def test_bundle_absent_excludes_only_the_bundle_served_row():
    compared, excluded = _backend_parity_rows(BACKEND + [BUNDLE], bundle_built=False)
    assert compared == BACKEND
    assert excluded == [BUNDLE]


def test_backend_rows_are_never_dropped_when_the_bundle_is_absent():
    compared, _ = _backend_parity_rows(BACKEND, bundle_built=False)
    assert compared == BACKEND


def test_an_unclaimed_bundle_served_blueprint_fails_rather_than_disappearing():
    # A new blueprint mounted under the SPA prefix must not join the exclusion
    # by accident: that would delete parity coverage while the suite stayed green.
    wire = BACKEND + [BUNDLE, _row("reader", "/app/reader")]
    with pytest.raises(AssertionError):
        _backend_parity_rows(wire, bundle_built=False)
