# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for fork issue #1556 — `error_or_exception` ate its format args.

`_Logger.error_or_exception` declared `stacklevel` BEFORE `*args`::

    def error_or_exception(self, message, stacklevel=2, *args, **kwargs):

so every printf-style call bound its first value into `stacklevel`::

    log.error_or_exception("Ingest directory not writable: %s", e)
    #                       message ------------------------^  ^-- stacklevel = PermissionError

`logging` then evaluates `while stacklevel > 0`, which raises::

    TypeError: '>' not supported between instances of 'PermissionError' and 'int'

The damage is not a cosmetic log line. In `cps/editbooks.py` the upload
handler *catches* `PermissionError`, logs it, and flashes a message written
for exactly that case ("Ingest folder is not writable. Check your
/cwa-book-ingest volume permissions."). The logger raising mid-handler meant
the flash never ran and the user got an opaque 500 instead of the sentence
that would have told them what to fix. That is what @Thovi98 hit on bare
metal in v4.1.33.

Contract pinned here:

* positional args after `message` are FORMAT args, never `stacklevel`
* `stacklevel=` still works as a keyword (``cps/tasks/mail.py`` relies on it)
* `stacklevel` is keyword-only in the signature, so the ordering cannot regress
* both the debug (`.exception`) and non-debug (`.error`) branches are covered
"""

from __future__ import annotations

import inspect
import logging

import pytest

import cps.logger as cwa_logger


@pytest.fixture
def logger_and_records():
    """A `_Logger` instance plus a handler capturing what it emits."""
    log = cwa_logger.get("test_1556_eoe")
    # `logging.getLogger` returns a plain Logger unless the class was set at
    # creation time; assert we really are exercising the fork's subclass.
    assert isinstance(log, cwa_logger._Logger), (
        "expected cps.logger._Logger, got %r — the subclass is what carries "
        "error_or_exception" % type(log)
    )

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture()
    saved_handlers = list(log.handlers)
    saved_level = log.level
    saved_propagate = log.propagate
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(logging.INFO)
    try:
        yield log, records
    finally:
        log.handlers = saved_handlers
        log.setLevel(saved_level)
        log.propagate = saved_propagate


def test_single_positional_format_arg_does_not_crash(logger_and_records):
    """The exact #1556 shape: one `%s` and one exception value."""
    log, records = logger_and_records
    err = PermissionError(13, "Permission denied")

    # Before the fix this raised TypeError from logging's `while stacklevel > 0`.
    log.error_or_exception("Ingest directory not writable: %s", err)

    assert len(records) == 1
    assert records[0].getMessage() == "Ingest directory not writable: %s" % err


def test_two_positional_format_args_do_not_crash(logger_and_records):
    """`_get_ingest_path` logs two values; both must reach the format string."""
    log, records = logger_and_records
    err = OSError("boom")

    log.error_or_exception("Failed to create ingest directory %s: %s",
                           "/cwa-book-ingest", err)

    assert len(records) == 1
    assert records[0].getMessage() == (
        "Failed to create ingest directory /cwa-book-ingest: %s" % err
    )


def test_stacklevel_still_accepted_as_keyword(logger_and_records):
    """`cps/tasks/mail.py` passes `stacklevel=3`; that must keep working."""
    log, records = logger_and_records
    err = ValueError("mail failed")

    log.error_or_exception(err, stacklevel=3)

    assert len(records) == 1
    assert records[0].getMessage() == str(err)


def test_stacklevel_keyword_combines_with_format_args(logger_and_records):
    """Both features at once — format args positional, stacklevel by keyword."""
    log, records = logger_and_records

    log.error_or_exception("book %d: %s", 42, "unreadable", stacklevel=3)

    assert len(records) == 1
    assert records[0].getMessage() == "book 42: unreadable"


def test_debug_branch_also_survives_positional_args(logger_and_records):
    """At DEBUG the call routes through `.exception`; same contract applies."""
    log, records = logger_and_records
    log.setLevel(logging.DEBUG)
    err = PermissionError(13, "Permission denied")

    try:
        raise err
    except PermissionError:
        log.error_or_exception("Ingest directory not writable: %s", err)

    assert len(records) == 1
    assert records[0].getMessage() == "Ingest directory not writable: %s" % err
    assert records[0].exc_info is not None, "debug branch should attach traceback"


def test_bare_exception_argument_still_logs(logger_and_records):
    """The common `log.error_or_exception(e)` shape keeps working."""
    log, records = logger_and_records
    err = RuntimeError("plain")

    log.error_or_exception(err)

    assert len(records) == 1
    assert records[0].getMessage() == str(err)


def test_stacklevel_is_keyword_only_in_signature():
    """Source-pin: the ordering bug cannot be reintroduced silently.

    `stacklevel` must sit AFTER `*args` (i.e. be KEYWORD_ONLY). If someone
    moves it back in front, positional format args start binding to it again
    and every error path in the app raises TypeError instead of reporting.
    """
    sig = inspect.signature(cwa_logger._Logger.error_or_exception)
    params = sig.parameters

    assert "stacklevel" in params
    assert params["stacklevel"].kind is inspect.Parameter.KEYWORD_ONLY, (
        "stacklevel must be keyword-only so positional args reach *args as "
        "format arguments; got kind=%s" % params["stacklevel"].kind
    )
    assert params["stacklevel"].default == 2

    kinds = [p.kind for p in params.values()]
    var_positional = kinds.index(inspect.Parameter.VAR_POSITIONAL)
    stacklevel_at = list(params).index("stacklevel")
    assert var_positional < stacklevel_at, "*args must precede stacklevel"
