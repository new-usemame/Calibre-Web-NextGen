import pytest
import re
from pathlib import Path

# CI selects with `pytest -m "smoke or unit"`, so this file was collected and then
# deselected — the whole #736/#918 theme registry pin has never gated a PR (#921).
pytestmark = pytest.mark.unit

from cps.ui_themes import (
    ALLOWED_THEME_SLUGS,
    DEFAULT_THEME_CODE,
    LEGACY_STANDARD_CODE,
    THEME_CODES,
    config_theme_code,
    config_theme_slug,
    theme_code,
    theme_slug,
)

REPO_ROOT = Path(__file__).parents[2]


def test_backend_theme_registry_matches_frontend():
    frontend_source = (REPO_ROOT / "frontend/src/lib/themes.ts").read_text(encoding="utf-8")
    frontend_slugs = set(re.findall(r"slug:\s*'([^']+)'", frontend_source))

    assert frontend_slugs == set(THEME_CODES.values()) == set(ALLOWED_THEME_SLUGS)


def test_theme_defaults_and_round_trips():
    assert theme_slug(DEFAULT_THEME_CODE) == "dark"
    for slug in ALLOWED_THEME_SLUGS:
        assert theme_slug(theme_code(slug)) == slug


def test_admin_theme_picker_renders_the_shared_registry_not_its_own_numbering():
    """#736: the admin form used to hardcode <option value="0">Light</option> /
    <option value="1">Dark</option> — its own Light=0/Dark=1 numbering, which
    matched neither THEME_CODES (1=dark, 2=light) nor the slugs the SPA stores.
    Picking Light wrote config_theme=0, which reads back as dark, so the form
    reported "Settings saved." and nothing changed. The picker must render from
    the shared registry so it cannot invent a numbering again."""
    admin_source = (REPO_ROOT / "frontend/src/pages/Admin.tsx").read_text(encoding="utf-8")

    # Anchor on the <select> element itself, not on the first "config_theme"
    # anywhere in the file — that also appears in the form-state object, from
    # which a `.*?</select>` run would land on whichever picker happens to come
    # next in the JSX and assert against the wrong one.
    theme_selects = [block for block in re.findall(r"<select[\s\S]*?</select>", admin_source)
                     if "config_theme" in block]
    assert len(theme_selects) == 1, (
        "expected exactly one <select> bound to config_theme, found %d — did the "
        "admin theme picker move or get duplicated?" % len(theme_selects)
    )
    block = theme_selects[0]

    # It must map the shared registry, and hold no hand-written <option> values.
    assert "THEMES.map" in block
    assert not re.search(r"<option\s+value=\"\d", block), (
        "admin theme picker hardcodes numeric option values again: %s" % block
    )


def test_config_theme_reads_the_legacy_light_code_as_light():
    """A 0 in config_theme is not an unmigrated anomaly like it is in User.theme
    — it is what the pre-#736 admin form actively stored for "Light". Reading it
    back as dark would discard the admin's saved choice."""
    assert config_theme_slug(LEGACY_STANDARD_CODE) == "light"
    assert theme_slug(LEGACY_STANDARD_CODE) == "dark"  # User.theme keeps its rule
    assert config_theme_code(LEGACY_STANDARD_CODE) == theme_code("light")


def test_config_theme_round_trips_every_supported_theme():
    for slug in ALLOWED_THEME_SLUGS:
        assert config_theme_slug(theme_code(slug)) == slug


def test_config_theme_falls_back_to_the_default_on_garbage():
    for junk in (None, "", "nonsense", object()):
        assert config_theme_slug(junk) == "dark"
        assert config_theme_code(junk) == DEFAULT_THEME_CODE


# Every place that seeds a new account's theme from the instance default.
#
# NOTE (#921): this list is deliberately no longer the guard. It named three of
# the seven create paths, and "adding a create path without adding it here is
# fine" is precisely how cps/api/auth.py shipped a raw copy while this stayed
# green. The real guard AST-enumerates every ub.User() site and cannot be
# out-of-date by omission — see tests/unit/test_921_theme_seeding_every_create_path.py.
# These three are kept as a cheap, readable smoke check over the paths #918 fixed.
THEME_SEEDING_CREATE_PATHS = (
    ("cps/api/admin.py", "admin_create_user — the New UI admin form"),
    ("cps/admin.py", "_handle_new_user — the classic admin form"),
    ("cps/web.py", "register_post — public self-registration"),
)


@pytest.mark.parametrize("path, description", THEME_SEEDING_CREATE_PATHS)
def test_every_create_path_normalises_config_theme_before_storing_it(path, description):
    """#736: config_theme and User.theme disagree on exactly one value — 0 means
    light in config_theme but reads back as dark in User.theme. So a create path
    that copies config_theme raw gives its users a different theme than the paths
    that normalise, from the same admin setting.

    register_post did exactly that: the two admin paths were fixed to call
    config_theme_code() while it kept `content.theme = getattr(config,
    'config_theme', 1)`, so with an admin-saved Light a self-registered account
    booted dark and an admin-created one booted light.
    """
    source = (REPO_ROOT / path).read_text(encoding="utf-8")

    raw_assignments = re.findall(
        r"^\s*(?:content|new_user|user)\.theme\s*=\s*(.+)$", source, re.MULTILINE
    )
    assert raw_assignments, "no theme seeding found in %s — did it move? (%s)" % (path, description)

    for expr in raw_assignments:
        assert "config_theme_code(" in expr, (
            "%s (%s) stores a theme without normalising through config_theme_code(): "
            "`.theme = %s`. A raw config_theme (legacy 0 = light) becomes a User.theme "
            "of 0, which reads back as dark." % (path, description, expr.strip())
        )
