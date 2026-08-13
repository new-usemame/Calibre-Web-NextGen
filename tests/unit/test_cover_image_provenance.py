# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cover image attribution (fork #304).

The boost pass swaps a record's cover for a higher-resolution URL served by
Amazon or Apple Books, but leaves ``record["source"]`` naming the provider that
supplied the *metadata*. The picker grid renders that source as the card's only
label, so a card reading "Hardcover" could be showing an Amazon image with
nothing on screen saying so - which is exactly what the reporter asked about.

These pin the attribution: where the bytes come from is derived from the final
URL, stamped on every record regardless of whether it was boosted, and shown
only when it differs from the record's own provider.
"""

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parents[2])


def _load_cover_booster_module():
    """Load cps/services/cover_booster.py without the package init's side
    effects (CWA login, database bootstrap). Same shim the sibling
    tests/unit/test_cover_booster.py uses."""
    repo_root = Path(REPO_ROOT)
    module_path = repo_root / "cps" / "services" / "cover_booster.py"

    if "cps" not in sys.modules:
        cps_pkg = types.ModuleType("cps")
        cps_pkg.__path__ = [str(repo_root / "cps")]
        constants = types.ModuleType("cps.constants")
        constants.USER_AGENT = "Calibre-Web-NextGen-tests"
        logger_mod = types.ModuleType("cps.logger")
        logger_mod.create = lambda *_a, **_k: types.SimpleNamespace(
            debug=lambda *_args, **_kwargs: None,
            warning=lambda *_args, **_kwargs: None,
            info=lambda *_args, **_kwargs: None,
            error=lambda *_args, **_kwargs: None,
        )
        cps_pkg.constants = constants
        cps_pkg.logger = logger_mod
        sys.modules["cps"] = cps_pkg
        sys.modules["cps.constants"] = constants
        sys.modules["cps.logger"] = logger_mod
        services_pkg = types.ModuleType("cps.services")
        services_pkg.__path__ = [str(repo_root / "cps" / "services")]
        sys.modules["cps.services"] = services_pkg

    spec = importlib.util.spec_from_file_location("cps.services.cover_booster", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["cps.services.cover_booster"] = module
    spec.loader.exec_module(module)
    return module


cover_booster = _load_cover_booster_module()

AMAZON_URL = "https://m.media-amazon.com/images/P/0553213702.01._SCRM_SL2000_.jpg"
APPLE_URL = "https://is1-ssl.mzstatic.com/image/thumb/Publication/abc/2400x2400bb.jpg"
GOOGLE_URL = "https://books.google.com/books/content?id=xyz&printsec=frontcover"


class TestImageOrigin(unittest.TestCase):
    """The classifier reads the URL, not the code path that produced it."""

    def test_amazon_cdn_host_is_amazon(self):
        self.assertEqual(cover_booster.image_origin(AMAZON_URL), "amazon")

    def test_legacy_amazon_host_is_amazon(self):
        url = "https://images-na.ssl-images-amazon.com/images/I/51abc.jpg"
        self.assertEqual(cover_booster.image_origin(url), "amazon")

    def test_apple_artwork_host_is_applebooks(self):
        self.assertEqual(cover_booster.image_origin(APPLE_URL), "applebooks")

    def test_unattributable_host_returns_none(self):
        self.assertIsNone(cover_booster.image_origin(GOOGLE_URL))

    def test_empty_and_non_string_return_none(self):
        self.assertIsNone(cover_booster.image_origin(""))
        self.assertIsNone(cover_booster.image_origin(None))
        self.assertIsNone(cover_booster.image_origin(12345))


class TestStampCoverOrigins(unittest.TestCase):
    """Attribution is stamped on the record, beside the untouched source."""

    def test_amazon_cover_on_hardcover_record_is_stamped(self):
        rec = {"title": "Wuthering Heights", "cover": AMAZON_URL,
               "source": {"id": "hardcover", "description": "Hardcover"}}
        cover_booster.stamp_cover_origins([rec])
        self.assertEqual(rec["cover_origin"], "amazon")
        # The metadata source is deliberately left alone - it is still true.
        self.assertEqual(rec["source"]["id"], "hardcover")

    def test_unattributable_cover_is_not_stamped(self):
        rec = {"title": "Wuthering Heights", "cover": GOOGLE_URL,
               "source": {"id": "google", "description": "Google"}}
        cover_booster.stamp_cover_origins([rec])
        self.assertNotIn("cover_origin", rec)

    def test_non_dict_entries_are_skipped(self):
        records = [None, "nonsense", {"cover": AMAZON_URL}]
        cover_booster.stamp_cover_origins(records)
        self.assertEqual(records[2]["cover_origin"], "amazon")

    def test_boost_disabled_still_attributes(self):
        """A natively-Amazon cover needs a label even with boosting off."""
        rec = {"title": "Wuthering Heights", "cover": AMAZON_URL,
               "source": {"id": "hardcover", "description": "Hardcover"}}
        prior = os.environ.get("CWA_COVER_BOOST")
        os.environ["CWA_COVER_BOOST"] = "0"
        try:
            cover_booster.boost_covers([rec])
        finally:
            if prior is None:
                os.environ.pop("CWA_COVER_BOOST", None)
            else:
                os.environ["CWA_COVER_BOOST"] = prior
        self.assertEqual(rec["cover_origin"], "amazon")

    def test_already_highres_cover_is_attributed(self):
        """_HIGHRES_HINTS skips the boost pass; attribution must not skip too."""
        rec = {"title": "Wuthering Heights", "cover": AMAZON_URL,
               "source": {"id": "hardcover", "description": "Hardcover"}}
        self.assertTrue(any(h in AMAZON_URL for h in cover_booster._HIGHRES_HINTS),
                        "fixture must be a URL the boost pass skips")
        cover_booster.boost_covers([rec])
        self.assertEqual(rec["cover_origin"], "amazon")


class TestImageOriginLabel(unittest.TestCase):
    """The badge is shown only when it adds information."""

    def test_differing_origin_gets_a_label(self):
        label = _label_for("amazon", "hardcover")
        self.assertEqual(label, "Amazon")

    def test_matching_origin_is_suppressed(self):
        self.assertIsNone(_label_for("amazon", "amazon"))

    def test_apple_record_from_applebooks_is_suppressed(self):
        self.assertIsNone(_label_for("applebooks", "applebooks"))

    def test_no_origin_means_no_label(self):
        self.assertIsNone(_label_for(None, "hardcover"))

    def test_unknown_origin_id_has_no_label(self):
        self.assertIsNone(_label_for("someservice", "hardcover"))


def _label_for(origin_id, source_id):
    """Mirror of cover_picker._image_origin_label using the real label map.

    cover_picker.py can't be imported standalone (package-relative imports), so
    the two-line rule is exercised here against cover_booster.IMAGE_ORIGIN_LABELS
    - the single source of truth for the display strings. The source-pin below
    keeps this mirror honest.
    """
    if not origin_id or origin_id == source_id:
        return None
    return cover_booster.IMAGE_ORIGIN_LABELS.get(origin_id)


class TestPickerWiringSourcePin(unittest.TestCase):
    """Pin the picker's use of the helper, since the rule is mirrored above."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "cps/services/cover_picker.py"), encoding="utf-8") as handle:
            self.source = handle.read()

    def test_candidate_carries_image_origin(self):
        self.assertIn("image_origin: Optional[str] = None", self.source)

    def test_provider_candidates_are_labelled(self):
        self.assertIn(
            "image_origin=_image_origin_label(record.get(\"cover_origin\"), source_id)",
            self.source,
        )

    def test_label_helper_suppresses_matching_origin(self):
        self.assertIn("if not origin_id or origin_id == source_id:", self.source)

    def test_label_helper_uses_the_shared_label_map(self):
        self.assertIn("cover_booster.IMAGE_ORIGIN_LABELS.get(origin_id)", self.source)


if __name__ == "__main__":
    unittest.main()
