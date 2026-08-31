# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The SPA comic byte paths obey the same viewer role as /show and /read."""
from types import SimpleNamespace
from unittest.mock import patch
import inspect

import flask
import pytest
from werkzeug.exceptions import Forbidden


@pytest.mark.unit
@pytest.mark.parametrize(("endpoint", "args"), [
    ("comic_info", (197,)),
    ("comic_page", (197, 0)),
])
def test_comic_content_rejects_user_without_viewer_before_file_access(endpoint, args):
    from cps.api import comic

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books/197/comic"):
        user = SimpleNamespace(role_viewer=lambda: False)
        with patch.object(comic, "current_user", user), \
                patch.object(comic, "_comic_file") as comic_file:
            with pytest.raises(Forbidden):
                inspect.unwrap(getattr(comic, endpoint))(*args)
    comic_file.assert_not_called()
