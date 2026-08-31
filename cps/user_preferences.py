# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Allowlisted per-user UI preferences stored in User.view_settings.

The public names are stable API identifiers; their storage paths stay an
implementation detail. Registering another boolean preference is intentionally
one line here, then a client hook call where the control lives.
"""

NAMED_BOOLEAN_PREFERENCE_PATHS = {
    "discover_hidden": ("preferences", "discover_hidden"),
    "show_hidden_books": ("preferences", "show_hidden_books"),
    "card_actions_hidden": ("preferences", "card_actions_hidden"),
}


def serialize_named_preferences(user):
    """Return every registered preference as bool or None when never set.

    ``None`` is load-bearing: the SPA uses it to distinguish a new account that
    may need one-time localStorage adoption from an authoritative server-side
    ``False``. Malformed historical data degrades to unset instead of faulting
    /me or being coerced truthy.
    """
    getter = getattr(user, "get_view_property", None)
    result = {}
    for name, (section, prop) in NAMED_BOOLEAN_PREFERENCE_PATHS.items():
        try:
            value = getter(section, prop) if callable(getter) else None
        except Exception:
            value = None
        result[name] = value if type(value) is bool else None
    return result


def set_named_preferences(user, updates):
    """Stage validated preference updates without committing the transaction."""
    setter = getattr(user, "set_view_property", None)
    if not callable(setter):
        raise AttributeError("User preference store is unavailable")
    for name, value in updates.items():
        section, prop = NAMED_BOOLEAN_PREFERENCE_PATHS[name]
        setter(section, prop, value, commit=False)
