# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared local-session cleanup for every logout surface."""

from flask import session as flask_session

from . import config, oauth_auto_redirect, ub
from .cw_login import current_user, logout_user


def cleanup_local_logout():
    """Clear OAuth-local state, the persisted session, and Flask-Login state."""
    if current_user is not None and current_user.is_authenticated:
        if config.config_login_type in (2, 3):
            try:
                from .oauth_bb import logout_oauth_user
            except ImportError:
                pass
            else:
                logout_oauth_user()
        ub.delete_user_session(current_user.id, flask_session.get('_id', ""))
        logout_user()

    flask_session.pop(oauth_auto_redirect.LOGIN_REDIRECT_COUNT_KEY, None)
    oauth_auto_redirect.clear_auto_redirect_state(flask_session)
    oauth_auto_redirect.clear_provider_oauth_states(flask_session)
