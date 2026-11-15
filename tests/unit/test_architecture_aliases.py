# SPDX-License-Identifier: GPL-3.0-or-later
"""Recognize the same 64-bit processors across OS-provided machine names."""
import platform
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask_babel import Babel

from cps import helper
from tests.unit.test_739_sticky_new_ui import _index_app, _HTML_ACCEPT


@pytest.fixture
def translated_context():
    app = Flask(__name__)
    Babel(app)
    with app.app_context():
        yield


@pytest.mark.unit
@pytest.mark.parametrize('machine', ['x86_64', 'aarch64', 'AMD64', 'amd64', 'ARM64', 'arm64'])
def test_supported_processor_alias_does_not_warn(monkeypatch, translated_context, machine):
    monkeypatch.setattr(helper.platform, 'machine', lambda: machine)
    assert helper.check_architecture() is None


@pytest.mark.unit
@pytest.mark.parametrize('machine', ['armv7l', 'i686', 'riscv64', ''])
def test_other_processor_names_keep_the_original_warning(monkeypatch, translated_context, machine):
    monkeypatch.setattr(helper.platform, 'machine', lambda: machine)
    warning = helper.check_architecture()
    assert warning and machine in warning
    assert 'optimized for x86_64 and aarch64' in warning


@pytest.mark.unit
def test_native_processor_uses_the_same_warning_policy(translated_context):
    machine = platform.machine()
    warning = helper.check_architecture()
    if machine.lower() in {'x86_64', 'aarch64', 'amd64', 'arm64'}:
        assert warning is None, (machine, warning)
    else:
        assert warning and machine in warning


@pytest.mark.unit
@pytest.mark.parametrize('machine,expected_warning', [('AMD64', False), ('ARM64', False), ('arm64', False), ('armv7l', True)])
def test_classic_admin_index_flashes_only_for_other_processors(tmp_path, monkeypatch, machine, expected_warning):
    app, web, environment = _index_app(tmp_path)
    Babel(app)
    admin = MagicMock()
    admin.is_authenticated = True
    admin.role_admin.return_value = True
    monkeypatch.setattr(helper.platform, 'machine', lambda: machine)
    try:
        with patch.object(web, 'current_user', admin), \
             patch.object(web, 'render_books_list', return_value='CLASSIC HOME'), \
             patch.object(web.config, 'config_anonbrowse', 1, create=True), \
             patch.object(web.config, 'config_allow_reverse_proxy_header_login', False, create=True):
            client = app.test_client()
            response = client.get('/?cwng_feedback=newui', headers=_HTML_ACCEPT)
            assert response.status_code == 200
            with client.session_transaction() as session:
                warnings = [text for category, text in session.get('_flashes', []) if category == 'cwa_arch_warning']
            assert bool(warnings) is expected_warning, (machine, warnings)
            if warnings:
                assert machine in warnings[0]
    finally:
        environment.undo()
