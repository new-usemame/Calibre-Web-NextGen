# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork issue #160 (follow-up) — the v4.0.56 locale
resolver chain landed the ``?lang=xx`` query-param override on the
inbound request, but every link in the returned XML was built with bare
``url_for(...)`` calls and therefore dropped the ``lang`` argument on the
floor. An OPDS client (Readest, KOReader, Kobo) that set
``/opds?lang=hu`` saw a Hungarian root page, clicked any nav entry, and
immediately flipped back to English.

@droM4X's comment on 2026-05-13 captured this as "only the second half of
the task was implemented" — the locale resolver is in, but the response
side of the loop wasn't completed.

Four fixes pinned here:

1. ``@opds.url_defaults`` propagates ``?lang=`` from inbound
   ``request.args`` to every ``url_for(...)`` call in the OPDS
   blueprint. A client that follows the response's nav links keeps the
   language.

2. Pagination ``rel=first|next|previous`` links preserve all inbound
   query args except ``offset`` (was: ``request.path`` + bare
   ``?offset=N`` which clobbered everything).

3. ``/opds/search/<path:query>`` strips the URL query string before
   pulling the search term out of ``RAW_URI`` — otherwise
   ``url_defaults`` adding ``?lang=hu`` would make every search look for
   ``term?lang=hu`` and return zero matches.

4. ``/opds/osd`` advertises the resolved request locale instead of a
   hardcoded ``en-EN``, and its search URL templates compose the
   OpenSearch ``{searchTerms}`` marker correctly relative to whatever
   query string ``url_defaults`` produced.

These tests pin every fix at the source level so a refactor can't
regress.
"""

import inspect

import pytest


@pytest.mark.unit
class TestLangPropagationUrlDefaults:
    """Source-pin: the OPDS blueprint registers a url_defaults callback
    that injects ``lang`` from the inbound request into every url_for
    call. Without this, the locale resolver's per-request ``?lang=``
    behavior is single-use — the next click reverts."""

    def test_url_defaults_callback_registered(self):
        from cps import opds as opds_module

        assert hasattr(opds_module, "_opds_propagate_lang"), (
            "_opds_propagate_lang missing — without it ?lang= drops on every "
            "url_for in the OPDS templates"
        )

    def test_callback_reads_lang_from_request_args(self):
        from cps import opds as opds_module

        src = inspect.getsource(opds_module._opds_propagate_lang)
        assert "request.args.get('lang')" in src, (
            "callback must read from request.args.get('lang'); without that "
            "specific source, the propagation is silently broken"
        )

    def test_callback_does_not_override_explicit_lang(self):
        from cps import opds as opds_module

        src = inspect.getsource(opds_module._opds_propagate_lang)
        # Caller-supplied lang must win; only inject when no lang is in
        # the values dict.
        assert "'lang' in values" in src, (
            "callback must skip injection when caller already supplied lang"
        )

    def test_callback_is_attached_to_opds_blueprint(self):
        """``url_defaults`` callbacks live in ``Blueprint.url_default_functions``
        keyed by ``None`` (blueprint-scoped). The OPDS blueprint must
        register exactly the function we wrote."""
        from cps import opds as opds_module

        all_callbacks = []
        for _key, cbs in opds_module.opds.url_default_functions.items():
            all_callbacks.extend(cbs)
        names = [getattr(cb, "__name__", "") for cb in all_callbacks]
        assert "_opds_propagate_lang" in names, (
            "OPDS blueprint must register _opds_propagate_lang via "
            "@opds.url_defaults — was: %r" % names
        )


@pytest.mark.unit
class TestLangPropagationBehavior:
    """Behavioral tests: drive Flask url_for with and without ``?lang=``
    on the inbound request and assert the resulting URL carries (or does
    not carry) the param as expected."""

    def _build_app(self):
        from flask import Flask, url_for

        app = Flask(__name__)
        from cps import opds as opds_module

        # Register the OPDS blueprint on a clean app so url_defaults
        # fires the same way it does in production.
        app.register_blueprint(opds_module.opds)
        return app, url_for

    def test_url_for_carries_lang_when_present_on_request(self):
        app, url_for = self._build_app()
        with app.test_request_context('/opds?lang=hu'):
            url = url_for('opds.feed_booksindex')
        assert 'lang=hu' in url, (
            "url_for must carry lang=hu through when the inbound request "
            "has ?lang=hu — got %r" % url
        )

    def test_url_for_omits_lang_when_absent(self):
        app, url_for = self._build_app()
        with app.test_request_context('/opds'):
            url = url_for('opds.feed_booksindex')
        assert 'lang=' not in url, (
            "url_for must NOT add lang when none was on the inbound request "
            "— got %r" % url
        )

    def test_explicit_lang_kwarg_wins_over_request_default(self):
        app, url_for = self._build_app()
        with app.test_request_context('/opds?lang=hu'):
            url = url_for('opds.feed_booksindex', lang='de')
        assert 'lang=de' in url and 'lang=hu' not in url, (
            "explicit lang= kwarg must win over request-derived default — "
            "got %r" % url
        )


@pytest.mark.unit
class TestPaginationPreservesQueryArgs:
    """The feed.xml pagination links must preserve every inbound query
    arg (most importantly ``lang``) — the previous template used
    ``request.path + '?offset=N'`` which clobbered them."""

    def test_paged_url_helper_exists(self):
        from cps import opds as opds_module
        assert hasattr(opds_module, "_opds_paged_url"), (
            "_opds_paged_url missing — pagination links will revert to the "
            "broken request.path?offset=N pattern that drops lang="
        )

    def test_paged_url_preserves_lang(self):
        from flask import Flask
        from cps import opds as opds_module

        app = Flask(__name__)
        app.register_blueprint(opds_module.opds)
        with app.test_request_context('/opds/books?lang=hu&offset=20'):
            url = opds_module._opds_paged_url(40)
        assert 'lang=hu' in url, (
            "paged URL must keep lang=hu when paging through; got %r" % url
        )
        assert 'offset=40' in url, "paged URL must update offset; got %r" % url
        # Old offset must be replaced, not duplicated
        assert url.count('offset=') == 1, (
            "paged URL must replace offset, not duplicate it; got %r" % url
        )

    def test_paged_url_first_form_drops_offset(self):
        from flask import Flask
        from cps import opds as opds_module

        app = Flask(__name__)
        app.register_blueprint(opds_module.opds)
        with app.test_request_context('/opds/books?lang=hu&offset=20'):
            url = opds_module._opds_paged_url(None)
        assert 'offset' not in url, (
            "first-form URL must drop offset entirely; got %r" % url
        )
        assert 'lang=hu' in url, (
            "first-form URL must still preserve lang=; got %r" % url
        )

    def test_paged_url_preserves_unrelated_query_args(self):
        from flask import Flask
        from cps import opds as opds_module

        app = Flask(__name__)
        app.register_blueprint(opds_module.opds)
        with app.test_request_context('/opds/books?lang=de&q=foo'):
            url = opds_module._opds_paged_url(10)
        assert 'lang=de' in url, "lost lang on pagination; got %r" % url
        assert 'q=foo' in url, "lost unrelated arg q=foo on pagination; got %r" % url

    def test_feed_template_uses_opds_paged_url_helper(self):
        """Source-pin: the feed.xml template references opds_paged_url for
        every pagination href so the broken pattern can't slip back."""
        import os
        from cps import opds as opds_module

        repo_root = os.path.dirname(os.path.dirname(opds_module.__file__))
        feed_xml_path = os.path.join(repo_root, 'cps', 'templates', 'feed.xml')
        with open(feed_xml_path) as fh:
            tpl = fh.read()
        for rel in ('first', 'next', 'previous'):
            assert ('rel="' + rel + '"') in tpl, (
                "feed.xml lost rel=%s link" % rel
            )
        # Forbid the legacy pattern that drops query args.
        assert "request.script_root + request.path}}?offset=" not in tpl, (
            "feed.xml still uses the bare request.path + ?offset pattern "
            "that drops lang= and other query args"
        )
        assert "opds_paged_url(" in tpl, (
            "feed.xml must use opds_paged_url() helper for pagination links"
        )


@pytest.mark.unit
class TestCcSearchStripsQueryString:
    """``/opds/search/<path:query>`` reads ``RAW_URI`` to recover the
    original search term — but with ``url_defaults`` now appending
    ``?lang=xx`` to every OPDS URL, the search request also gets
    ``?lang=xx`` and the old code captured ``term?lang=xx`` as the
    search term. Strip the query string before splitting."""

    def test_feed_cc_search_strips_query_before_split(self):
        from cps import opds as opds_module

        src = inspect.getsource(opds_module.feed_cc_search)
        # Source-pin: the fix splits on '?' before extracting the term.
        assert ".split('?'" in src or '.split("?"' in src, (
            "feed_cc_search must strip the query string from RAW_URI before "
            "extracting the search term; otherwise ?lang=xx contaminates "
            "every search"
        )


@pytest.mark.unit
class TestOsdReflectsLocale:
    """``/opds/osd`` advertised English regardless of the resolved locale."""

    def test_feed_osd_uses_get_locale(self):
        from cps import opds as opds_module

        src = inspect.getsource(opds_module.feed_osd)
        assert "get_locale()" in src, (
            "feed_osd must reflect get_locale() in the OSD <Language> "
            "element — was hardcoded to 'en-EN'"
        )
        assert "'en-EN'" not in src and '"en-EN"' not in src, (
            "feed_osd still hardcodes 'en-EN' somewhere"
        )


@pytest.mark.unit
class TestSearchUrlTemplateHelper:
    """The context helper that builds the advertised OpenSearch template must
    keep ``?lang=hu`` and add ``query={searchTerms}`` as a further parameter,
    never a second ``?``. Since #2846 it is the query form; the client side of
    that change is driven end to end in test_opds_search_plus_2846.py."""

    def test_helper_merges_the_marker_onto_the_lang_query(self):
        from urllib.parse import parse_qs, urlsplit
        from flask import Flask
        from cps import opds as opds_module

        app = Flask(__name__)
        app.register_blueprint(opds_module.opds)
        with app.test_request_context('/opds?lang=hu'):
            url = opds_module._opds_search_url_template()
        parts = urlsplit(url)
        assert parts.path == '/opds/search', url
        assert url.count('?') == 1, url
        assert parse_qs(parts.query) == {'lang': ['hu'], 'query': ['{searchTerms}']}, url
