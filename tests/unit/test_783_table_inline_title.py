"""Acceptance pins for #783's safe inline-title table core."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_table_inline_title_is_permission_gated_and_uses_canonical_mutation():
    source = (ROOT / "frontend/src/pages/Table.tsx").read_text()
    assert "useMe().data?.role?.edit" in source
    assert "useUpdateMetadata(book.id)" in source
    assert "update.mutate({ title }" in source


def test_table_inline_title_has_keyboard_and_accessible_controls():
    source = (ROOT / "frontend/src/pages/Table.tsx").read_text()
    assert "event.key === 'Enter'" in source
    assert "event.key === 'Escape'" in source
    assert "Edit title for {title}" in source
    assert "Cancel title edit" in source
    assert 'role="alert"' in source
