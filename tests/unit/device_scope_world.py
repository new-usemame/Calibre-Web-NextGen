# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A real library, website and KOReader sync for the per-device scope tests.

``LibraryWorld`` (real app.db + metadata.db, real Basic auth, real kosync
blueprint) plus the website half: a session login and the devices API
(``cps.annotations``), so a test sets a device's scope the way the Devices
page does and reads what a KOReader device is then told to hold.

Contract: CONTRACT.md at the root of the device-scope PR A branch.
"""

from cps import ub
from cps.services import device_registry
from tests.unit.koreader_library_world import LibraryWorld

DEVICES = "/api/annotations/devices"


class ScopeWorld(LibraryWorld):
    def __init__(self, monkeypatch, tmp_path):
        super().__init__(monkeypatch, tmp_path)
        self.enable_web()
        from cps import annotations
        self.app.register_blueprint(annotations.annotations_bp)
        self._browsers = {}

    # -- devices --------------------------------------------------------------

    def koreader(self, user, raw_id, name="Kindle"):
        """Register a KOReader device the way its first sync does."""
        internal = device_registry.register_koreader_device_best_effort(
            user_id=user.id, device_id=raw_id, device_name=name,
            secret_key=self.app.secret_key,
        )
        assert internal is not None
        self.session.expire_all()
        return self.session.get(ub.Device, internal)

    def kobo(self, user, name="Kobo"):
        device = ub.Device(user_id=user.id, kind="kobo", display_name=name,
                           active=True, created_by="auto")
        self.session.add(device)
        self.session.commit()
        return device

    def web(self, user):
        if user.name not in self._browsers:
            self._browsers[user.name] = self.browser(user.name)
        return self._browsers[user.name]

    def set_scope(self, user, device, mode, shelves=(), magic_shelves=()):
        """What the Devices page sends when the owner picks a scope."""
        body = {"mode": mode}
        if mode == "shelves":
            body["shelf_ids"] = [shelf.id for shelf in shelves]
            body["magic_shelf_ids"] = [shelf.id for shelf in magic_shelves]
        return self.web(user).patch(
            "%s/%s" % (DEVICES, device.public_id), json={"book_scope": body})

    def device_rows(self, user):
        response = self.web(user).get(DEVICES)
        assert response.status_code == 200, response.get_data(as_text=True)
        return {row["public_id"]: row for row in response.get_json()["devices"]}

    # -- what a KOReader device is told ----------------------------------------

    def manifest(self, user, raw_id, name="Kindle", **params):
        """The whole manifest one KOReader device reads, every page of it."""
        headers = self.basic(user.name)
        headers.update({"X-CWNG-Device-ID": raw_id, "X-CWNG-Device-Name": name})
        params.setdefault("limit", 500)
        response = self.client.get("/kosync/syncs/library", query_string=params,
                                   headers=headers)
        return response

    def held(self, user, raw_id, name="Kindle"):
        response = self.manifest(user, raw_id, name)
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert body["next_cursor"] is None
        return {book["book_id"] for book in body["books"]}, body
