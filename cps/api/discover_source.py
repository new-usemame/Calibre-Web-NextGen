# SPDX-License-Identifier: GPL-3.0-or-later
"""The signed-in account's Discover source preference."""
from flask import jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from . import api_v1
from .account import _require_real_user
from .. import logger, ub, user_library
from ..cw_login import current_user
from ..services import discover_source

log = logger.create()


@api_v1.route("/account/discover-source", methods=["GET", "PUT"])
def account_discover_source():
    guard = _require_real_user()
    if guard is not None:
        return guard
    user_library.mark_response_user_specific()
    try:
        if request.method == "PUT":
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or set(body) != {"source"}:
                return jsonify({"error": {"code": "invalid_request", "message": "source is required"}}), 400
            source = discover_source.validate_source(body["source"], current_user)
            current_user.set_view_property("discover", "source", source, commit=False)
            ub.session.commit()
            discover_source.clear_filter_cache()
        return jsonify(discover_source.settings_payload(current_user))
    except ValueError as exc:
        return jsonify({"error": {"code": "invalid_discover_source", "message": str(exc)}}), 400
    except SQLAlchemyError:
        ub.session.rollback()
        log.exception("Could not load or save Discover source")
        return jsonify({"error": {"code": "source_unavailable", "message": "Could not load or save Discover source"}}), 503
