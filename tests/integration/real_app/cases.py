"""Explicitly invoked in isolation by test_real_app.py."""
import pytest

pytestmark = pytest.mark.integration


def test_real_bootstrap(real_app):
    from cps.reverseproxy import ReverseProxied

    import cps

    assert cps.config.db_configured
    assert real_app.blueprints
    assert isinstance(real_app.wsgi_app, ReverseProxied)
    assert "csrf" in real_app.extensions
    assert real_app.error_handler_spec
    response = real_app.test_client().get("/login")
    assert response.status_code == 200
    assert b"csrf_token" in response.data
