# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""An unauthenticated kosync request must answer 401, not 400.

``handle_sync_error`` turned every ``KOSyncError`` into a 400, including
``ERROR_UNAUTHORIZED_USER``. So ``GET /kosync/syncs/progress/<document>`` without
credentials replied ``400 Bad Request`` with a body reading
``{"error": 2001, "message": "Unauthorized"}`` -- the status contradicting the
payload, and a client unable to tell "log in again" from "your request was
malformed" by the one field that exists to say so.

Sibling handlers in the same blueprint already return 401 directly for exactly
this condition, so the protocol was inconsistent with itself.

This was invisible for a long time because the integration test that covers it
could not run: the shared API-client fixture had stopped authenticating, and the
lane skipped rather than failed. The fix that revived the lane is what surfaced
this.

Both directions are pinned. Mapping *every* error to 401 would be as wrong as
mapping every one to 400, and would be just as green if only the auth case were
asserted.
"""
from __future__ import annotations

import sys

import pytest

# The blueprint inside this module is also called ``kosync``, so a plain
# ``from ... import kosync`` binds the Blueprint and not the module.
import cps.progress_syncing.protocols.kosync  # noqa: F401

kosync = sys.modules["cps.progress_syncing.protocols.kosync"]

pytestmark = pytest.mark.unit


@pytest.fixture
def app_context():
    """handle_sync_error builds a JSON response, which needs an app context."""
    from flask import Flask

    app = Flask(__name__)
    with app.app_context():
        yield


def _status(error_code):
    """handle_sync_error returns Flask's (response, status) tuple."""
    _response, status = kosync.handle_sync_error(
        kosync.KOSyncError(error_code, "irrelevant to the status"),
    )
    return status


def test_an_unauthorized_error_answers_401(app_context):
    assert _status(kosync.ERROR_UNAUTHORIZED_USER) == 401


@pytest.mark.parametrize("error_code", [
    kosync.ERROR_DOCUMENT_FIELD_MISSING,
    kosync.ERROR_INTERNAL,
])
def test_other_errors_still_answer_400(app_context, error_code):
    """The change must be narrow: only the authentication case moves."""
    assert _status(error_code) == 400
