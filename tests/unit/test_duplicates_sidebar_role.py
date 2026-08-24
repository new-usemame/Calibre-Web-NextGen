# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest


ROOT = Path(__file__).parents[2]


@pytest.mark.unit
def test_duplicates_sidebar_uses_admin_or_edit_not_upload():
    source = (ROOT / "frontend" / "src" / "components" / "Sidebar.tsx").read_text()
    assert "const canEdit = !!me?.role?.edit;" in source
    assert "(canEdit || isAdmin) && showDuplicates" in source
    duplicates_gate = source[source.index("showDuplicates && (") - 80:source.index("showDuplicates && (") + 20]
    assert "canUpload" not in duplicates_gate


@pytest.mark.unit
@pytest.mark.parametrize("admin,edit", [(True, False), (False, True)])
def test_duplicates_api_accepts_the_same_two_roles(admin, edit):
    from cps.api import duplicates as mod
    user = SimpleNamespace(
        is_authenticated=True, is_anonymous=False,
        role_admin=lambda: admin, role_edit=lambda: edit,
    )
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/duplicates"):
        with patch.object(mod, "current_user", user):
            assert inspect.unwrap(mod._require_admin_or_edit)() is None
