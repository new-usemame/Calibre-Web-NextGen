import re
from pathlib import Path

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

    theme_select = re.search(
        r"config_theme.*?</select>", admin_source, re.DOTALL
    )
    assert theme_select, "admin theme picker not found — did config_theme move?"
    block = theme_select.group(0)

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
