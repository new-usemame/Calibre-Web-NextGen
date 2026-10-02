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
    "reading_tags_hidden": ("preferences", "reading_tags_hidden"),
    # The original import name helps diagnose filenames on a detail page, but
    # is noisy in previews. Missing means the historical visible behavior.
    "show_original_filename": ("preferences", "show_original_filename"),
    # The classic grid's "Hide shelf badges on covers" toggle already stores
    # this; sharing its path keeps one answer across both UIs (#1254).
    "shelf_badges_hidden": ("cover", "hide_shelf_badges"),
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


def set_checkbox_preference_from_form(user, form, name, presence_field):
    """Apply one classic checkbox only when its form explicitly owns it.

    HTML omits an unchecked checkbox, while partial/older form posts may omit
    the setting entirely. The hidden presence field separates those cases.
    Shared Guest state is never written by the profile form.
    """
    if name not in NAMED_BOOLEAN_PREFERENCE_PATHS:
        raise ValueError("Unknown preference: %s" % name)
    if presence_field not in form or getattr(user, "is_anonymous", False):
        return False
    setter = getattr(user, "set_view_property", None)
    if not callable(setter):
        raise AttributeError("User preference store is unavailable")
    section, prop = NAMED_BOOLEAN_PREFERENCE_PATHS[name]
    setter(section, prop, form.get(name) == "on", commit=False)
    return True
