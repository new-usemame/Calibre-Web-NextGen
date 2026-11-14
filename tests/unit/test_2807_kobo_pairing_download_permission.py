# SPDX-License-Identifier: GPL-3.0-or-later
"""The pairing details say when this account's Kobo would be refused its sync (#2807).

A Kobo token whose account lacks download permission gets a 403 on
``/v1/library/sync``. The Kobo only shows that the sync failed, and the one
explanation was an INFO line in the server log, so a reporter on #2807 spent a
round of testing on it. The pairing payload carries the same check, so the
setup screens can say so before anyone edits the Kobo's config file.

These drive the real Kobo blueprint and the real pairing view on one database
user, and compare the payload with what the sync route actually does.
"""

import inspect
from unittest.mock import patch

import pytest

from .kobo_pairing_world import HEADERS, kobo_app  # noqa: F401


def _refused_for_download(kobo_app, monkeypatch):
    """Did ``/v1/library/sync`` stop at the download-permission gate?

    Past the gate a real sync needs a calibre library this world does not
    build, so anything after the gate (including an error) counts as "not
    refused here".
    """
    said = []
    log = kobo_app.kobo.log
    real_info = log.info
    monkeypatch.setattr(log, "info", lambda msg, *a, **kw: (said.append(str(msg)), real_info(msg, *a, **kw)))
    try:
        status = kobo_app.app.test_client().get(
            "/kobo/reader-token/v1/library/sync", headers=HEADERS).status_code
    except Exception:  # noqa: BLE001 - past the gate, see docstring
        status = None
    return status == 403 and any("download permissions" in m for m in said)


def _pairing_payload(kobo_app, user_id=None):
    from cps import ub
    from cps.api import kobo_pairing

    reader = ub.session.query(ub.User).filter(ub.User.name == "reader").one()
    with kobo_app.app.test_request_context(
            "/api/v1/account/kobo-sync-token", base_url="https://books.example.test"), \
            patch.object(kobo_pairing, "current_user", reader):
        response = inspect.unwrap(kobo_pairing.get_kobo_sync_token)(user_id)
    return response.get_json()


@pytest.mark.unit
def test_an_account_without_download_permission_is_told_its_kobo_will_be_refused(
        kobo_app, monkeypatch):
    # The world's reader has role=0: no download permission.
    assert _refused_for_download(kobo_app, monkeypatch)

    payload = _pairing_payload(kobo_app)

    assert payload["download_allowed"] is False


@pytest.mark.unit
def test_granting_download_permission_clears_the_notice_and_the_refusal(kobo_app, monkeypatch):
    from cps import constants, ub

    reader = ub.session.query(ub.User).filter(ub.User.name == "reader").one()
    reader.role = constants.ROLE_DOWNLOAD
    ub.session.commit()

    payload = _pairing_payload(kobo_app)

    assert payload["download_allowed"] is True
    assert not _refused_for_download(kobo_app, monkeypatch)


@pytest.mark.unit
def test_an_admin_viewing_another_account_sees_that_accounts_permission(kobo_app):
    from cps import constants, ub

    admin = ub.User(name="admin", email="admin@example.invalid", password="unused",
                    role=constants.ROLE_ADMIN | constants.ROLE_DOWNLOAD)
    ub.session.add(admin)
    ub.session.commit()
    reader = ub.session.query(ub.User).filter(ub.User.name == "reader").one()

    from cps.api import kobo_pairing
    with kobo_app.app.test_request_context(
            f"/api/v1/admin/users/{reader.id}/kobo-sync-token",
            base_url="https://books.example.test"), \
            patch.object(kobo_pairing, "current_user", admin):
        payload = inspect.unwrap(kobo_pairing.get_kobo_sync_token)(reader.id).get_json()

    assert payload["user_id"] == reader.id
    assert payload["download_allowed"] is False
