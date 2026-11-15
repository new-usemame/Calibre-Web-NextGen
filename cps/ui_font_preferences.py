# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated UI font defaults for new accounts.

The stored values are registry keys, never CSS. The matching option labels and
stacks live in ``frontend/src/lib/fonts.ts``; this module is the server-side
allowlist used both by admin configuration and account creation.
"""

ALLOWED_UI_FONT_BODY = frozenset({"", "system-sans", "serif", "mono"})
ALLOWED_UI_FONT_DISPLAY = frozenset({"", "system-sans", "serif", "mono"})

DEFAULT_FONT_FIELDS = {
    "config_default_ui_font_body": ("ui_font_body", ALLOWED_UI_FONT_BODY),
    "config_default_ui_font_display": ("ui_font_display", ALLOWED_UI_FONT_DISPLAY),
}


def validate_default_font_updates(data):
    """Return present default-font updates after validating the entire pair.

    Call this before applying any other configuration changes so an invalid
    token cannot leave a partially changed in-memory or persisted config.
    """
    updates = {}
    for config_field, (_user_field, allowed) in DEFAULT_FONT_FIELDS.items():
        if config_field not in data:
            continue
        value = data[config_field]
        if not isinstance(value, str) or value not in allowed:
            label = "body" if config_field.endswith("body") else "display"
            raise ValueError("Invalid default %s font option" % label)
        updates[config_field] = value
    return updates


def seed_new_user_ui_font_defaults(user, settings=None):
    """Copy the current validated instance font defaults onto a new user.

    Existing users never pass through this function when an admin changes the
    defaults. A bad hand-edited legacy setting degrades to the empty preset,
    which means the shipped theme default, and cannot inject arbitrary CSS.
    """
    if settings is None:
        from . import config
        settings = config
    for config_field, (user_field, allowed) in DEFAULT_FONT_FIELDS.items():
        value = getattr(settings, config_field, "")
        setattr(user, user_field, value if isinstance(value, str) and value in allowed else "")
