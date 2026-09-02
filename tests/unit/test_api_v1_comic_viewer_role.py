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
@pytest.mark.parametrize(("viewer", "download", "allowed"), [
    pytest.param(False, True, False, id="download-only"),
    pytest.param(True, False, True, id="viewer-only"),
    pytest.param(True, True, True, id="viewer-and-download"),
])
def test_comic_content_uses_viewer_not_download_role(
        endpoint, args, viewer, download, allowed):
    from cps.api import comic

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books/197/comic"):
        user = SimpleNamespace(
            role_viewer=lambda: viewer,
            role_download=lambda: download,
        )
        with patch.object(comic, "current_user", user), \
                patch.object(comic, "_comic_file", return_value=("/books/197.cbz", "cbz")) as comic_file, \
                patch.object(comic.os.path, "isfile", return_value=True), \
                patch.object(comic, "_list_pages", return_value=["001.jpg"]), \
                patch.object(comic, "_read_entry", return_value=b"page"):
            if allowed:
                response = inspect.unwrap(getattr(comic, endpoint))(*args)
                status = response.status_code if hasattr(response, "status_code") else response[1]
                assert status == 200
                comic_file.assert_called_once_with(197)
            else:
                with pytest.raises(Forbidden):
                    inspect.unwrap(getattr(comic, endpoint))(*args)
                comic_file.assert_not_called()
