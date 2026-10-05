"""Classic navigation renders the configured support policy in both themes."""
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from jinja2 import ChainableUndefined

from cps.services.support_policy import support_policy


class _Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("id") == "top_support":
            self.links.append(attrs)


def _render(theme, policy):
    from cps import jinjia, ub
    app = Flask(__name__, template_folder=str(
        Path(__file__).resolve().parents[2] / "cps" / "templates"
    ))
    app.register_blueprint(jinjia.jinjia)
    environment = app.jinja_env
    environment.undefined = ChainableUndefined
    environment.globals.update(
        _=lambda value: "translated:" + value,
        url_for=lambda endpoint, **_kwargs: "/" + endpoint,
        get_flashed_messages=lambda **_kwargs: [],
        csrf_token=lambda: "test-token",
    )
    return environment.get_template("layout.html").render(
        g=SimpleNamespace(current_theme=theme, google_site_verification=""),
        current_user=ub.User(name="Reader", locale="en", role=0, sidebar_view=0,
                             view_settings={}, kindle_mail=""),
        support_destinations=policy,
        request=SimpleNamespace(path="/", query_string=b""),
        sidebar=[], shelf=[], magic_shelves=[],
    )


@pytest.mark.parametrize("theme", [0, 1])
def test_default_support_is_one_safe_translated_link_in_each_classic_theme(theme):
    html = _render(theme, support_policy(SimpleNamespace()))
    links = _Links(html).links
    assert len(links) == 1
    assert links[0]["href"] == "https://ko-fi.com/calibrewebnextgen"
    assert links[0]["target"] == "_blank"
    assert set(links[0]["rel"].split()) == {"noopener", "noreferrer"}
    assert "translated:Support Calibre-Web NextGen" in html


@pytest.mark.parametrize("theme", [0, 1])
def test_host_support_replaces_project_destination_in_each_classic_theme(theme):
    html = _render(theme, support_policy(SimpleNamespace(
        config_show_project_support=False,
        config_support_url="https://example.invalid/help?a=1&b=2",
        config_support_label="Library <help>",
    )))
    links = _Links(html).links
    assert len(links) == 1
    assert links[0]["href"] == "https://example.invalid/help?a=1&b=2"
    assert "Library &lt;help&gt;" in html
    assert "ko-fi.com/calibrewebnextgen" not in html


@pytest.mark.parametrize("theme", [0, 1])
def test_hidden_support_without_replacement_is_absent_in_each_classic_theme(theme):
    html = _render(theme, support_policy(SimpleNamespace(config_show_project_support=False)))
    assert _Links(html).links == []
    assert "ko-fi.com/calibrewebnextgen" not in html
