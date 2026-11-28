# SPDX-License-Identifier: GPL-3.0-or-later
"""The support pack's tracebacks.txt digest (fork issue #2728).

Tracebacks are already inside the log files; in a long log they are a handful of
lines among thousands. The digest lifts them out, one entry per place an
exception was raised. It is built from the text that goes into the pack, so in
the sanitised pack it is built from scrubbed text and must carry nothing the
logs do not.
"""

from __future__ import annotations

import zipfile

import pytest

from cps import debug_info

pytestmark = pytest.mark.unit

# A traceback as it appears in a real log: the record line, then the traceback.
AMAZON_503 = (
    '[2026-11-17 08:15:02,100]  ERROR {cps.metadata_provider.amazon:210} Amazon search failed\n'
    'Traceback (most recent call last):\n'
    '  File "/app/calibre-web-automated/cps/metadata_provider/amazon.py", line 206, in search\n'
    '    results.raise_for_status()\n'
    '    ~~~~~~~~~~~~~~~~~~~~~~~~^^\n'
    '  File "/lsiopy/lib/python3.13/site-packages/requests/models.py", line 1026, in raise_for_status\n'
    '    raise HTTPError(http_error_msg, response=self)\n'
    'requests.exceptions.HTTPError: 503 Server Error: Service Unavailable for url: https://www.amazon.com/s?k=a\n'
)


def _digest(*texts):
    digest = debug_info._TracebackDigest()
    for text in texts:
        digest.feed(text)
    return digest.render("calibre-web.log*")


class TestExtraction:
    def test_finds_a_real_traceback_with_its_timestamp_and_exception_line(self):
        found = list(debug_info._extract_tracebacks(AMAZON_503 + "[2026-11-17 08:15:03,000]  INFO {x:1} next\n"))
        assert len(found) == 1
        timestamp, block, exception_line = found[0]
        assert timestamp == "2026-11-17 08:15:02"
        assert block.startswith("Traceback (most recent call last):")
        assert "~~~~~~~~~~~~~~~~~~~~~~~~^^" in block  # indented marker lines stay in the block
        assert exception_line.startswith("requests.exceptions.HTTPError: 503")
        assert "next" not in block

    def test_chained_exceptions_are_two_tracebacks(self):
        chained = (
            '[2026-11-18 10:00:00,000] ERROR {cps.x:1} boom\n'
            'Traceback (most recent call last):\n'
            '  File "/a/one.py", line 1, in first\n'
            '    inner()\n'
            'KeyError: \'k\'\n'
            '\n'
            'During handling of the above exception, another exception occurred:\n'
            '\n'
            'Traceback (most recent call last):\n'
            '  File "/a/two.py", line 2, in second\n'
            '    raise RuntimeError("x")\n'
            'RuntimeError: x\n'
        )
        found = list(debug_info._extract_tracebacks(chained))
        assert [exc for _t, _b, exc in found] == ["KeyError: 'k'", "RuntimeError: x"]

    def test_a_traceback_cut_off_at_the_end_of_the_file_is_still_kept(self):
        cut = ('[2026-11-18 10:00:00,000] ERROR {cps.x:1} boom\nTraceback (most recent call last):\n'
               '  File "/a/one.py", line 1, in first\n    inner()\n')
        found = list(debug_info._extract_tracebacks(cut))
        assert len(found) == 1 and "one.py" in found[0][1]

    def test_text_without_a_traceback_yields_nothing(self):
        assert list(debug_info._extract_tracebacks("[2026-11-18 10:00:00,000] INFO {x:1} fine\n")) == []


class TestGrouping:
    def test_same_raise_site_groups_even_when_the_message_changes(self):
        later = AMAZON_503.replace("08:15:02", "09:40:11").replace("k=a", "k=b").replace("503", "504")
        out = _digest(AMAZON_503, later)
        assert "2 traceback(s) in 1 distinct place(s)" in out
        assert "== 1. requests.exceptions.HTTPError @ models.py:1026 in raise_for_status" in out
        assert "seen 2 time(s), first 2026-11-17 08:15:02, last 2026-11-17 09:40:11" in out
        assert "504 Server Error" in out and "503 Server Error" not in out  # sample = most recent

    def test_different_sites_are_separate_and_most_recent_comes_first(self):
        other = ('[2026-11-19 12:00:00,000] ERROR {cps.y:1} other\nTraceback (most recent call last):\n'
                 '  File "/a/three.py", line 9, in third\n    go()\nValueError: bad\n')
        out = _digest(AMAZON_503, other)
        assert out.index("ValueError @ three.py:9 in third") < out.index("HTTPError @ models.py:1026")

    def test_only_the_most_recent_groups_are_shown(self, monkeypatch):
        monkeypatch.setattr(debug_info, "MAX_TRACEBACK_GROUPS", 3)
        texts = [(f'[2026-11-{10 + i:02d} 12:00:00,000] ERROR {{x:1}} e\nTraceback (most recent call last):\n'
                  f'  File "/a/f{i}.py", line {i}, in fn{i}\n    go()\nValueError: v{i}\n') for i in range(6)]
        out = _digest(*texts)
        assert "6 traceback(s) in 6 distinct place(s)" in out and "3 older place(s) omitted" in out
        assert "f5.py" in out and "f3.py" in out and "f2.py" not in out

    def test_over_long_tracebacks_keep_the_top_and_the_tail(self):
        frames = "".join(f'  File "/a/deep.py", line {n}, in rec\n    rec()\n' for n in range(400))
        text = ('[2026-11-18 10:00:00,000] ERROR {x:1} deep\nTraceback (most recent call last):\n'
                + frames + 'RecursionError: maximum recursion depth exceeded\n')
        out = _digest(text)
        assert "characters omitted" in out
        assert out.rstrip().endswith("RecursionError: maximum recursion depth exceeded")
        assert 'line 0, in rec' in out

    def test_the_file_is_capped(self, monkeypatch):
        monkeypatch.setattr(debug_info, "MAX_TRACEBACK_GROUPS", 1000)
        monkeypatch.setattr(debug_info, "_MAX_TRACEBACKS_FILE_BYTES", 2000)
        texts = [(f'[2026-11-{1 + i % 28:02d} 12:00:00,000] ERROR {{x:1}} e\nTraceback (most recent call last):\n'
                  f'  File "/a/f{i}.py", line {i}, in fn{i}\n    go()\nValueError: v{i}\n') for i in range(60)]
        out = _digest(*texts)
        assert len(out.encode()) <= 2100 and out.rstrip().endswith("[digest truncated]")

    def test_no_tracebacks_says_so_explicitly(self):
        out = _digest("[2026-11-18 10:00:00,000] INFO {x:1} all fine\n")
        assert out.startswith("No tracebacks were found in the included application logs")


class TestInBuiltPack:
    """The digest is built from the text going into the pack, so the sanitised
    pack's digest must carry nothing the scrubbed logs do not."""

    EMAIL, USER, IP, TOKEN = "reader.canary@example.org", "canaryreader", "203.0.113.77", "c0ffee00c0ffee00c0ffee00c0ffee00"

    @pytest.fixture
    def built(self, tmp_path, monkeypatch):
        leaky = (
            '[2026-11-18 10:00:00,000] ERROR {cps.web:1} send failed\n'
            'Traceback (most recent call last):\n'
            '  File "/config/plugins/send.py", line 7, in send\n'
            f'    deliver("{self.EMAIL}", "/kobo/{self.TOKEN}/v1/library/sync")\n'
            f'smtplib.SMTPRecipientsRefused: {{\'{self.EMAIL}\': (550, b\'no such user {self.USER} from {self.IP}\')}}\n'
        )
        (tmp_path / "calibre-web.log").write_text(AMAZON_503 + leaky, encoding="utf-8")
        # A traceback in the access log is not an application error; it must not be digested.
        (tmp_path / "access.log").write_text(
            'Traceback (most recent call last):\n  File "/x/access_only.py", line 1, in a\nOops: access\n', encoding="utf-8")

        class _Cfg:
            config_logfile = str(tmp_path / "calibre-web.log")
            config_access_logfile = str(tmp_path / "access.log")
            config_access_log = 1

            def to_dict(self):
                return {"config_port": 8083}

        monkeypatch.setattr(debug_info, "config", _Cfg())
        monkeypatch.setattr(debug_info, "collect_stats", lambda: {"python": "3"})
        monkeypatch.setattr(debug_info, "_known_usernames", lambda: [self.USER])
        monkeypatch.setattr(debug_info, "_resolve_state_paths", lambda: {})
        monkeypatch.setattr(debug_info, "_resolve_integration_inputs", lambda: {})
        return debug_info

    def _digest(self, built, sanitize):
        with zipfile.ZipFile(built._build_debug_zip(sanitize=sanitize)) as zf:
            assert "tracebacks.txt" in zf.namelist()
            return zf.read("tracebacks.txt").decode()

    def test_sanitised_digest_carries_none_of_the_canaries(self, built):
        text = self._digest(built, True)
        for canary in (self.EMAIL, self.USER, self.IP, self.TOKEN):
            assert canary not in text, f"{canary!r} leaked into tracebacks.txt"
        assert "<email_1>" in text and "/kobo/<token>/" in text  # scrubbed, not just dropped
        assert "SMTPRecipientsRefused" in text and "requests.exceptions.HTTPError" in text

    def test_access_log_tracebacks_are_not_digested(self, built):
        assert "access_only" not in self._digest(built, True)

    def test_unredacted_debug_pack_gets_a_digest_too(self, built):
        text = self._digest(built, False)
        assert self.EMAIL in text  # private pack: raw, like its logs
        assert "2 traceback(s) in 2 distinct place(s)" in text
