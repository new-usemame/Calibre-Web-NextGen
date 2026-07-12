# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Unit tests for the cover-picker orchestration service."""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

_MISSING = object()


def _restore_binding(mapping, key, original):
    """Restore a sys.modules-style binding to its pre-stub value (or remove)."""
    if original is _MISSING:
        mapping.pop(key, None)
    else:
        mapping[key] = original


def _restore_attr(obj, name, original):
    """Restore an attribute to its pre-stub value (or delete it)."""
    if original is _MISSING:
        if hasattr(obj, name):
            delattr(obj, name)
    else:
        setattr(obj, name, original)


def _load_picker_module():
    """Idempotently top up the cps stub so this test can co-exist with the
    other service tests that import the same parent package."""
    cps_pkg = sys.modules.get("cps")
    if cps_pkg is None:
        cps_pkg = types.ModuleType("cps")
        cps_pkg.__path__ = [str(REPO_ROOT / "cps")]
        sys.modules["cps"] = cps_pkg

    constants = sys.modules.get("cps.constants") or types.ModuleType("cps.constants")
    if not hasattr(constants, "USER_AGENT"):
        constants.USER_AGENT = "Calibre-Web-NextGen-tests"
    sys.modules["cps.constants"] = constants
    cps_pkg.constants = constants

    logger_mod = sys.modules.get("cps.logger") or types.ModuleType("cps.logger")
    if not hasattr(logger_mod, "create"):
        logger_mod.create = lambda *_a, **_k: types.SimpleNamespace(
            debug=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
            info=lambda *_args, **_kwargs: None,
            error=lambda *_args, **_kwargs: None,
        )
    sys.modules["cps.logger"] = logger_mod
    cps_pkg.logger = logger_mod

    # Snapshot the real cps.config binding so the isolated stub below does not
    # leak into other test files sharing this xdist worker (corrupting the real
    # ConfigSQL singleton for any later `from cps import config`). See fix/hardcover.
    _orig_config_sysmod = sys.modules.get("cps.config", _MISSING)
    _orig_pkg_config = getattr(cps_pkg, "config", _MISSING)
    config_mod = sys.modules.get("cps.config") or types.ModuleType("cps.config")
    if not hasattr(config_mod, "get_book_path"):
        config_mod.get_book_path = lambda: "/tmp/library"
    sys.modules["cps.config"] = config_mod
    cps_pkg.config = config_mod

    if "cps.services" not in sys.modules:
        services_pkg = types.ModuleType("cps.services")
        services_pkg.__path__ = [str(REPO_ROOT / "cps" / "services")]
        sys.modules["cps.services"] = services_pkg

    # cover_booster is imported by cover_picker; stub it so picker tests
    # don't pull in real network code.
    booster_spec = importlib.util.spec_from_file_location(
        "cps.services.cover_booster", REPO_ROOT / "cps" / "services" / "cover_booster.py"
    )
    booster_module = importlib.util.module_from_spec(booster_spec)
    sys.modules["cps.services.cover_booster"] = booster_module
    booster_spec.loader.exec_module(booster_module)

    spec = importlib.util.spec_from_file_location(
        "cps.services.cover_picker", REPO_ROOT / "cps" / "services" / "cover_picker.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["cps.services.cover_picker"] = module
    spec.loader.exec_module(module)
    # cover_picker captured its `config` reference during exec_module above, so
    # restoring the globals here keeps this module working while leaving the
    # real cps.config intact for every other test file on the worker.
    _restore_binding(sys.modules, "cps.config", _orig_config_sysmod)
    _restore_attr(cps_pkg, "config", _orig_pkg_config)
    return module


picker = _load_picker_module()


def _fake_provider(provider_id, name, search_results=None, search_raises=None):
    """Build a minimal stand-in for cps.metadata_provider.Metadata
    subclasses. Matches the duck-typing the picker expects."""
    inst = types.SimpleNamespace()
    inst.__id__ = provider_id
    inst.__name__ = name
    if search_raises is not None:
        def search(*args, **kwargs):
            raise search_raises
    else:
        def search(*args, **kwargs):
            return search_results or []
    inst.search = search
    return inst


def _fake_metarecord(provider_id, source_name, title, cover_url, isbn=None):
    """A MetaRecord-shaped dataclass instance. We return real dataclass
    instances so dataclasses.asdict in the picker works the same way it
    does on real provider results."""

    @dataclasses.dataclass
    class _SourceInfo:
        id: str
        description: str
        link: str = ""

    @dataclasses.dataclass
    class _MetaRecord:
        id: str
        title: str
        authors: list
        url: str
        source: _SourceInfo
        cover: str = ""
        publisher: str = ""
        publishedDate: str = ""
        identifiers: dict = dataclasses.field(default_factory=dict)

    return _MetaRecord(
        id=f"{provider_id}-{title[:8]}",
        title=title,
        authors=["Test Author"],
        url=f"https://example.com/{provider_id}/{title}",
        source=_SourceInfo(id=provider_id, description=source_name),
        cover=cover_url,
        publisher="Test Publisher",
        publishedDate="2008-12-05",
        identifiers={"isbn": isbn} if isbn else {},
    )


@pytest.mark.unit
class TestGatherCoverCandidates:
    def test_amazon_highres_candidate_uses_book_isbn_when_amazon_provider_disabled(self):
        providers = [_fake_provider("amazon", "Amazon", search_results=[])]
        highres_url = "https://m.media-amazon.com/images/P/0441172717.01._SCRM_SL2000_.jpg"

        with (
            patch.object(picker.cover_booster, "_AMAZON_CDN_ENABLED", True),
            patch.object(
                picker.cover_booster,
                "_amazon_cdn_cover_for_isbn10",
                return_value=highres_url,
            ) as probe,
        ):
            candidates, statuses = picker.gather_cover_candidates(
                providers=providers,
                query="Dune",
                static_cover="generic_cover.svg",
                locale="en",
                book_isbns=["9780441172719"],
                is_provider_enabled=lambda _provider: False,
            )

        assert [(candidate.source_id, candidate.source_name, candidate.cover_url)
                for candidate in candidates] == [
            ("amazon_highres", "Amazon (high-res)", highres_url),
        ]
        assert candidates[0].candidate_id == "amazon_highres:0441172717"
        assert statuses[0].status == "disabled"
        probe.assert_called_once_with("0441172717")

    @pytest.mark.parametrize("book_isbns", [[], [""], ["not-an-isbn"], ["9791234567896"]])
    def test_amazon_highres_candidate_skips_missing_or_unconvertible_isbn(self, book_isbns):
        with (
            patch.object(picker.cover_booster, "_AMAZON_CDN_ENABLED", True),
            patch.object(picker.cover_booster, "_amazon_cdn_cover_for_isbn10") as probe,
        ):
            candidates, _ = picker.gather_cover_candidates(
                providers=[], query="", static_cover="generic_cover.svg", locale="en",
                book_isbns=book_isbns,
            )

        assert candidates == []
        probe.assert_not_called()

    def test_amazon_highres_candidate_respects_existing_cdn_kill_switch(self):
        with (
            patch.object(picker.cover_booster, "_AMAZON_CDN_ENABLED", False),
            patch.object(picker.cover_booster, "_amazon_cdn_cover_for_isbn10") as probe,
        ):
            candidates, _ = picker.gather_cover_candidates(
                providers=[], query="", static_cover="generic_cover.svg", locale="en",
                book_isbns=["9780441172719"],
            )

        assert candidates == []
        probe.assert_not_called()

    def test_amazon_highres_network_failure_degrades_to_other_candidates(self):
        providers = [
            _fake_provider("hardcover", "Hardcover", search_results=[
                _fake_metarecord("hardcover", "Hardcover", "Dune", "https://example.com/dune.jpg"),
            ]),
        ]
        with (
            patch.object(picker.cover_booster, "_AMAZON_CDN_ENABLED", True),
            patch.object(
                picker.cover_booster,
                "_amazon_cdn_cover_for_isbn10",
                side_effect=TimeoutError("CDN unavailable"),
            ),
            patch.object(picker, "boost_covers", side_effect=lambda records: records),
        ):
            candidates, statuses = picker.gather_cover_candidates(
                providers=providers, query="Dune", static_cover="generic_cover.svg", locale="en",
                book_isbns=["9780441172719"],
            )

        assert [candidate.source_id for candidate in candidates] == ["hardcover"]
        assert statuses[0].status == "ok"

    def test_amazon_highres_candidate_deduplicates_existing_grid_url(self):
        highres_url = "https://m.media-amazon.com/images/P/0441172717.01._SCRM_SL2000_.jpg"
        providers = [
            _fake_provider("amazon", "Amazon", search_results=[
                _fake_metarecord("amazon", "Amazon", "Dune", highres_url),
            ]),
        ]
        with (
            patch.object(picker.cover_booster, "_AMAZON_CDN_ENABLED", True),
            patch.object(picker.cover_booster, "_amazon_cdn_cover_for_isbn10", return_value=highres_url),
            patch.object(picker, "boost_covers", side_effect=lambda records: records),
        ):
            candidates, _ = picker.gather_cover_candidates(
                providers=providers, query="Dune", static_cover="generic_cover.svg", locale="en",
                book_isbns=["9780441172719"],
            )

        assert len(candidates) == 1
        assert candidates[0].source_id == "amazon"

    def test_collects_from_all_enabled_providers(self):
        providers = [
            _fake_provider("alpha", "Alpha", search_results=[
                _fake_metarecord("alpha", "Alpha", "Wuthering Heights",
                                 "https://example.com/alpha/cover.jpg"),
            ]),
            _fake_provider("beta", "Beta", search_results=[
                _fake_metarecord("beta", "Beta", "Wuthering Heights",
                                 "https://example.com/beta/cover.jpg"),
            ]),
        ]
        with patch.object(picker, "boost_covers", side_effect=lambda r: r):
            candidates, statuses = picker.gather_cover_candidates(
                providers=providers, query="Wuthering Heights",
                static_cover="generic_cover.svg", locale="en",
            )
        assert len(candidates) == 2
        sources = {c.source_id for c in candidates}
        assert sources == {"alpha", "beta"}
        assert all(s.status == "ok" for s in statuses)

    def test_disabled_provider_skipped_with_status_marker(self):
        providers = [
            _fake_provider("alpha", "Alpha", search_results=[
                _fake_metarecord("alpha", "Alpha", "Title",
                                 "https://example.com/a.jpg"),
            ]),
            _fake_provider("beta", "Beta", search_results=[]),
        ]
        with patch.object(picker, "boost_covers", side_effect=lambda r: r):
            candidates, statuses = picker.gather_cover_candidates(
                providers=providers, query="Title",
                static_cover="g.svg", locale="en",
                is_provider_enabled=lambda p: p.__id__ != "beta",
            )
        beta_status = next(s for s in statuses if s.id == "beta")
        assert beta_status.status == "disabled"
        assert beta_status.count == 0
        assert all(c.source_id != "beta" for c in candidates)

    def test_failed_provider_classified(self):
        providers = [
            _fake_provider("good", "Good", search_results=[
                _fake_metarecord("good", "Good", "X", "https://e.com/x.jpg"),
            ]),
            _fake_provider("bad", "Bad", search_raises=ConnectionError("nope")),
        ]
        with patch.object(picker, "boost_covers", side_effect=lambda r: r):
            candidates, statuses = picker.gather_cover_candidates(
                providers=providers, query="X",
                static_cover="g.svg", locale="en",
                classify_failure=lambda exc: ("error", str(exc)[:30]),
            )
        bad_status = next(s for s in statuses if s.id == "bad")
        assert bad_status.status == "error"
        good_status = next(s for s in statuses if s.id == "good")
        assert good_status.status == "ok"
        assert len(candidates) == 1

    def test_generic_cover_filtered_out(self):
        # Real providers fall back to a generic SVG when they can't find a
        # cover. Those records should NOT show up in the picker grid.
        providers = [
            _fake_provider("hardcover", "Hardcover", search_results=[
                _fake_metarecord("hardcover", "Hardcover", "Real",
                                 "https://example.com/real.jpg"),
                _fake_metarecord("hardcover", "Hardcover", "Generic",
                                 "/static/generic_cover.svg"),
            ]),
        ]
        with patch.object(picker, "boost_covers", side_effect=lambda r: r):
            candidates, _ = picker.gather_cover_candidates(
                providers=providers, query="Q",
                static_cover="/static/generic_cover.svg", locale="en",
            )
        assert len(candidates) == 1
        assert candidates[0].cover_url.endswith("real.jpg")

    def test_embedded_cover_added_first(self):
        providers = [
            _fake_provider("alpha", "Alpha", search_results=[
                _fake_metarecord("alpha", "Alpha", "T", "https://e.com/x.jpg"),
            ]),
        ]
        embedded_payload = b"\xff\xd8\xff\xe0FAKE"

        class _ExtractedCover:
            def __init__(self):
                self.data = embedded_payload
                self.mime_type = "image/jpeg"

        with patch.object(picker, "boost_covers", side_effect=lambda r: r):
            candidates, _ = picker.gather_cover_candidates(
                providers=providers, query="T",
                static_cover="g.svg", locale="en",
                extract_embedded=_ExtractedCover,
            )
        assert candidates[0].source_id == "embedded"
        assert candidates[0].cover_url.startswith("data:image/jpeg;base64,")

    def test_embedded_callable_returning_none_is_silent(self):
        providers = [
            _fake_provider("alpha", "Alpha", search_results=[
                _fake_metarecord("alpha", "Alpha", "T", "https://e.com/x.jpg"),
            ]),
        ]
        with patch.object(picker, "boost_covers", side_effect=lambda r: r):
            candidates, _ = picker.gather_cover_candidates(
                providers=providers, query="T",
                static_cover="g.svg", locale="en",
                extract_embedded=lambda: None,
            )
        # Only the provider candidate.
        assert len(candidates) == 1
        assert candidates[0].source_id == "alpha"

    def test_year_extracted_from_published_date(self):
        providers = [
            _fake_provider("alpha", "Alpha", search_results=[
                _fake_metarecord("alpha", "Alpha", "T", "https://e.com/x.jpg"),
            ]),
        ]
        with patch.object(picker, "boost_covers", side_effect=lambda r: r):
            candidates, _ = picker.gather_cover_candidates(
                providers=providers, query="T",
                static_cover="g.svg", locale="en",
            )
        assert candidates[0].year == "2008"

    def test_empty_query_returns_only_embedded(self):
        providers = [
            _fake_provider("alpha", "Alpha", search_results=[]),
        ]

        class _ExtractedCover:
            data = b"\xff\xd8\xff\xe0FAKE"
            mime_type = "image/jpeg"

        with patch.object(picker, "boost_covers", side_effect=lambda r: r):
            candidates, _ = picker.gather_cover_candidates(
                providers=providers, query="",
                static_cover="g.svg", locale="en",
                extract_embedded=_ExtractedCover,
            )
        assert len(candidates) == 1
        assert candidates[0].source_id == "embedded"


@pytest.mark.unit
class TestSerialization:
    def test_candidate_to_dict_is_jsonable(self):
        c = picker.CoverCandidate(
            source_id="hardcover", source_name="Hardcover",
            cover_url="https://example.com/x.jpg", title="T",
            width=900, height=1200,
        )
        d = c.to_dict()
        assert d["source_id"] == "hardcover"
        assert d["width"] == 900
        # JSON-roundtrip safety
        import json
        json.dumps(d)
