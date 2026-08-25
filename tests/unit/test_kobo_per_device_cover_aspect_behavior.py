# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Behavioral coverage for request-specific Kobo cover aspect selection."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from flask import Flask

from cps import kobo


pytestmark = pytest.mark.unit


class _Column:
    """Small SQLAlchemy-column stand-in used by the neighboring Kobo tests."""

    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return _Predicate(self.name, value)

    def is_(self, value):
        return _Predicate(self.name, value)


@dataclass(frozen=True)
class _Predicate:
    field: str
    value: object


class _Device:
    model = _Column("model")
    user_id = _Column("user_id")
    kind = _Column("kind")
    active = _Column("active")


@dataclass
class _DeviceRow:
    model: str
    user_id: int = 7
    kind: str = "kobo"
    active: bool = True


class _DeviceQuery:
    def __init__(self, rows):
        self.rows = rows
        self.predicates = []

    def filter(self, *predicates):
        self.predicates.extend(predicates)
        return self

    def all(self):
        matching = [
            row for row in self.rows
            if all(getattr(row, predicate.field) == predicate.value
                   for predicate in self.predicates)
        ]
        return [(row.model,) for row in matching]


class _DeviceSession:
    def __init__(self, rows=(), error=None):
        self.rows = list(rows)
        self.error = error
        self.query_count = 0

    def query(self, projected_column):
        assert projected_column is _Device.model
        self.query_count += 1
        if self.error is not None:
            raise self.error
        return _DeviceQuery(self.rows)


@pytest.fixture
def resolution_harness(monkeypatch):
    session = _DeviceSession()
    monkeypatch.setattr(kobo.ub, "Device", _Device)
    monkeypatch.setattr(kobo.ub, "session", session)
    monkeypatch.setattr(kobo, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(
        kobo.config, "config_kobo_cover_padding_aspect", "kobo_libra_color",
        raising=False,
    )
    monkeypatch.setattr(
        kobo.config, "config_kobo_cover_padding_enabled", True, raising=False,
    )
    monkeypatch.setattr(
        kobo.config, "config_kobo_cover_padding_fill_mode", "edge_mirror",
        raising=False,
    )
    monkeypatch.setattr(
        kobo.config, "config_kobo_cover_padding_color", "", raising=False,
    )
    return SimpleNamespace(app=Flask(__name__), session=session)


def _aspect():
    return kobo._current_padding_settings().target_aspect


def test_recognised_header_wins_over_config_and_registered_device(resolution_harness):
    resolution_harness.session.rows = [_DeviceRow("Kobo Libra Colour")]

    with resolution_harness.app.test_request_context(
        "/kobo/token/v1/library/sync",
        headers={"x-kobo-devicemodel": "Kobo Clara BW"},
    ):
        assert kobo._requesting_device_aspect() == "kobo_clara_bw"
        assert _aspect() == "kobo_clara_bw"

    assert resolution_harness.session.query_count == 0


def test_one_active_registered_kobo_supplies_its_preset(resolution_harness):
    resolution_harness.session.rows = [_DeviceRow("Kobo Clara BW")]

    with resolution_harness.app.test_request_context("/kobo/token/v1/library/sync"):
        assert kobo._requesting_device_aspect() == "kobo_clara_bw"
        assert _aspect() == "kobo_clara_bw"


def test_different_registered_presets_keep_configured_value(resolution_harness):
    resolution_harness.session.rows = [
        _DeviceRow("Kobo Clara BW"),
        _DeviceRow("Kobo Libra Colour"),
    ]

    with resolution_harness.app.test_request_context("/kobo/token/v1/library/sync"):
        assert kobo._requesting_device_aspect() is None
        assert _aspect() == "kobo_libra_color"


def test_same_registered_presets_collapse_to_one_answer(resolution_harness):
    resolution_harness.session.rows = [
        _DeviceRow("Kobo Clara BW"),
        _DeviceRow("KOBO CLARA BW"),
    ]

    with resolution_harness.app.test_request_context("/kobo/token/v1/library/sync"):
        assert kobo._requesting_device_aspect() == "kobo_clara_bw"
        assert _aspect() == "kobo_clara_bw"


@pytest.mark.parametrize("unknown_header", ["Kobo Nia", "junk", "A" * 161])
def test_unrecognised_header_falls_through_to_registry(
    resolution_harness, unknown_header,
):
    resolution_harness.session.rows = [_DeviceRow("Kobo Clara BW")]

    with resolution_harness.app.test_request_context(
        "/kobo/token/v1/library/sync",
        headers={"x-kobo-devicemodel": unknown_header},
    ):
        assert kobo._requesting_device_aspect() == "kobo_clara_bw"
        assert _aspect() == "kobo_clara_bw"

    assert resolution_harness.session.query_count == 1


def test_registry_ignores_other_users_inactive_devices_and_non_kobos(
    resolution_harness,
):
    resolution_harness.session.rows = [
        _DeviceRow("Kobo Clara BW"),
        _DeviceRow("Kobo Libra Colour", user_id=8),
        _DeviceRow("Kobo Libra Colour", active=False),
        _DeviceRow("Kobo Libra Colour", kind="android"),
    ]

    with resolution_harness.app.test_request_context("/kobo/token/v1/library/sync"):
        assert kobo._requesting_device_aspect() == "kobo_clara_bw"
        assert _aspect() == "kobo_clara_bw"


def test_no_request_context_keeps_configured_value_without_query(resolution_harness):
    assert kobo._requesting_device_aspect() is None
    assert _aspect() == "kobo_libra_color"
    assert resolution_harness.session.query_count == 0


def test_device_query_failure_keeps_configured_value_and_does_not_escape(
    resolution_harness,
):
    resolution_harness.session.error = RuntimeError("device registry unavailable")

    with resolution_harness.app.test_request_context("/kobo/token/v1/library/sync"):
        assert kobo._requesting_device_aspect() is None
        assert _aspect() == "kobo_libra_color"

    assert resolution_harness.session.query_count == 1


def test_current_padding_settings_queries_devices_once_per_request_and_re_resolves_next_request(
    resolution_harness,
):
    resolution_harness.session.rows = [_DeviceRow("Kobo Clara BW")]

    with resolution_harness.app.test_request_context("/kobo/token/v1/library/sync"):
        assert [_aspect() for _ in range(12)] == ["kobo_clara_bw"] * 12
        assert resolution_harness.session.query_count == 1

    resolution_harness.session.rows = [_DeviceRow("Kobo Libra Colour")]
    with resolution_harness.app.test_request_context("/kobo/token/v1/library/sync"):
        assert _aspect() == "kobo_libra_color"

    assert resolution_harness.session.query_count == 2
