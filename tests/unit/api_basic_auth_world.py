# SPDX-License-Identifier: GPL-3.0-or-later
"""An /api/v1 app wired the way production wires its auth layers: the api_v1
blueprint with its before_request hooks, cw_login sessions over real ub rows,
the sign-in limiter, and the project's own CSRFProtect instance (``cps.csrf``),
so a CSRF exemption registered on that instance is the one being exercised.
"""
from flask import Flask
from flask_babel import Babel

import cps
from cps import limiter, ub
from cps.api import api_v1
from cps.cw_login import LoginManager


def build_api_app(session, *, secret_key="api-basic-auth-world"):
    """Return a Flask app serving api_v1 with CSRF on, users loaded from ``session``."""
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY=secret_key,
                      WTF_CSRF_ENABLED=True, RATELIMIT_ENABLED=True,
                      RATELIMIT_STORAGE_URI="memory://")
    Babel(app)
    manager = LoginManager(app)
    manager.user_loader(lambda user_id, user_random, session_key:
                        session.get(ub.User, int(user_id)))
    limiter.init_app(app)
    with app.app_context():
        limiter.reset()
    app.register_blueprint(api_v1)
    cps.csrf.init_app(app)      # the instance production initialises
    return app
