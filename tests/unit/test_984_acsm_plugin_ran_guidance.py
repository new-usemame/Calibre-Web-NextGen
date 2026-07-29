# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fork #984: the .acsm ingest guidance told a user their ACSM plugin was
missing while that plugin was installed, had run, and had printed the real
reason it failed ("DeACSM v0.0.16: ADE auth is missing or broken").

Root cause: conversion_failure_guidance() chose its message from the file
extension alone. ebook-convert ran without its output being captured (the
old failure branch even documented `e.stderr` as always None), so nothing
in the failure path could tell "no ACSM plugin is installed" apart from
"a plugin ran and failed for its own reason" — two situations that need
opposite advice. The fix carries the converter's own output into the
decision.

Pinned behavior:
  1. _run_converter_streaming() tees: it echoes the converter's output live
     (a silent log during a long conversion is what a hang looks like) while
     keeping a bounded tail, and preserves the deadline + the
     CalledProcessError/TimeoutExpired contract convert_book() already
     handles.
  2. conversion_failure_guidance() surfaces the plugin's own reported reason
     when the output shows an ACSM-capable plugin actually ran, and does not
     claim the plugin is missing.
  3. With no evidence, or with Calibre's bare "No plugin to handle input
     format: acsm", the original install-a-plugin guidance is unchanged —
     the new branch only fires on positive evidence.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import ingest_processor  # noqa: E402


# The reporter's actual log (#984), trimmed. Both signals are present: the
# ValueError that made the old code assume "no plugin", and the plugin's own
# lines proving one was installed and did run.
PLUGIN_RAN_OUTPUT = textwrap.dedent(
    """\
    Traceback (most recent call last):
      File "calibre/ebooks/conversion/plumber.py", line 767, in __init__
    ValueError: No plugin to handle input format: acsm
    DeACSM v0.0.16: Trying to parse file DungeonCrawlerCarl_9798232923594.acsm
    ADE sanity check: Can't parse activation container
    DeACSM v0.0.16: ADE auth is missing or broken
    """
)

# Stock Calibre with no ACSM plugin at all — the #448 case, where telling the
# user to install one is exactly right.
NO_PLUGIN_OUTPUT = textwrap.dedent(
    """\
    Traceback (most recent call last):
      File "calibre/ebooks/conversion/plumber.py", line 767, in __init__
    ValueError: No plugin to handle input format: acsm
    """
)


class TestGuidanceUsesConverterEvidence:
    def test_plugin_ran_guidance_does_not_claim_the_plugin_is_missing(self):
        text = ingest_processor.conversion_failure_guidance(
            "acsm", "Ticket.acsm", converter_output=PLUGIN_RAN_OUTPUT)
        assert text is not None
        # The whole complaint in #984: being told to install what is installed.
        assert "CWA_CALIBRE_USER_PLUGINS" not in text, (
            "told the user to install an ACSM plugin although the converter "
            "output shows one ran"
        )
        assert "ACSM Input plugin) is installed" not in text

    def test_plugin_ran_guidance_surfaces_the_plugins_own_reason(self):
        text = ingest_processor.conversion_failure_guidance(
            "acsm", "Ticket.acsm", converter_output=PLUGIN_RAN_OUTPUT)
        assert "ADE auth is missing or broken" in text, (
            "the plugin's own error is the actionable part and must be echoed"
        )
        assert "Ticket.acsm" in text

    def test_no_plugin_output_keeps_the_install_guidance(self):
        text = ingest_processor.conversion_failure_guidance(
            "acsm", "Ticket.acsm", converter_output=NO_PLUGIN_OUTPUT)
        assert "CWA_CALIBRE_USER_PLUGINS" in text
        assert "Adobe Digital Editions" in text

    def test_absent_evidence_keeps_the_install_guidance(self):
        """No output captured => fall back, never guess the new branch."""
        for output in (None, "", "   "):
            text = ingest_processor.conversion_failure_guidance(
                "acsm", "Ticket.acsm", converter_output=output)
            assert "CWA_CALIBRE_USER_PLUGINS" in text

    def test_backward_compatible_two_arg_call(self):
        """#448's callers pass no evidence; that signature must keep working."""
        text = ingest_processor.conversion_failure_guidance("acsm", "x.acsm")
        assert "CWA_CALIBRE_USER_PLUGINS" in text

    @pytest.mark.parametrize("fmt", ["mobi", "epub", "pdf", "", None])
    def test_other_formats_still_get_no_guidance(self, fmt):
        assert ingest_processor.conversion_failure_guidance(
            fmt, "x.bin", converter_output=PLUGIN_RAN_OUTPUT) is None

    def test_acsm_input_plugin_is_also_recognised(self):
        """DeACSM is not the only ACSM-capable plugin."""
        out = "ACSM Input v1.2: failed to fulfil: server returned E_ADEPT_ERROR"
        text = ingest_processor.conversion_failure_guidance(
            "acsm", "x.acsm", converter_output=out)
        assert "CWA_CALIBRE_USER_PLUGINS" not in text
        assert "E_ADEPT_ERROR" in text


class TestConverterStreamingTee:
    """The evidence has to exist without the log going quiet."""

    def _run(self, script, timeout=30):
        return ingest_processor._run_converter_streaming(
            [sys.executable, "-c", script], env=None, timeout=timeout)

    def test_streams_live_and_returns_the_output(self, capfd):
        captured = self._run("print('CONVERT_LINE_1')\nprint('CONVERT_LINE_2')")
        assert "CONVERT_LINE_1" in captured and "CONVERT_LINE_2" in captured
        # Live echo: the same lines must reach our own stdout, not just the
        # return value, or a long conversion logs nothing until it ends.
        assert "CONVERT_LINE_1" in capfd.readouterr().out

    def test_merges_stderr_so_plugin_messages_are_never_missed(self):
        captured = self._run(
            "import sys; sys.stderr.write('DeACSM v0.0.16: ADE auth is missing\\n')")
        assert "ADE auth is missing" in captured

    def test_nonzero_exit_raises_calledprocesserror_carrying_the_output(self):
        with pytest.raises(subprocess.CalledProcessError) as excinfo:
            self._run("import sys; print('PLUGIN_SAID_THIS'); sys.exit(3)")
        assert excinfo.value.returncode == 3
        assert "PLUGIN_SAID_THIS" in (excinfo.value.output or "")

    def test_deadline_is_enforced_even_when_the_child_is_silent(self):
        """A hung converter prints nothing; the timeout must not depend on
        output arriving, which is how a line-driven deadline check fails."""
        with pytest.raises(subprocess.TimeoutExpired):
            self._run("import time; time.sleep(30)", timeout=2)

    def test_output_tail_is_bounded(self):
        """A chatty conversion must not be buffered without limit."""
        captured = self._run("\n".join(
            [f"print('line {i}')" for i in range(5000)]))
        limit = ingest_processor._CONVERTER_LOG_TAIL_LINES
        assert len(captured.splitlines()) <= limit
        # The tail is what matters — a plugin's error is printed last.
        assert "line 4999" in captured
