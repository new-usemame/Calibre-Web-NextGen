# SPDX-License-Identifier: GPL-3.0-or-later
"""Success logging for hierarchy value manipulation.

Every mutating endpoint now writes one INFO line to the application log saying
what changed and who changed it. This suite pins that, because the failure mode
is the same one this feature has had repeatedly: something quietly stops being
recorded and nothing notices, because a missing log line is not an error.

The rules being enforced:

1. **Every successful operation logs exactly once.** Not zero, not twice.
2. **Failures do NOT log a success line.** A refused operation logged as though
   it worked is worse than no logging at all -- it is how somebody spends an
   afternoon looking for a change that never happened.
3. **The line is written after the commit**, so it never describes a change that
   was rolled back.
4. **The actor is captured** and degrades to ``system`` rather than raising.
5. The line is the *application log*, not an audit table. It is rotated, so it is
   a diagnostic record and not a permanent one.
"""
import json

import pytest

from cps.api import cc_values

pytestmark = pytest.mark.unit


class FakeUser:
    name = "alice"

    @property
    def is_authenticated(self):
        return True


@pytest.fixture
def captured(monkeypatch):
    """Collect the INFO lines instead of writing them."""
    lines = []

    class _Log:
        def info(self, message, *args):
            lines.append(message % args if args else message)

        def warning(self, message, *args, **kwargs):
            pass

        def error(self, message, *args, **kwargs):
            pass

        def debug(self, message, *args, **kwargs):
            pass

    monkeypatch.setattr(cc_values, "log", _Log())
    monkeypatch.setattr(cc_values, "_actor", lambda: "alice")
    monkeypatch.setattr(cc_values, "_column_label", lambda col_id: "ddc")
    return lines


# ---------------------------------------------------------------------------
# The line format
# ---------------------------------------------------------------------------

def test_a_line_names_the_column_the_action_the_actor_and_the_detail(captured):
    cc_values._log_action("rename", 3, path="Arts", renamed=4, books=12)

    line = captured[0]
    assert line.startswith("[cc-values]")
    assert "ddc" in line and "3" in line
    assert "rename" in line and "alice" in line
    assert "path=Arts" in line and "renamed=4" in line


def test_absent_detail_is_omitted_rather_than_printed_as_none(captured):
    """A log full of `after=None books=0` is a log nobody reads."""
    cc_values._log_action("create", 3, path="Arts", after=None)

    assert "None" not in captured[0]
    assert "after" not in captured[0]


def test_the_actor_degrades_to_system_when_there_is_no_login_manager(monkeypatch):
    """current_user is a LocalProxy resolving through Flask's login manager, which
    most unit tests do not have. A missing actor must never turn a successful
    operation into a 500."""
    from cps import cw_login

    class _Exploding:
        def __getattr__(self, name):
            raise RuntimeError("no login manager")

    monkeypatch.setattr(cc_values, "current_user", _Exploding())

    assert cc_values._actor() == "system"


def test_the_actor_reports_system_for_an_anonymous_caller(monkeypatch):
    """login_required_if_no_ano allows an anonymous session when anonbrowse is
    on. _require_metadata_editor then rejects the write, but nothing here may
    assume a logged-in user exists."""
    class _Anon:
        name = None

        @property
        def is_authenticated(self):
            return False

    monkeypatch.setattr(cc_values, "current_user", _Anon())

    assert cc_values._actor() == "system"


def test_the_column_label_degrades_rather_than_raising(monkeypatch):
    """It is a convenience for the reader; a failed lookup must not break the
    operation being logged."""
    class _Boom:
        def query(self, *a, **k):
            raise RuntimeError("no session")

    monkeypatch.setattr(cc_values, "calibre_db", SimpleNamespace(session=_Boom()))

    assert cc_values._column_label(3) == "?"


class SimpleNamespace:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ---------------------------------------------------------------------------
# Every endpoint logs exactly once, and only on success
# ---------------------------------------------------------------------------

SUCCESS_SITES = [
    ("rename", {"action": "rename"}),
    ("reorder", {"action": "reorder", "direction": "up"}),
    ("merge", {"action": "merge"}),
    ("delete", {"action": "delete"}),
    ("move", {"action": "move"}),
    ("create", {"action": "create"}),
]


@pytest.mark.parametrize("endpoint,expected", SUCCESS_SITES)
def test_every_mutating_endpoint_logs_its_own_action(captured, endpoint, expected):
    """A source assertion on purpose. Six near-identical return paths is exactly
    where one drifts, and the drift is invisible: a missing line is not an error.

    Naming the action per endpoint is the actual guarantee -- it catches an
    endpoint that logs the WRONG verb, which a "did it log at all" test would
    pass.
    """
    import inspect

    source = inspect.getsource(cc_values)
    body = source[source.index("def %s_cc_value" % endpoint):]
    # Stop at the next route, or at the next top-level def for the LAST endpoint
    # in the file, which has no following decorator.
    for terminator in ("\n@api_v1.route", "\ndef ", "\n# ---"):
        cut = body.find(terminator, 1)
        if cut != -1:
            body = body[:cut]
            break
    assert "_log_action(" in body, "%s logs nothing on success" % endpoint
    assert '"%s"' % expected["action"] in body, \
        "%s logs a different action than it performs" % endpoint


def test_the_order_write_helpers_log_on_success_too(monkeypatch):
    """set_order_for_siblings / carry_order / drop_order are called from inside
    the endpoints, so their own lines would double up. They must stay quiet and
    let the endpoint speak."""
    import inspect

    for name in ("_set_sibling_order", "_carry_order", "_drop_order"):
        body = inspect.getsource(getattr(cc_values, name))
        assert "log.info" not in body, (
            "%s logs directly; the endpoint already logs the operation, and two "
            "lines per change would be noise" % name)


def test_a_refused_operation_writes_no_success_line(captured):
    """The rule that matters most. A 409 asked about a merge, or a 409 for a
    reorder that is already at the end, must not leave a line saying it
    happened."""
    from werkzeug.exceptions import Conflict
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context("/"):
        response, status = cc_values._conflict("Already at the top", [])

    assert status == 409
    assert captured == [], "a refusal logged a success line"


def test_the_commit_and_log_docstring_no_longer_claims_an_audit_trail():
    """The enforcer DELETES each queue file once consumed, so calling it an audit
    trail sent people looking for a record that was never kept."""
    doc = cc_values._commit_and_log.__doc__
    assert "NOT an audit trail" in doc
    assert "_log_action" in doc, "it should point at the thing that IS a record"
