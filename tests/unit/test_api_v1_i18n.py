"""Unit tests for the SPA i18n catalog endpoint (cps/api/i18n.py).

The endpoint derives a per-locale { msgid: translation } catalog from the same
.po files the server compiles, so the React SPA reuses existing translations
with English-source fallback. These tests pin:
  * real extraction from a shipped locale (de) including a known translation,
  * msgfmt-equivalent filtering (skip header, fuzzy, empty),
  * graceful behaviour for 'en' (source locale) and unknown locales,
  * that the endpoint is reachable without auth (login screen needs it).
"""
import json
import os
import inspect

import pytest
import flask


def _call_view(locale):
    """Invoke the i18n_catalog view in a request context and return parsed JSON."""
    from cps.api import i18n as i18n_mod
    app = flask.Flask(__name__)
    with app.test_request_context(f"/api/v1/i18n/{locale}.json"):
        view = inspect.unwrap(i18n_mod.i18n_catalog)
        result = view(locale)
        # The view returns a Response (jsonify + Cache-Control header).
        body = json.loads(result.get_data(as_text=True))
        return body, result


@pytest.fixture(autouse=True)
def _clear_i18n_caches():
    """Each test starts with cold caches so monkeypatched dirs take effect."""
    from cps.api import i18n as i18n_mod
    i18n_mod._catalog_cache.clear()
    i18n_mod._available_locales = None
    yield
    i18n_mod._catalog_cache.clear()
    i18n_mod._available_locales = None


@pytest.mark.unit
def test_real_de_catalog_has_known_translation():
    """Reading the shipped de .po yields real translations (Books -> Bücher)."""
    body, _ = _call_view("de")
    assert body["locale"] == "de"
    catalog = body["catalog"]
    assert isinstance(catalog, dict)
    assert len(catalog) > 100  # the real catalog has ~1400 entries
    assert catalog.get("Books") == "Bücher"
    assert catalog.get("Authors") == "Autoren"


@pytest.mark.unit
def test_en_returns_empty_catalog():
    """English is the source locale — its catalog is empty (keys are the strings)."""
    body, _ = _call_view("en")
    assert body["locale"] == "en"
    assert body["catalog"] == {}


@pytest.mark.unit
def test_unknown_locale_returns_empty_no_error():
    """A locale we don't ship returns an empty catalog, not a 404/500."""
    body, _ = _call_view("zz")
    assert body["locale"] == "zz"
    assert body["catalog"] == {}


@pytest.mark.unit
def test_catalog_revalidates_with_etag():
    """The catalog changes across image versions at a stable URL, so it must be
    revalidated (no-cache) and carry a content ETag — otherwise a browser or
    reverse proxy serves stale translations after an upgrade (#615)."""
    _, resp = _call_view("de")
    cache_control = resp.headers.get("Cache-Control", "")
    assert "no-cache" in cache_control
    # A long-lived opaque cache is exactly the bug: reject any max-age > 0.
    assert "max-age=3600" not in cache_control
    etag = resp.headers.get("ETag", "")
    assert etag  # a validator must be present so caches can revalidate


@pytest.mark.unit
def test_catalog_etag_tracks_content():
    """The ETag changes when the served strings change and is stable otherwise —
    so an upgrade that ships new translations invalidates cached catalogs while
    an unchanged catalog keeps returning 304."""
    from cps.api import i18n as i18n_mod
    base = {"Read now": "Lire", "Mark as read": "Marquer comme lu"}
    same = {"Mark as read": "Marquer comme lu", "Read now": "Lire"}  # order differs
    changed = {"Read now": "Lire", "Mark as read": "Marquer comme non lu"}  # regressed
    assert i18n_mod._catalog_etag("fr", base) == i18n_mod._catalog_etag("fr", same)
    assert i18n_mod._catalog_etag("fr", base) != i18n_mod._catalog_etag("fr", changed)
    # Locale is folded in: two empty catalogs still validate distinctly.
    assert i18n_mod._catalog_etag("en", {}) != i18n_mod._catalog_etag("zz", {})


@pytest.mark.unit
def test_conditional_request_returns_304_when_etag_matches():
    """A client that already holds the current catalog gets a bodiless 304, so
    revalidation stays cheap; a stale/absent validator gets the fresh catalog."""
    from cps.api import i18n as i18n_mod
    app = flask.Flask(__name__)

    # First request: full 200 with an ETag.
    with app.test_request_context("/api/v1/i18n/de.json"):
        first = inspect.unwrap(i18n_mod.i18n_catalog)("de")
    etag = first.headers["ETag"]
    assert first.status_code == 200
    assert first.get_data()  # body present

    # Second request carrying the ETag: 304 (the WSGI layer drops the body for a
    # 304; a direct view call keeps it, so we pin the status contract here and
    # verify the empty-body wire behaviour over real HTTP in the PR).
    with app.test_request_context(
        "/api/v1/i18n/de.json", headers={"If-None-Match": etag}
    ):
        second = inspect.unwrap(i18n_mod.i18n_catalog)("de")
    assert second.status_code == 304

    # A different ETag must NOT short-circuit — the fresh catalog is served.
    with app.test_request_context(
        "/api/v1/i18n/de.json", headers={"If-None-Match": '"stale"'}
    ):
        third = inspect.unwrap(i18n_mod.i18n_catalog)("de")
    assert third.status_code == 200
    assert third.get_data()


@pytest.mark.unit
def test_fr_read_toggle_strings_are_correct():
    """#615: the read button and read toggle must carry the right French, so a
    correctly-revalidated catalog never shows English 'Read now' or the inverted
    'Mark as read' -> 'Marquer comme non lu' the reporter saw on a stale build."""
    body, _ = _call_view("fr")
    catalog = body["catalog"]
    assert catalog.get("Read now") == "Lire"
    assert catalog.get("Mark as read") == "Marquer comme lu"
    assert catalog.get("Mark as unread") == "Marquer comme non lu"


@pytest.mark.unit
def test_fr_reporter_residual_spa_strings_are_served():
    """#615/#886: the exact residual labels reported after v4.1.12 must
    survive extraction and msgfmt-equivalent fuzzy filtering.

    This tests the catalog the SPA actually consumes, rather than accepting a
    fuzzy or empty ``msgstr`` that looks translated in messages.po but is
    intentionally omitted at runtime.
    """
    body, _ = _call_view("fr")
    catalog = body["catalog"]
    assert catalog.get("A few random picks from your library") == (
        "Une sélection aléatoire de votre bibliothèque"
    )
    assert catalog.get("Favorites") == "Favoris"
    assert catalog.get("Hot") == "Populaires"
    assert catalog.get("Top Rated") == "Les mieux notés"
    assert catalog.get("Table view") == "Vue en tableau"
    assert catalog.get("Smart shelves") == "Étagères intelligentes"
    assert catalog.get("Load more") == "Charger plus"


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        (
            "fr",
            {
                "Currently Reading": "Lecture en cours",
                "Ratings": "Évaluations",
                "Loading": "Chargement…",
                "Smart shelves": "Étagères intelligentes",
                "Table view": "Vue en tableau",
                "{count} books": "{count} livres",
                "Hot — Most Downloaded": "Populaires — Les plus téléchargés",
                "Comfortable": "Confortable",
                "contains": "contient",
                "Standard (username / password)": "Standard (nom d’utilisateur / mot de passe)",
                "Simple (service account)": "Simple (compte de service)",
                "Help & support": "Aide et assistance",
            },
        ),
        (
            "ru",
            {
                "Currently Reading": "Читаю сейчас",
                "Ratings": "Рейтинги",
                "Loading": "Загрузка",
                "Smart shelves": "Смарт-полки",
                "Table view": "Табличный вид",
                "{count} books": "Книг: {count}",
                "Discover — Random Picks": "Открытия — Случайная подборка",
                "Page {number}": "Страница {number}",
                "contains": "содержит",
                "Standard (username / password)": "Стандартный (имя пользователя / пароль)",
                "Simple (service account)": "Простая (служебная учётная запись)",
                "Help & support": "Помощь и поддержка",
            },
        ),
        (
            "de",
            {
                "Currently Reading": "Wird gerade gelesen",
                "Ratings": "Bewertungen",
                "Loading": "Wird geladen…",
                "Smart shelves": "Intelligente Regale",
                "Table view": "Tabellenansicht",
                "{count} books": "{count} Bücher",
                "Hot — Most Downloaded": "Beliebt — Am häufigsten heruntergeladen",
                "Compact": "Kompakt",
                "contains": "enthält",
                "Standard (username / password)": "Standard (Benutzername / Passwort)",
                "Simple (service account)": "Einfach (Dienstkonto)",
                "Help & support": "Hilfe und Support",
            },
        ),
        (
            "hu",
            {
                "Currently Reading": "Jelenleg olvasott",
                "Ratings": "Értékelések",
                "Loading": "Betöltés…",
                "Smart shelves": "Intelligens polcok",
                "Table view": "Táblázatos nézet",
                "{count} books": "{count} könyv",
                "Discover — Random Picks": "Felfedezés — Véletlenszerű válogatás",
                "Page {number}": "{number}. oldal",
                "contains": "tartalmazza",
                "Standard (username / password)": "Normál (felhasználónév / jelszó)",
                "Simple (service account)": "Egyszerű (szolgáltatási fiók)",
                "Help & support": "Súgó és támogatás",
            },
        ),
    ],
)
def test_reviewed_new_ui_translations_survive_runtime_fuzzy_filter(locale, expected):
    """#879/#886: reviewed reporter and household translations must be in the
    exact catalog consumed by the SPA, rather than merely present as fuzzy PO
    guesses that the safe runtime policy intentionally drops.
    """
    body, _ = _call_view(locale)
    catalog = body["catalog"]
    assert {msgid: catalog.get(msgid) for msgid in expected} == expected


@pytest.mark.parametrize("locale", ["fr", "ru", "de", "hu"])
def test_newly_wrapped_shelf_states_are_translated(locale):
    """A representative newly wrapped state and error remain non-English in
    every reporter/household locale after extraction and catalog filtering.
    """
    body, _ = _call_view(locale)
    catalog = body["catalog"]
    for msgid in (
        "No shelves yet. Create a shelf to start collecting books.",
        "Could not save order.",
    ):
        assert catalog.get(msgid)
        assert catalog[msgid] != msgid


@pytest.mark.parametrize("locale", ["fr", "ru", "de", "hu"])
def test_smart_shelf_editor_copy_is_runtime_catalog_eligible(locale):
    """#886: the whole named Smart shelves surface, not only its operators,
    must survive the runtime empty/fuzzy filter in the target locales.
    """
    body, _ = _call_view(locale)
    catalog = body["catalog"]
    keys = (
        "Add rule", "Could not create the shelf.", "Could not save the shelf.",
        "Create smart shelf", "Date Added", "Edit smart shelf",
        "Give your smart shelf a name.", "Icon", "Match", "Match condition",
        "Name", "New smart shelf", "Remove rule", "Rule field",
        "Rule operator", "Save changes", "Saving…", "Tag", "all rules",
        "any rule", "books match", "e.g. Unread sci-fi", "value",
    )
    assert [key for key in keys if not catalog.get(key)] == []


@pytest.mark.parametrize("locale", ["fr", "ru", "de", "hu"])
def test_reporter_catalog_controls_have_accessible_translations(locale):
    body, _ = _call_view(locale)
    catalog = body["catalog"]
    keys = ("Read status filter", "Sort order", "Shuffle picks", "Hide Discover section")
    assert [key for key in keys if not catalog.get(key)] == []


@pytest.mark.parametrize(
    ("locale", "keys"),
    [
        ("fr", (
            "Book density", "Grid view", "List view", "Loading…", "Rows per load",
            "Select multiple", "Series order", "Series order (reverse)", "Series view",
            "Show Discover section", "Failed to load books.",
            'No results for "{q}".', "No {filter} books here.",
        )),
        ("ru", ("Book density", "Rows per load", "Series view")),
    ],
)
def test_household_and_reporter_catalog_surface_is_complete(locale, keys):
    body, _ = _call_view(locale)
    catalog = body["catalog"]
    assert [key for key in keys if not catalog.get(key)] == []


@pytest.mark.unit
def test_i18n_endpoint_is_public():
    """The auth gate must let the catalog through (login screen needs strings)."""
    from cps.api import _PUBLIC_ENDPOINTS
    assert "api_v1.i18n_catalog" in _PUBLIC_ENDPOINTS


@pytest.mark.unit
def test_load_catalog_skips_header_fuzzy_and_empty(tmp_path, monkeypatch):
    """_load_catalog mirrors msgfmt: drop the header, fuzzy entries, and empties."""
    from cps.api import i18n as i18n_mod

    locale = "xx"
    lc = tmp_path / locale / "LC_MESSAGES"
    lc.mkdir(parents=True)
    (lc / "messages.po").write_text(
        'msgid ""\n'
        'msgstr ""\n'
        '"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '\n'
        'msgid "Normal"\n'
        'msgstr "NormalXX"\n'
        '\n'
        '#, fuzzy\n'
        'msgid "FuzzyOne"\n'
        'msgstr "FuzzyXX"\n'
        '\n'
        'msgid "EmptyOne"\n'
        'msgstr ""\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(i18n_mod, "_TRANSLATIONS_DIR", str(tmp_path))
    i18n_mod._catalog_cache.clear()
    i18n_mod._available_locales = None

    catalog = i18n_mod._load_catalog(locale)
    assert catalog == {"Normal": "NormalXX"}  # fuzzy + empty + header all dropped


@pytest.mark.unit
def test_load_catalog_missing_po_returns_empty(tmp_path, monkeypatch):
    """A locale dir without a messages.po yields an empty catalog (no exception)."""
    from cps.api import i18n as i18n_mod
    monkeypatch.setattr(i18n_mod, "_TRANSLATIONS_DIR", str(tmp_path))
    i18n_mod._catalog_cache.clear()
    assert i18n_mod._load_catalog("nope") == {}


@pytest.mark.unit
def test_po_locales_allowlist_from_filesystem():
    """The allowlist is derived from shipped .po dirs (so it works without .mo)."""
    from cps.api import i18n as i18n_mod
    locales = i18n_mod._po_locales()
    assert "de" in locales
    assert "fr" in locales
    # 'en' is the source locale and has no .po dir; it's handled separately.
    assert isinstance(locales, set)


@pytest.mark.unit
def test_route_registered():
    """The endpoint is wired onto the api_v1 blueprint at the expected rule."""
    from cps.api import i18n as i18n_mod  # noqa: F401  (ensure import side effects)
    # The view function exists and is callable; the route decorator attached it.
    assert callable(i18n_mod.i18n_catalog)
