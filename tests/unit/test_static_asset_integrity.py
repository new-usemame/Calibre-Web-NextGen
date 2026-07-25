# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for the static-asset integrity bug class (#1095).

Three separate user-visible faults, one root cause: nothing checked that the
files under ``cps/static/`` and the references to them agree. Assets were
shipped that nothing points at, and assets were pointed at that nothing ships.

  * ``cps/static/icon.png`` and ``cps/static/icon.svg`` are the pre-favicon.ico
    app icons. They were carried into the refactored tree and referenced by
    nothing from day one, so every published image hauled ~29 KB of dead
    payload. Reported by @chloeroform (#1095).

  * Every ``<link rel="apple-touch-icon">`` pointed at ``favicon.ico`` with
    ``sizes="140x140"``. iOS wants a PNG, the ICO holds no 140x140 member at
    all, and a correct 180x180 PNG was sitting unused at
    ``static/img/apple-touch-icon.png`` — so "Add to Home Screen" on iOS had
    no usable icon and fell back to a screenshot of the page.

A third fault found by the same audit — ``GET /robots.txt`` is routed at a
static file that has never existed here or upstream, so it always 404s — is
tracked separately: fixing it means choosing a default crawl policy, and this
app ships ``GOOGLE_SITE_VERIFICATION`` support (``cps/admin.py``), so some
operators deliberately want their instance indexed. That is a behaviour
decision, not an asset cleanup.

These tests pin the two fixes above, plus a partial invariant: every *literal*
``url_for('static', filename='…')`` in a Jinja template resolves to a file that
exists. It deliberately does not cover Python-generated references, SPA
references, or dynamic template expressions — those need their own checks.
"""
import os
import re

import flask
import pytest

import cps

CPS_DIR = os.path.dirname(cps.__file__)
STATIC_DIR = os.path.join(CPS_DIR, "static")
TEMPLATE_DIR = os.path.join(CPS_DIR, "templates")

# ``url_for('static', filename=...)`` targets that are deliberately absent from
# a source checkout. Keep this list at exactly one entry — a new "missing but
# referenced" asset must fail the test rather than hide behind an exemption.
#
#   koplugin.zip  the KOReader plugin archive is zipped up by the Dockerfile at
#                 image-build time from koreader/plugins/cwasync.koplugin/ and
#                 dropped into cps/static/. It is correctly absent from a source
#                 tree and correctly present in every published image.
BUILD_TIME_GENERATED = {"koplugin.zip"}

# Matches only *complete* string literals: `filename='x/y.css')`. Concatenated
# expressions (`filename='js/locale-' ~ lang ~ '.js'`) have no closing paren
# straight after the quote and are skipped — their value isn't knowable here.
_STATIC_LITERAL = re.compile(
    r"""url_for\(\s*['"]static['"]\s*,\s*filename\s*=\s*['"]([^'"]+)['"]\s*\)"""
)

_APPLE_TOUCH_ICON = re.compile(
    r"""<link\s+rel=["']apple-touch-icon["']\s+sizes=["'](\d+)x(\d+)["']\s+"""
    r"""href=["']?\{?\{?\s*(?:url_for\(\s*['"]static['"]\s*,\s*filename\s*=\s*"""
    r"""['"]([^'"]+)['"]\s*\)\s*\}?\}?|[^"'>]*?)["']?\s*>"""
)


def _iter_templates():
    for root, _dirs, files in os.walk(TEMPLATE_DIR):
        for name in files:
            if name.endswith((".html", ".xml", ".txt")):
                yield os.path.join(root, name)


def _png_dimensions(path):
    """Width/height straight out of the PNG IHDR chunk — no image library."""
    with open(path, "rb") as handle:
        header = handle.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n", "%s is not a PNG" % path
    return (
        int.from_bytes(header[16:20], "big"),
        int.from_bytes(header[20:24], "big"),
    )


def _apple_touch_icon_declarations():
    """Every apple-touch-icon <link> in the app: (source, sizes, href-target).

    Covers both the Jinja templates and the SPA shell, which injects its own
    <head> tags in Python rather than in a template (cps/spa.py).
    """
    found = []
    for path in _iter_templates():
        with open(path, encoding="utf-8") as handle:
            body = handle.read()
        for match in _APPLE_TOUCH_ICON.finditer(body):
            found.append((path, int(match.group(1)), int(match.group(2)),
                          match.group(3)))

    spa_path = os.path.join(CPS_DIR, "spa.py")
    with open(spa_path, encoding="utf-8") as handle:
        spa_src = handle.read()
    for match in re.finditer(
        r"""apple-touch-icon["']\s+sizes=\\?["'](\d+)x(\d+)\\?["']\s+"""
        r"""href=\\?["']%s/([^"'\\]+)""",
        spa_src,
    ):
        found.append((spa_path, int(match.group(1)), int(match.group(2)),
                      match.group(3)))
    return found


@pytest.mark.unit
def test_pre_favicon_app_icons_are_gone():
    """#1095 — icon.png/icon.svg are dead weight; they must stay deleted."""
    for name in ("icon.png", "icon.svg"):
        path = os.path.join(STATIC_DIR, name)
        assert not os.path.exists(path), (
            "cps/static/%s is the pre-favicon.ico app icon and is referenced "
            "by nothing (#1095). It must not come back." % name
        )


@pytest.mark.unit
def test_favicon_is_still_the_one_that_ships():
    """Guard against over-deleting: favicon.ico is heavily referenced."""
    assert os.path.isfile(os.path.join(STATIC_DIR, "favicon.ico"))


@pytest.mark.unit
def test_apple_touch_icon_declarations_exist():
    declarations = _apple_touch_icon_declarations()
    assert declarations, (
        "no apple-touch-icon <link> found — the regex below has drifted away "
        "from the markup and this whole test file is no longer checking "
        "anything."
    )
    # 8 Jinja templates + the SPA shell injection in cps/spa.py.
    assert len(declarations) >= 9, declarations


@pytest.mark.unit
def test_apple_touch_icon_points_at_a_real_png():
    """iOS cannot use an .ico for a home-screen icon — it must be a PNG that
    exists on disk."""
    for source, _w, _h, target in _apple_touch_icon_declarations():
        assert target.endswith(".png"), (
            "%s declares an apple-touch-icon of %r; iOS requires a PNG, so an "
            ".ico here means 'Add to Home Screen' falls back to a screenshot "
            "of the page." % (source, target)
        )
        assert os.path.isfile(os.path.join(STATIC_DIR, target)), (
            "%s points apple-touch-icon at cps/static/%s, which does not "
            "exist." % (source, target)
        )


@pytest.mark.unit
def test_apple_touch_icon_sizes_match_the_file():
    """A `sizes` that lies about the asset is worse than none — iOS picks the
    icon by the declared size."""
    for source, width, height, target in _apple_touch_icon_declarations():
        actual = _png_dimensions(os.path.join(STATIC_DIR, target))
        assert (width, height) == actual, (
            "%s declares sizes=\"%dx%d\" but cps/static/%s is %dx%d."
            % (source, width, height, target, actual[0], actual[1])
        )


@pytest.mark.unit
def test_spa_shell_serves_the_png_in_a_real_response():
    """Not a source-pin: render the SPA shell through Flask and read the tag
    off the response, so a broken injection is caught even if the source
    string still looks right."""
    import cps.spa as spa_mod

    index = ('<!doctype html><html><head><title>t</title></head>'
             '<body><div id="root"></div></body></html>')
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "index.html"), "w") as handle:
            handle.write(index)
        original_dir, original_env = spa_mod._SPA_DIR, os.environ.get("CWNG_SPA")
        spa_mod._SPA_DIR = tmp
        os.environ["CWNG_SPA"] = "1"
        try:
            app = flask.Flask(__name__)
            app.register_blueprint(spa_mod.spa)
            client = app.test_client()
            body = client.get("/app").get_data(as_text=True)
            assert ('<link rel="apple-touch-icon" sizes="180x180" '
                    'href="/static/img/apple-touch-icon.png">') in body, body

            # And behind a reverse-proxy subpath mount (the #571/#574 class).
            prefixed = client.get(
                "/app", environ_overrides={"SCRIPT_NAME": "/cwa"}
            ).get_data(as_text=True)
            assert 'href="/cwa/static/img/apple-touch-icon.png"' in prefixed
            assert 'href="/static/img/apple-touch-icon.png"' not in prefixed
        finally:
            spa_mod._SPA_DIR = original_dir
            if original_env is None:
                os.environ.pop("CWNG_SPA", None)
            else:
                os.environ["CWNG_SPA"] = original_env


@pytest.mark.unit
def test_classic_template_renders_the_png_tag():
    """Same again for the Jinja side: render the <head> block rather than
    grepping the template source."""
    env = flask.Flask(__name__, template_folder=TEMPLATE_DIR)
    with open(os.path.join(TEMPLATE_DIR, "layout.html"), encoding="utf-8") as h:
        source = h.read()
    head = re.search(r"<link rel=[\"']apple-touch-icon[^>]*>", source).group(0)
    with env.test_request_context("/"):
        rendered = flask.render_template_string(head)
    assert rendered == (
        '<link rel="apple-touch-icon" sizes="180x180" '
        'href="/static/img/apple-touch-icon.png">'
    ), rendered


@pytest.mark.unit
def test_every_literal_static_reference_resolves():
    """The invariant behind all three bugs: a template that names a static
    file must name one that ships."""
    missing = []
    for path in _iter_templates():
        with open(path, encoding="utf-8") as handle:
            body = handle.read()
        for match in _STATIC_LITERAL.finditer(body):
            target = match.group(1)
            if target in BUILD_TIME_GENERATED or target.endswith("/"):
                continue
            if not os.path.exists(os.path.join(STATIC_DIR, target)):
                missing.append("%s -> static/%s"
                               % (os.path.relpath(path, CPS_DIR), target))
    assert not missing, "referenced static assets that do not exist:\n" + "\n".join(
        sorted(missing)
    )


@pytest.mark.unit
def test_build_time_exemption_list_stays_minimal():
    """Every entry here is an asset the tests cannot verify. Adding one hides
    a real 404, so the list is pinned rather than merely documented."""
    assert BUILD_TIME_GENERATED == {"koplugin.zip"}
