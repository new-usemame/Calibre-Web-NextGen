# SPDX-License-Identifier: GPL-3.0-or-later
"""Canary-secret tests for the sanitised support pack (fork issue #312).

The existing debug-pack tests pin individual scrub rules. These tests pin
the property that matters to someone about to paste the zip into a public
issue: seed every place a credential or identifier can enter the pack with
a recognisable canary, build the sanitised zip, and assert that no canary
survives in ANY archive member. A new scrub rule that is forgotten, or a new
member added to the pack without going through the scrubber, fails here.

Behavioural on purpose: it asserts on the produced archive, not on source text.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

KOBO_TOKEN = "c0ffee00c0ffee00c0ffee00c0ffee00"  # 32 hex, same shape as kobo_auth
SMTP_PASSWORD = "canary-smtp-password-4471"
LDAP_PASSWORD = "canary-ldap-bind-9921"
OAUTH_TOKEN = "1//canary-refresh-token"
EMAIL = "reader.canary@example.org"
USERNAME = "canaryreader"
PUBLIC_IP = "203.0.113.77"
BEARER = "eyJcanary.payload.signature01234567"

APP_LOG = (
    f'[2026-10-26 09:00:00]  INFO {{cps.kobo:1729}} sync request /kobo/{KOBO_TOKEN}/v1/library/sync\n'
    f'[2026-10-26 09:00:01]  WARN {{cps.web:2521}} Login failed for user "{USERNAME}" IP-address: {PUBLIC_IP}\n'
    f'[2026-10-26 09:00:02] ERROR {{cps.services.worker:80}} Send to eReader failed for {EMAIL}\n'
    f'[2026-10-26 09:00:03]  INFO {{cps.kobo:851}} Authorization: Bearer {BEARER}\n'
)
ACCESS_LOG = (
    f'{PUBLIC_IP} - - [26/Oct/2026 09:00:00] "GET /kobo/{KOBO_TOKEN}/v1/library/sync HTTP/1.1" 200 512\n'
    f'{PUBLIC_IP} - {USERNAME} [26/Oct/2026 09:00:05] "GET /opds/ HTTP/1.1" 200 1024\n'
)
CANARIES = (KOBO_TOKEN, SMTP_PASSWORD, LDAP_PASSWORD, OAUTH_TOKEN, EMAIL,
            USERNAME, PUBLIC_IP, BEARER)


@pytest.fixture
def seeded_pack_env(tmp_path, monkeypatch):
    from cps import debug_info

    log_path = tmp_path / "calibre-web.log"
    log_path.write_text(APP_LOG, encoding="utf-8")
    access_path = tmp_path / "access.log"
    access_path.write_text(ACCESS_LOG, encoding="utf-8")

    settings = {
        "mail_password": SMTP_PASSWORD,
        "config_ldap_serv_password": LDAP_PASSWORD,
        "mail_gmail_token": json.dumps({"refresh_token": OAUTH_TOKEN}),
        "config_port": 8083,
    }

    class _FakeConfig:
        config_logfile = str(log_path)
        config_access_logfile = str(access_path)
        config_access_log = 1

        def to_dict(self):
            return dict(settings)

    monkeypatch.setattr(debug_info, "config", _FakeConfig())
    monkeypatch.setattr("cps.debug_info.collect_stats",
                        lambda: {"python": "3.12", "platform": "Linux"})
    monkeypatch.setattr(debug_info, "_known_usernames", lambda: [USERNAME, "admin"],
                        raising=False)
    return debug_info


def _members(zip_buf):
    with zipfile.ZipFile(zip_buf) as zf:
        return {n: zf.read(n).decode("utf-8", errors="replace") for n in zf.namelist()}


@pytest.mark.unit
class TestNoCanarySurvivesInSanitisedPack:
    def test_no_canary_in_any_member(self, seeded_pack_env):
        members = _members(seeded_pack_env._build_debug_zip(sanitize=True))
        assert "calibre-web.log" in members and "access.log" in members
        for name, text in members.items():
            for canary in CANARIES:
                assert canary not in text, f"{canary!r} leaked into {name}"

    def test_kobo_token_replaced_in_both_logs(self, seeded_pack_env):
        members = _members(seeded_pack_env._build_debug_zip(sanitize=True))
        assert "/kobo/<token>/v1/library/sync" in members["calibre-web.log"]
        assert "/kobo/<token>/v1/library/sync" in members["access.log"]

    def test_same_account_gets_same_label_across_files(self, seeded_pack_env):
        members = _members(seeded_pack_env._build_debug_zip(sanitize=True))
        app, access = members["calibre-web.log"], members["access.log"]
        label = "<user_1>"
        assert label in app and label in access
        assert "<email_1>" in app

    def test_unsanitised_pack_still_contains_originals(self, seeded_pack_env):
        # The private pack is deliberately raw; guards against the scrubber
        # being applied to it by accident.
        members = _members(seeded_pack_env._build_debug_zip(sanitize=False))
        assert KOBO_TOKEN in members["calibre-web.log"]
        assert EMAIL in members["calibre-web.log"]


@pytest.mark.unit
class TestUsernameLookupFailure:
    def test_pack_says_so_when_names_could_not_be_read(self, seeded_pack_env, monkeypatch):
        monkeypatch.setattr(seeded_pack_env, "_known_usernames", lambda: None)
        members = _members(seeded_pack_env._build_debug_zip(sanitize=True))
        assert "could not be read" in members["SANITIZED.txt"]
        # Pattern-based scrubs still apply with no name list.
        assert KOBO_TOKEN not in members["calibre-web.log"]
        assert EMAIL not in members["calibre-web.log"]


@pytest.mark.unit
class TestLogScrubber:
    def test_short_and_path_like_names_are_not_mangled(self):
        from cps.debug_info import _LogScrubber

        s = _LogScrubber(known_names=["alice", "pi"])
        line = 'GET /alice/logfile via alice.alice by user "alice" and pi'
        out = s(line)
        assert "/alice/logfile" in out and "alice.alice" in out
        assert 'user "<user_1>"' in out
        assert out.endswith(" and pi")  # 2 chars: below the minimum, left alone

    def test_labels_are_case_insensitive_and_per_scrubber(self):
        from cps.debug_info import _LogScrubber

        a, b = _LogScrubber(), _LogScrubber()
        assert a("x Alice@Example.com y alice@example.com") == "x <email_1> y <email_1>"
        assert b("bob@example.com") == "<email_1>"  # labels restart per pack

    def test_kobo_segment_floor_keeps_version_paths(self):
        from cps.debug_info import _sanitize_log_line

        assert _sanitize_log_line("GET /kobo/v1/library") == "GET /kobo/v1/library"


@pytest.mark.unit
class TestNamesThatAreLogVocabulary:
    """Account names that are also words the logs are made of (HTTP methods,
    log levels, the scrubber's own placeholder words, the default accounts)
    must not rewrite those words on every line. Seen on a running build: the
    default "admin" account turned "Enable in Admin -> CWA Settings" into
    "Enable in <user_1> -> CWA Settings"."""

    def test_vocabulary_names_leave_log_text_readable(self):
        from cps.debug_info import _LogScrubber

        s = _LogScrubber(known_names=["get", "token", "info", "admin", "Guest", "kobo", "alice"])
        access = '<ip> - - [x] "GET /kobo/0123456789abcdef0123456789abcdef/v1/library/sync HTTP/1.1" 200'
        assert s(access) == '<ip> - - [x] "GET /kobo/<token>/v1/library/sync HTTP/1.1" 200'
        app = "[t]  INFO {cps.kosync:97} Enable in Admin -> CWA Settings for Guest and Kobo"
        assert s(app) == app
        # A real account name on the same line is still replaced.
        assert s('Login failed for user "alice"') == 'Login failed for user "<user_1>"'

    def test_placeholders_are_never_relabelled(self):
        from cps.debug_info import _LogScrubber

        s = _LogScrubber(known_names=["user", "email", "redacted"])
        line = "x <user_1> <email_1> <redacted> <token>"
        assert s(line) == line


@pytest.mark.unit
class TestEmailForms:
    def test_url_encoded_and_non_ascii_emails(self):
        from cps.debug_info import _LogScrubber

        s = _LogScrubber()
        out = s("GET /search?query=alice%40example.com and bob@bücher.de")
        assert "example.com" not in out and "bücher" not in out, out
        assert "alice" not in out and "bob" not in out, out


@pytest.mark.unit
class TestKnownUsernamesReadsTheUserTable:
    def test_db_failure_returns_none(self, monkeypatch):
        from cps import debug_info, ub

        class _Broken:
            def query(self, *a, **k):
                raise RuntimeError("session in a bad state")

        monkeypatch.setattr(ub, "session", _Broken(), raising=False)
        assert debug_info._known_usernames() is None
