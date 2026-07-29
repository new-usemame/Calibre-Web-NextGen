# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every field the plugin sends must be declared in api.json (#920).

``api.json`` governs the wire through **two independent lua-Spore mechanisms**,
and a field has to satisfy both. Declaring it in one and not the other fails
silently in one direction and loudly in the other. Both have now shipped a bug.

**1. ``payload`` builds the body.** lua-Spore does not send the table the caller
passes; it rebuilds the request body from exactly the keys named in ``payload``
and silently drops everything else (``src/Spore.lua``)::

    if method.payload then
        payload = {}
        for i = 1, #method.payload do
            local v = method.payload[i]
            payload[v] = params[v]
        end
    end

So a field can be set by the client, reviewed in the diff, covered by tests on
both sides, and still never reach the server. That is what happened to #906: it
added ``complete``/``complete_source`` to the push and never touched
``api.json``, so lua-Spore dropped both and the delete-sync it shipped was a
no-op on the wire.

**2. ``required_params`` + ``optional_params`` gate the call.** Before any body
is built, Spore validates the caller's params and *raises* on any name it was
not told to expect (``src/Spore.lua``, ``validate``)::

    local optional_params = method.optional_params or {}
    for param in pairs(params) do
        ...
        assert(found, param .. " is not expected for method " .. caller)
    end

Note the two lists are not related to each other: naming a field in ``payload``
does **not** make it an expected param. #924 declared ``deleted`` /
``delete_source`` in ``payload`` only, so every delete cycle died inside the
plugin with ``deleted is not expected for method push_annotations`` and the push
never left the device — a hard error, where #906's was a silent drop. It escaped
because it was verified server-side over real HTTP, which never exercises
Spore's client-side validation, and because the guard this file added for #906
checked ``payload`` alone.

Neither failure is visible in review — the two files are far apart, and one mode
is silent — so both are pinned here. These tests read the real client source and
the real spec, so they fail when either drifts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PLUGIN = Path(__file__).resolve().parents[2] / "koreader" / "plugins" / "cwasync.koplugin"
CLIENT_LUA = PLUGIN / "CWASyncClient.lua"
API_JSON = PLUGIN / "api.json"

# `self.client:<method>({`  — the opening of the call; the body is then scanned
# by matching braces rather than by regex. A lazy `.*?\}` would stop at the
# first `}` of a nested table and silently miss every key after it, which is
# the exact failure this file exists to prevent.
_CALL_OPEN = re.compile(r"self\.client:(?P<method>\w+)\(\{")
# a `key =` at the top level of that table constructor
_KEY = re.compile(r"^\s*(?P<key>\w+)\s*=", re.MULTILINE)


def _spec():
    return json.loads(API_JSON.read_text(encoding="utf-8"))["methods"]


def _balanced_body(source, start):
    """The text of the table constructor opened at `start` (just past its `{`),
    up to its matching close brace."""
    depth, i = 1, start
    while i < len(source) and depth:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1
    return source[start:i - 1]


def _top_level_keys(body):
    """`key =` names at depth 0, so a nested table's own keys aren't mistaken
    for fields of the request body. Newlines inside nested tables are kept so
    line-anchored matching still lines up."""
    flat, depth = [], 0
    for char in body:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0 or char == "\n":
            flat.append(char)
    return set(_KEY.findall("".join(flat)))


def _client_calls():
    """{method: {keys the client passes}} straight from the plugin source."""
    source = CLIENT_LUA.read_text(encoding="utf-8")
    calls = {}
    for match in _CALL_OPEN.finditer(source):
        body = _balanced_body(source, match.end())
        calls.setdefault(match.group("method"), set()).update(_top_level_keys(body))
    return calls


def test_the_parser_actually_finds_the_calls():
    """Guards the test itself: a regex that matches nothing would pass every
    assertion below while checking nothing."""
    calls = _client_calls()
    assert set(calls) == {
        "update_progress", "get_progress", "pull_annotations", "push_annotations",
    }
    assert "annotations" in calls["push_annotations"]


def test_a_nested_table_is_parsed_by_depth_not_by_the_first_brace():
    """A nested table must not confuse the parser in either direction: its own
    keys are not request fields, and keys declared after it must still be seen.
    A line-anchored scan over the raw body reports `Accept` here, which would
    fail this contract against an api.json that is perfectly correct."""
    source = """
        return self.client:push_annotations({
            document = document,
            headers = {
                Accept = "application/json",
            },
            deleted = deleted,
        })
    """
    match = _CALL_OPEN.search(source)
    keys = _top_level_keys(_balanced_body(source, match.end()))

    assert keys == {"document", "headers", "deleted"}
    assert "Accept" not in keys, "a nested table's own key is not a request field"


def _expected_params(spec):
    """The names Spore's ``validate`` will accept: required ∪ optional. Any other
    param the client passes makes the call raise before a request is built."""
    return set(spec.get("required_params", [])) | set(spec.get("optional_params", []))


def test_push_annotations_declares_the_delete_fields():
    """The #920 fix rides on these two; undeclared, they never leave the device.

    Both lists matter and for different reasons: absent from ``payload`` they are
    dropped from the body, absent from the param lists the whole call raises."""
    spec = _spec()["push_annotations"]
    for field in ("deleted", "delete_source"):
        assert field in spec["payload"], f"{field} would be dropped from the body"
        assert field in _expected_params(spec), (
            f"{field} would make every delete-cycle push raise "
            f"'{field} is not expected for method push_annotations'"
        )


@pytest.mark.parametrize("method", sorted(_spec()))
def test_every_field_the_client_sends_is_declared_in_the_payload(method):
    spec = _spec()[method]
    if "payload" not in spec:
        pytest.skip(f"{method} sends no body")
    sent = _client_calls().get(method, set())
    undeclared = sent - set(spec["payload"])
    assert not undeclared, (
        f"{method} passes {sorted(undeclared)}, which api.json does not list in "
        f"`payload` — lua-Spore will drop them and the server will never see "
        f"them. Add them to api.json's payload list."
    )


@pytest.mark.parametrize("method", sorted(_spec()))
def test_every_field_the_client_sends_is_an_expected_param(method):
    """The #924 half of the contract. Spore validates the caller's params before
    it builds anything, so an undeclared name is not dropped — it raises inside
    the plugin and the request never reaches us."""
    spec = _spec()[method]
    sent = _client_calls().get(method, set())
    unexpected = sent - _expected_params(spec)
    assert not unexpected, (
        f"{method} passes {sorted(unexpected)}, which api.json lists in neither "
        f"`required_params` nor `optional_params` — lua-Spore raises "
        f"'<field> is not expected for method {method}' and the call never goes "
        f"out. Add them to api.json's optional_params list."
    )


@pytest.mark.parametrize("method", sorted(_spec()))
def test_every_payload_field_is_also_an_expected_param(method):
    """Spec-level pin, independent of what the client happens to send today.

    ``payload`` and the param lists are unrelated in Spore, so a field can be
    declared for the body while remaining unexpected as a param — the exact
    shape of #924. Declaring a body field the caller cannot legally pass is
    always a mistake, so it is caught here at the spec rather than waiting for a
    client change to expose it."""
    spec = _spec()[method]
    if "payload" not in spec:
        pytest.skip(f"{method} sends no body")
    undeclared = set(spec["payload"]) - _expected_params(spec)
    assert not undeclared, (
        f"{method} lists {sorted(undeclared)} in `payload` but in neither "
        f"`required_params` nor `optional_params`. Any caller passing them hits "
        f"Spore's validate() and raises; any caller omitting them sends null. "
        f"Add them to optional_params."
    )
