# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Per-locale "translations are incomplete" notice throttle marker.

The marker records the date the notice was last shown for a locale, so the
banner fires once a day rather than on every page render. :mod:`cps.render_template`
is the only reader and writer; the path lives here so it is stated once.

The marker used to be written to ``/app``. That directory belongs to the
application tree, not to state: it is replaced wholesale on image upgrade, it
does not exist at all on a source or distribution install, and under a rootless
container it is not writable. Off Docker the write raised ``FileNotFoundError``
on every page render for any non-English user — caught and logged by the caller,
so the throttle silently never persisted and the notice was effectively dead.
See issue #1447.

It now lives in the application's configured state directory — the same place
``app.db`` lives — which is ``/config`` in the container image and
``CALIBRE_DBPATH`` elsewhere. This mirrors :mod:`cps.duplicate_notice` (#992);
the two together are the convention for anything this application writes at
runtime.
"""

import os

# Pre-#1447 location. Read-only fallback so a locale that was already throttled
# under the old path doesn't re-fire the notice the first time it renders after
# an upgrade. Nothing writes here any more, and the fallback expires by itself
# when the app tree is replaced.
LEGACY_NOTICE_DIR = "/app"


def _config_dir():
    """The configured, writable state directory (where ``app.db`` lives).

    Imported lazily from :mod:`cps.constants` so this module stays cheap to
    import and easy to unit-test, while still honouring ``CALIBRE_DBPATH``
    instead of hard-coding the container's ``/config``.
    """
    from .constants import CONFIG_DIR

    return CONFIG_DIR


def _notice_basename(lang):
    # lang comes from flask_babel's get_locale(). basename() keeps an
    # unexpected value from escaping the state directory.
    safe_lang = os.path.basename(str(lang)) or "unknown"
    return "cwa_translation_notice_{}".format(safe_lang)


def translation_notice_file(lang):
    """Absolute path of the marker recording when the incomplete-translation
    notice was last shown for ``lang``. This is the only path ever written to."""
    return os.path.join(_config_dir(), _notice_basename(lang))


def legacy_translation_notice_file(lang):
    """Absolute path of the pre-#1447 marker. Read-only compatibility."""
    return os.path.join(LEGACY_NOTICE_DIR, _notice_basename(lang))


def last_notified(lang):
    """The date string recorded for ``lang``, or ``None`` when the notice has
    never been shown. Falls back to the legacy ``/app`` marker so an upgrade
    doesn't re-fire a notice that was already shown today.

    Unreadable markers read as "never shown": the caller's only use for this
    value is deciding whether to flash once more today, and a spurious extra
    notice is a better failure than an exception on every page render.

    ``ValueError`` is caught alongside ``OSError`` because ``lang`` is not a
    trusted value — it is ``current_user.locale``, which the self-service
    profile route stores unvalidated — and an embedded null byte makes ``open``
    raise ``ValueError`` rather than ``OSError``.
    """
    for path in (translation_notice_file(lang), legacy_translation_notice_file(lang)):
        try:
            with open(path, "r") as handle:
                recorded = handle.read().strip()
        except (OSError, ValueError):
            continue
        if recorded:
            return recorded
    return None


def record_notified(lang, date_str):
    """Record that the notice was shown for ``lang`` on ``date_str``.

    Best-effort: a state directory that isn't writable must not turn a page
    render into an error. Returns True when the marker was persisted.

    See :func:`last_notified` for why ``ValueError`` is caught too.
    """
    try:
        with open(translation_notice_file(lang), "w") as handle:
            handle.write(date_str)
        return True
    except (OSError, ValueError):
        return False
