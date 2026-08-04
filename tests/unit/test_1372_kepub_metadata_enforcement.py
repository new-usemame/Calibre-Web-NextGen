# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression pins for metadata enforcement on .kepub files (fork #1372).

Editing a book's series in the web UI updated metadata.db and the .epub, but the
.kepub was silently skipped: ``supported_formats`` was ``["epub", "azw3"]`` and
``"book.kepub".endswith(".epub")`` is False. Kobo sync serves the .kepub, so the
reporter's Kobo showed no series no matter how many times they re-saved.

The .kepub is written with ``ebook-meta --from-opf`` rather than ``ebook-polish``
on purpose. Measured on calibre 9.1 against a kepubify-produced file, polishing a
.kepub re-segments it: kobo span count went 7016 -> ~13.5k with AND without -U,
because calibre re-applies its own KEPUB spans. Kobo stores reading positions
against those span ids, so polishing would move readers' bookmarks in every
already-synced book. ``ebook-meta --from-opf`` embeds the same metadata and left
all 24 content files byte-identical in span count (7016 -> 7016).
"""

import ast
import atexit
import importlib.util
import shutil
import sys
import tempfile
import types
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cover_enforcer.py"


def _load_module():
    """Import cover_enforcer.py with its DB dependency stubbed out.

    The stub is installed only for the duration of the import and then removed,
    restoring whatever was there before. Leaving a fake ``cwa_db`` behind in
    ``sys.modules`` is visible to every test that runs after this file in the
    same process -- it broke test_802's enforcer invocation when this pin was
    first written.
    """
    stub = types.ModuleType("cwa_db")

    class _StubDB:  # pragma: no cover - only needs to exist for import
        def __init__(self, *a, **kw):
            self.cwa_settings = {"auto_metadata_enforcement": 1}

    stub.CWA_DB = _StubDB
    sentinel = object()
    previous = sys.modules.get("cwa_db", sentinel)
    sys.modules["cwa_db"] = stub

    # Importing cover_enforcer takes a lock file at module scope ("x" mode,
    # sys.exit(2) if it already exists) keyed on tempfile.gettempdir(), and only
    # drops it via atexit. Shared with the real module that test_802 imports,
    # that is a collision in both directions: our import would strand the lock
    # and make theirs exit 2, and theirs would make ours die during fixture
    # setup. pytest-randomly shuffles file order, so either way round is
    # reachable in CI. Point the import at a private temp dir so the two never
    # contend, then unregister the atexit hook -- it resolves gettempdir() at
    # call time and would otherwise delete the real module's lock at shutdown.
    private_tmp = tempfile.mkdtemp(prefix="cover_enforcer_test_")
    real_gettempdir = tempfile.gettempdir
    tempfile.gettempdir = lambda: private_tmp
    try:
        spec = importlib.util.spec_from_file_location(
            "cover_enforcer_under_test", SCRIPT
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        tempfile.gettempdir = real_gettempdir
        if previous is sentinel:
            sys.modules.pop("cwa_db", None)
        else:
            sys.modules["cwa_db"] = previous

    atexit.unregister(module.removeLock)
    shutil.rmtree(private_tmp, ignore_errors=True)
    return module


@pytest.fixture(scope="module")
def enforcer_module():
    return _load_module()


def _bare_enforcer(module):
    """An Enforcer instance without __init__ (which would touch the real DB)."""
    inst = object.__new__(module.Enforcer)
    # Read the production default out of the source rather than hand-setting it,
    # so these tests fail when that list stops covering kepub. Enforcer.__init__
    # builds a real CWA_DB, so it cannot be called here.
    src = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    formats = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Attribute) and t.attr == "supported_formats"
                for t in node.targets
            )
            and isinstance(node.value, ast.List)
        ):
            formats = [el.value for el in node.value.elts]
            break
    assert formats is not None, "supported_formats assignment not found"
    inst.supported_formats = formats
    return inst


@pytest.mark.unit
def test_kepub_is_a_supported_enforcement_format(enforcer_module):
    """The production format list must cover kepub (#1372)."""
    inst = _bare_enforcer(enforcer_module)
    assert "kepub" in inst.supported_formats
    # the pre-existing formats must not regress
    assert "epub" in inst.supported_formats
    assert "azw3" in inst.supported_formats


@pytest.mark.unit
def test_kepub_file_is_discovered_in_a_book_dir(enforcer_module, tmp_path):
    """A .kepub in the book dir is picked up for enforcement.

    Red before the fix: '.kepub' does not end with '.epub', so the file was
    invisible to the enforcer and the reporter's Kobo never saw the new series.
    """
    book_dir = tmp_path / "Oscar Wilde" / "The Picture of Dorian Gray (2)"
    book_dir.mkdir(parents=True)
    kepub = book_dir / "The Picture of Dorian Gray - Oscar Wilde.kepub"
    kepub.write_bytes(b"PK\x03\x04stub")

    inst = _bare_enforcer(enforcer_module)
    found = inst.get_supported_files_from_dir(str(book_dir))

    assert [Path(f).name for f in found] == [kepub.name]


@pytest.mark.unit
def test_epub_and_kepub_side_by_side_are_both_enforced(enforcer_module, tmp_path):
    """The reporter's real layout: one book carrying both formats."""
    book_dir = tmp_path / "Oscar Wilde" / "The Picture of Dorian Gray (2)"
    book_dir.mkdir(parents=True)
    (book_dir / "book.epub").write_bytes(b"PK\x03\x04stub")
    (book_dir / "book.kepub").write_bytes(b"PK\x03\x04stub")

    inst = _bare_enforcer(enforcer_module)
    found = {Path(f).name for f in inst.get_supported_files_from_dir(str(book_dir))}

    assert found == {"book.epub", "book.kepub"}


@pytest.mark.unit
def test_double_extension_kepub_epub_is_not_enforced_twice(enforcer_module, tmp_path):
    """A 'book.kepub.epub' must be returned once, not once per matching format."""
    book_dir = tmp_path / "Author" / "Title (7)"
    book_dir.mkdir(parents=True)
    (book_dir / "book.kepub.epub").write_bytes(b"PK\x03\x04stub")

    inst = _bare_enforcer(enforcer_module)
    found = inst.get_supported_files_from_dir(str(book_dir))

    assert len(found) == 1, f"expected one entry, got {found}"


@pytest.mark.unit
def test_kepub_uses_ebook_meta_not_ebook_polish(enforcer_module, tmp_path, monkeypatch):
    """kepub metadata is embedded WITHOUT re-polishing the content.

    Polishing a kepub makes calibre re-apply its own KEPUB spans (measured
    7016 -> ~13.5k on calibre 9.1), which would move Kobo reading positions.
    """
    book_dir = tmp_path / "Author" / "Title (3)"
    book_dir.mkdir(parents=True)
    kepub = book_dir / "book.kepub"
    kepub.write_bytes(b"PK\x03\x04stub")
    (book_dir / "cover.jpg").write_bytes(b"\xff\xd8stub")
    opf = tmp_path / "new_metadata.opf"
    opf.write_text("<package/>", encoding="utf-8")

    calls = _patch_enforce_dependencies(enforcer_module, monkeypatch, book_dir, opf)

    inst = _bare_enforcer(enforcer_module)
    inst.enforce_cover(str(book_dir))

    assert calls, "no subprocess command was issued for the kepub"
    cmd = calls[0]
    assert cmd[0] == "ebook-meta", f"kepub must not be polished; got {cmd[0]}"
    assert "--from-opf" in cmd, f"metadata source OPF not passed: {cmd}"
    assert str(opf) in cmd
    assert str(kepub) in cmd
    assert "ebook-polish" not in cmd


@pytest.mark.unit
def test_epub_still_uses_ebook_polish(enforcer_module, tmp_path, monkeypatch):
    """No regression: the existing .epub path is unchanged."""
    book_dir = tmp_path / "Author" / "Title (4)"
    book_dir.mkdir(parents=True)
    epub = book_dir / "book.epub"
    epub.write_bytes(b"PK\x03\x04stub")
    (book_dir / "cover.jpg").write_bytes(b"\xff\xd8stub")
    opf = tmp_path / "new_metadata.opf"
    opf.write_text("<package/>", encoding="utf-8")

    calls = _patch_enforce_dependencies(enforcer_module, monkeypatch, book_dir, opf)

    inst = _bare_enforcer(enforcer_module)
    inst.enforce_cover(str(book_dir))

    assert calls, "no subprocess command was issued for the epub"
    cmd = calls[0]
    assert cmd[0] == "ebook-polish"
    assert "-o" in cmd and str(opf) in cmd


def _patch_enforce_dependencies(module, monkeypatch, book_dir, opf):
    """Stub out Book/subprocess/side-effects so enforce_cover is testable.

    Returns the list that captures each argv the enforcer would have run.
    """
    calls = []

    class _FakeBook:
        def __init__(self, bd, file_path):
            self.book_dir = bd
            self.file_path = file_path
            self.file_format = Path(file_path).suffix.replace(".", "")
            self.cover_path = str(Path(bd) / "cover.jpg")
            self.old_metadata_path = str(Path(bd) / "metadata.opf")
            self.new_metadata_path = str(opf)
            self.book_id = "3"
            self.title_author = "Author - Title"
            # Present so that the pre-fix fall-through into
            # _write_metadata_backup_for_unsupported fails on the assertion
            # under test rather than on a missing attribute of this stub.
            self.book_title = "Title"
            self.author_name = "Author"

    def _fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        result = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return result

    monkeypatch.setattr(module, "Book", _FakeBook)
    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        module.Enforcer, "replace_old_metadata", lambda self, a, b: None
    )
    monkeypatch.setattr(
        module.Enforcer, "empty_metadata_temp", lambda self: None
    )
    monkeypatch.setattr(
        module.Enforcer,
        "_recalculate_checksum_after_modification",
        lambda self, *a, **kw: None,
    )
    monkeypatch.setattr(
        module.Enforcer, "_reset_book_dir_ownership", staticmethod(lambda *a: None)
    )
    return calls


# --- The messages that name the supported formats must derive from the list ---
#
# Adding kepub left three statements still reading "EPUB and AZW3": the
# skipped-format notice in _write_metadata_backup_for_unsupported, the
# no-supported-files line in main(), and a docstring. Two of those are printed
# to the user, so the enforcer named kepub as unsupported in the same release
# that started supporting it. These pin the messages to supported_formats.


@pytest.mark.unit
def test_supported_formats_label_names_every_supported_format(enforcer_module):
    """The human-readable label covers the production list, whatever is in it."""
    inst = _bare_enforcer(enforcer_module)
    label = inst.supported_formats_label()
    for fmt in inst.supported_formats:
        assert fmt.upper() in label, f"{fmt} missing from {label!r}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "formats, expected",
    [
        (["epub"], "EPUB"),
        (["epub", "azw3"], "EPUB & AZW3"),
        (["epub", "azw3", "kepub"], "EPUB, AZW3 & KEPUB"),
        ([], "no"),
    ],
)
def test_supported_formats_label_shape(enforcer_module, formats, expected):
    """Rendering stays grammatical as the list grows or shrinks."""
    inst = _bare_enforcer(enforcer_module)
    inst.supported_formats = formats
    assert inst.supported_formats_label() == expected


@pytest.mark.unit
def test_no_user_facing_message_hardcodes_a_format_name():
    """No print() may restate the format list.

    This is the pin that actually prevents the drift: a hardcoded 'AZW3' in a
    printed string is a second source of truth for supported_formats, and it is
    how all three statements silently went stale. Docstrings are exempt -- they
    are not shown to users -- so this walks print() call arguments only.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print"):
            continue
        for const in ast.walk(node):
            if isinstance(const, ast.Constant) and isinstance(const.value, str):
                if "AZW3" in const.value or "KEPUB" in const.value:
                    offenders.append((getattr(node, "lineno", "?"), const.value.strip()))
    assert not offenders, (
        "print() restates the supported-format list instead of calling "
        f"supported_formats_label(): {offenders}"
    )


@pytest.mark.unit
def test_enforce_all_covers_enforces_a_dual_format_book_once(enforcer_module, tmp_path):
    """Whole-library enforcement visits a book dir once, not once per format.

    ``enforce_all_covers`` appended one dir per supported FILE while
    ``enforce_cover`` already enforces every supported file in the dir it is
    given, so a book was fully re-enforced once per format it carried. Adding
    kepub made that the normal layout rather than an .azw3 edge case: an .epub
    beside its .kepub meant four file rewrites instead of two, the checksum
    recalculated twice per file, and a duplicate enforcement-log row.
    """
    module = enforcer_module
    book_dir = tmp_path / "Some Book (1)"
    book_dir.mkdir()
    (book_dir / "book.epub").write_text("x", encoding="utf-8")
    (book_dir / "book.kepub").write_text("x", encoding="utf-8")

    inst = _bare_enforcer(module)
    inst.calibre_library = str(tmp_path)
    inst.db = types.SimpleNamespace(enforce_add_entry_from_all=lambda *a, **kw: None)

    calls = []

    def _fake_enforce_cover(book_dir_arg):
        calls.append(book_dir_arg)
        return []

    inst.enforce_cover = _fake_enforce_cover

    inst.enforce_all_covers()

    assert calls == [str(book_dir)], (
        f"expected the dual-format book dir to be enforced once, got {calls}"
    )
