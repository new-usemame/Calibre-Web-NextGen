# SPDX-License-Identifier: GPL-3.0-or-later
"""Root-cause regression pins for the cohesive SP2 display-options pass."""
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_browser_uploads_write_explicit_original_filename_manifests():
    """Staging prefixes are implementation data, so both upload surfaces must
    carry the browser basename explicitly instead of stripping via regex."""
    for path in ("cps/api/upload.py", "cps/editbooks.py"):
        body = source(path)
        assert '"action": "import"' in body
        assert '"original_filename": uploaded.filename' in body or \
               '"original_filename": requested_file.filename' in body
    ingest = source("scripts/ingest_processor.py")
    assert 'if action == "import"' in ingest
    assert 'nbp.original_filename = Path(original_filename).name' in ingest
    assert "re.match" not in ingest.split('if action == "import"', 1)[1][:500]


def test_spa_detail_api_and_type_expose_original_filename():
    assert 'original_filename=_original_filename(book_id)' in source("cps/api/books.py")
    assert '"original_filename": original_filename' in source("cps/api/serializers.py")
    assert 'original_filename: string | null' in source("frontend/src/lib/api.ts")
    assert "t('Imported as')" in source("frontend/src/pages/BookDetail.tsx")


def test_reload_restats_every_real_format_before_one_commit(tmp_path):
    from cps.editbooks import _refresh_format_sizes

    (tmp_path / "Title.epub").write_bytes(b"1234567")
    (tmp_path / "Title.pdf").write_bytes(b"12345678901")
    epub = SimpleNamespace(format="EPUB", name="Title", uncompressed_size=1)
    pdf = SimpleNamespace(format="PDF", name="Title", uncompressed_size=11)
    missing = SimpleNamespace(format="MOBI", name="Missing", uncompressed_size=9)
    changed = _refresh_format_sizes(SimpleNamespace(data=[epub, pdf, missing]), str(tmp_path))
    assert changed == ["EPUB"]
    assert epub.uncompressed_size == 7
    assert pdf.uncompressed_size == 11
    assert missing.uncompressed_size == 9

    body = source("cps/editbooks.py").split("def reload_metadata_from_disk", 1)[1]
    assert "_refresh_format_sizes(book, book_dir)" in body
    assert "format_size:" in body
    assert body.index("_refresh_format_sizes(book, book_dir)") < body.index("calibre_db.session.commit()")


def test_display_preferences_have_one_persistence_key_each():
    catalog = source("frontend/src/pages/Catalog.tsx")
    assert catalog.count("cwng:catalog-density-v1") == 1
    assert catalog.count("cwng:series-presentation-v1") == 1
    assert "cwng:browse-list-compact" in source("frontend/src/pages/BrowseList.tsx")


def test_catalog_rows_drive_live_width_aware_page_size():
    catalog = source("frontend/src/pages/Catalog.tsx")
    queries = source("frontend/src/lib/queries.ts")
    assert catalog.count("cwng:catalog-rows-v1") == 1
    assert "ResizeObserver" in catalog
    assert "rowsPerLoad * columnCount" in catalog
    assert "perPage" in queries
    assert "params.set('per_page', String(perPage))" in queries
    assert "perPage" in queries.split("queryKey: ['books'", 1)[1].split("queryFn:", 1)[0]


def test_discover_honors_instance_random_book_count():
    auth = source("cps/api/auth.py")
    api = source("frontend/src/lib/api.ts")
    discover = source("frontend/src/components/DiscoverSection.tsx")
    assert 'getattr(config, "config_random_books", 4)' in auth
    assert "random_books: number" in api
    assert "me?.display?.random_books" in discover


def test_touch_read_now_actions_share_a_bottom_baseline():
    css = source("frontend/src/components/BookCard.module.css")
    assert ".wrap {" in css and "height: 100%;" in css
    assert ".card, .cardSelected" in css and "flex: 1;" in css
    assert ".readNow" in css and "margin-top: auto;" in css


def test_spa_magic_shelf_builder_exposes_relative_date_fields():
    builder = source("frontend/src/pages/MagicShelf.tsx")
    for token in ("pubdate", "timestamp", "Publication Date", "Date Added",
                  "in_last_days", "not_in_last_days"):
        assert token in builder
    assert "type={inputType(r)}" in builder


def test_spa_magic_shelf_create_does_not_fetch_an_empty_id_and_mobile_rules_fit():
    queries = source("frontend/src/lib/queries.ts")
    css = source("frontend/src/pages/MagicShelf.module.css")
    assert "enabled: String(id).length > 0" in queries
    assert "@media (max-width: 600px)" in css
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto" in css
    assert ".ruleRow input { grid-column: 1 / 3; width: 100%; }" in css


def test_spa_exposes_permission_gated_reload_metadata_action():
    detail = source("frontend/src/pages/BookDetail.tsx")
    queries = source("frontend/src/lib/queries.ts")
    assert "useReloadMetadata(id)" in detail
    assert "me?.role?.edit" in detail
    assert "t('Reload metadata from disk')" in detail
    assert 'role="status"' in detail
    assert "/admin/book/${id}/reload_metadata" in queries


def test_detail_accessibility_contracts_are_semantic():
    detail = source("frontend/src/pages/BookDetail.tsx")
    assert 'role="progressbar"' in detail
    for attribute in ("aria-valuemin", "aria-valuemax", "aria-valuenow"):
        assert attribute in detail
    assert "aria-expanded={expanded}" in detail
    assert 'aria-controls="book-tags"' in detail


def test_customize_panel_restores_table_through_sidebar_visibility():
    sidebar = source("frontend/src/components/Sidebar.tsx")
    assert "v.list = me?.sidebar?.list !== false" in sidebar
    assert "setVis((current) => ({ ...current, list:" in sidebar
    assert "t('Show Table view')" in sidebar
    account = source("cps/api/account.py")
    assert "current_user.sidebar_view = view" in account


def test_login_methods_share_one_accessible_named_provider_group():
    login = source("frontend/src/pages/Login.tsx")
    assert 'role="group" aria-label={t(\'Login with\')}' in login
    assert "providers.map" in login and "{p.name}" in login
    assert "t('Magic link')" in login


def test_shelf_toolbar_wraps_inside_a_375px_viewport():
    css = source("frontend/src/pages/Shelf.module.css")
    assert ".manage" in css and "flex-wrap: wrap" in css
    assert "@media (max-width: 420px)" in css
    assert ".subRow, .manage { width: 100%; }" in css
