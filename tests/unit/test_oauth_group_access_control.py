# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for Generic OAuth group-based access control (PR #494, @lduesing).

The feature lets an administrator restrict Generic OAuth/OIDC login to members
of specific identity-provider groups, and choose which token claim carries the
group list (``groups`` by default; Keycloak/Authentik commonly use a custom
claim). These tests pin the three building blocks the feature rests on:

1. ``_normalize_oauth_claim_values`` — an IdP can send a group claim as a JSON
   list, a comma/space-separated string, or omit it. All shapes must reduce to
   a clean ``list[str]`` so downstream comparisons are uniform.
2. ``_oauth_claim_contains_any`` — group comparison must be case-insensitive
   (``Admin`` must satisfy an expected ``admin``).
3. ``_oauth_group_access_denied`` — the authorization decision itself, which is
   the security-critical seam. It MUST fail closed: requiring membership with
   an empty allow-list rejects everyone rather than admitting all.

Plus a source-pin that the gate runs *before* any user is created (a rejected
login must never auto-provision an account), and a real-SQLite migration test
that the three new ``oauthProvider`` columns are added idempotently.
"""

import inspect
import os
import tempfile

import pytest
from sqlalchemy import create_engine, inspect as sa_inspect, text

from cps import oauth_bb, ub

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _normalize_oauth_claim_values — claim payload shapes
# ---------------------------------------------------------------------------
class TestNormalizeClaimValues:
    def test_list_payload(self):
        assert oauth_bb._normalize_oauth_claim_values(["a", "b"]) == ["a", "b"]

    def test_comma_separated_string(self):
        assert oauth_bb._normalize_oauth_claim_values("a, b ,c") == ["a", "b", "c"]

    def test_space_separated_string(self):
        assert oauth_bb._normalize_oauth_claim_values("a   b\tc") == ["a", "b", "c"]

    def test_drops_empty_and_whitespace_entries(self):
        assert oauth_bb._normalize_oauth_claim_values(["a", "  ", "", "b"]) == ["a", "b"]
        assert oauth_bb._normalize_oauth_claim_values("a,,  , b") == ["a", "b"]

    def test_none_and_other_types_become_empty(self):
        assert oauth_bb._normalize_oauth_claim_values(None) == []
        assert oauth_bb._normalize_oauth_claim_values(42) == []
        assert oauth_bb._normalize_oauth_claim_values({"a": 1}) == []

    def test_list_with_non_string_items_is_stringified(self):
        # An IdP that sends numeric group ids must not crash the comparison.
        assert oauth_bb._normalize_oauth_claim_values([1, 2, "x"]) == ["1", "2", "x"]


# ---------------------------------------------------------------------------
# _oauth_claim_contains_any — case-insensitive membership
# ---------------------------------------------------------------------------
class TestClaimContainsAny:
    def test_case_insensitive_match(self):
        assert oauth_bb._oauth_claim_contains_any(["Admin", "users"], ["admin"]) is True
        assert oauth_bb._oauth_claim_contains_any(["admin"], ["ADMIN"]) is True

    def test_no_match(self):
        assert oauth_bb._oauth_claim_contains_any(["users"], ["admin"]) is False

    def test_empty_inputs(self):
        assert oauth_bb._oauth_claim_contains_any([], ["admin"]) is False
        assert oauth_bb._oauth_claim_contains_any(["admin"], []) is False

    def test_any_of_several_expected(self):
        assert oauth_bb._oauth_claim_contains_any(["staff"], ["admin", "staff"]) is True


# ---------------------------------------------------------------------------
# _oauth_group_access_denied — the authorization decision (fail-closed)
# ---------------------------------------------------------------------------
class TestGroupAccessDenied:
    def test_requirement_off_never_denies(self):
        # No requirement → membership is irrelevant, even with no user groups.
        assert oauth_bb._oauth_group_access_denied(False, [], []) is False
        assert oauth_bb._oauth_group_access_denied(False, ["admin"], []) is False

    def test_member_is_allowed(self):
        assert oauth_bb._oauth_group_access_denied(True, ["calibre"], ["calibre"]) is False

    def test_non_member_is_denied(self):
        assert oauth_bb._oauth_group_access_denied(True, ["calibre"], ["other"]) is True

    def test_required_but_empty_allowlist_denies_everyone(self):
        # Fail closed: enabling the requirement with no allowed groups must not
        # silently admit every authenticated directory user.
        assert oauth_bb._oauth_group_access_denied(True, [], ["anything"]) is True
        assert oauth_bb._oauth_group_access_denied(True, [], []) is True

    def test_membership_is_case_insensitive(self):
        assert oauth_bb._oauth_group_access_denied(True, ["Calibre-Users"], ["calibre-users"]) is False

    def test_one_matching_group_among_many_allows(self):
        assert oauth_bb._oauth_group_access_denied(True, ["admins", "readers"], ["readers", "guests"]) is False


# ---------------------------------------------------------------------------
# Source-pin: the gate runs before user creation
# ---------------------------------------------------------------------------
class TestGateOrdering:
    def test_access_gate_precedes_user_creation(self):
        """A rejected login must return BEFORE ``ub.User()`` is instantiated, so
        an unauthorized identity never auto-provisions an account."""
        src = inspect.getsource(oauth_bb.register_user_from_generic_oauth)
        gate = src.index("_oauth_group_access_denied(")
        creation = src.index("ub.User()")
        assert gate < creation, "group access gate must run before user auto-creation"
        # And the rejection path must actually short-circuit (return), not just log.
        gate_block = src[gate:creation]
        assert "return _oauth_failure_redirect(" in gate_block

    def test_configurable_group_claim_used(self):
        """The group claim name must come from provider config, not be hardcoded
        to 'groups' (Keycloak/Authentik use custom claims)."""
        src = inspect.getsource(oauth_bb.register_user_from_generic_oauth)
        assert "generic.get('oauth_group_claim')" in src


# ---------------------------------------------------------------------------
# Migration: the three new columns are added idempotently
# ---------------------------------------------------------------------------
NEW_COLUMNS = {"oauth_group_claim", "oauth_allowed_groups", "oauth_require_group"}

# The prior (pre-#494) migration-managed column set — a realistic upgrade base.
PRIOR_MANAGED = [
    "oauth_base_url", "oauth_authorize_url", "oauth_token_url", "oauth_userinfo_url",
    "oauth_admin_group", "metadata_url", "scope", "username_mapper",
    "email_mapper", "login_button",
]


def _make_db_without_new_columns():
    fd, path = tempfile.mkstemp(suffix="-app.db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    col_defs = [
        "id INTEGER PRIMARY KEY",
        "provider_name VARCHAR",
        "oauth_client_id VARCHAR",
        "oauth_client_secret VARCHAR",
        "active BOOLEAN",
    ] + [f"'{c}' VARCHAR DEFAULT NULL" for c in PRIOR_MANAGED]
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE oauthProvider ({', '.join(col_defs)})"))
    return engine, path


def _columns(engine):
    return {c["name"] for c in sa_inspect(engine).get_columns("oauthProvider")}


class TestGroupColumnsMigration:
    def test_new_columns_added(self):
        engine, path = _make_db_without_new_columns()
        try:
            assert NEW_COLUMNS.isdisjoint(_columns(engine))
            ub.migrate_oauth_provider_table(engine, None)
            assert NEW_COLUMNS.issubset(_columns(engine))
        finally:
            engine.dispose()
            os.unlink(path)

    def test_migration_is_idempotent(self):
        engine, path = _make_db_without_new_columns()
        try:
            ub.migrate_oauth_provider_table(engine, None)
            ub.migrate_oauth_provider_table(engine, None)  # must not raise
            assert NEW_COLUMNS.issubset(_columns(engine))
        finally:
            engine.dispose()
            os.unlink(path)

    def test_group_claim_defaults_to_groups(self):
        engine, path = _make_db_without_new_columns()
        try:
            ub.migrate_oauth_provider_table(engine, None)
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO oauthProvider (provider_name) VALUES ('generic')"))
                row = conn.execute(text(
                    "SELECT oauth_group_claim, oauth_require_group FROM oauthProvider"
                )).fetchone()
            assert row[0] == "groups"      # claim defaults to the conventional 'groups'
            assert not row[1]              # require_group defaults to off (no regression)
        finally:
            engine.dispose()
            os.unlink(path)
