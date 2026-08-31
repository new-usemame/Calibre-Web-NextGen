# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pin the ordering that lets the Dockerfile delete the `koreader/` tree.

The image builds `koplugin.zip` from `koreader/plugins/cwngsync.koplugin/`
and copies it into `cps/static/`, which is the only copy the running
container reads: `cps/templates/kosync_plugin.html` links it as
``url_for('static', filename='koplugin.zip')``. The source tree left
behind is a second copy of the same plugin, so #1478 removes it — one
copy in the image instead of two, and nobody edits
`koreader/plugins/cwngsync.koplugin/` inside a container and wonders why
the download is unchanged.

That is only safe while the copy happens *before* the delete. Both live
in the same `RUN`, chained with `&&`, so reordering them is a one-line
edit with no local symptom: the build still succeeds, the tests still
pass, and the plugin download 404s at runtime for everyone. This file is
the pin that makes that reorder go red.

`scripts/publish-cwngsync-plugin.sh` also reads `koreader/plugins/`, but
it resolves its ROOT from its own location in a repo checkout and runs
on CI, never inside the image, so the delete does not affect it.
"""

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"

STATIC_COPY = "/app/calibre-web-automated/cps/static/"
KOREADER_TREE = "/app/calibre-web-automated/koreader"

#: The `rm` line, however it is spelled. Deliberately loose about the trailing
#: slash and the quoting: an equivalent rewrite must not make the ordering pin
#: below silently skip, because a skip reads as a pass.
REMOVE_RE = re.compile(
    r"^\s*rm\s+(?P<flags>(?:-{1,2}[A-Za-z-]+\s+)*)['\"]?"
    + re.escape(KOREADER_TREE)
    + r"/?['\"]?\s*$"
)

#: A filesystem reference to a directory named `koreader`, in the two shapes
#: this repo actually uses to build paths, plus the absolute in-image path.
#: Matching the bare word would hit `cps/progress_syncing/checksums/koreader.py`
#: and every import of it, which are modules, not paths.
RUNTIME_READ_RE = re.compile(
    r"calibre-web-automated/koreader"
    r"|/\s*['\"]koreader['\"]"
    r"|join\([^)]*['\"]koreader['\"]"
)


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text()


def _line_index(text: str, predicate) -> int:
    for i, line in enumerate(text.splitlines()):
        if predicate(line):
            return i
    return -1


def _copy_line_index(text: str) -> int:
    return _line_index(
        text,
        lambda ln: ln.strip().startswith("cp ")
        and "koplugin.zip" in ln
        and STATIC_COPY in ln,
    )


def _remove_line_index(text: str) -> int:
    return _line_index(text, lambda ln: REMOVE_RE.match(ln) is not None)


def _remove_flags(line: str) -> str:
    """Every flag token on the `rm`, joined. `-rf`, `-r -f` and
    `--recursive --force` all have to read the same way."""
    match = REMOVE_RE.match(line)
    return "" if match is None else match.group("flags")


def test_the_plugin_zip_is_copied_into_static(dockerfile: str) -> None:
    """The runtime copy must exist at all — the delete is only safe because
    the download button is served from cps/static, not from koreader/."""
    assert _copy_line_index(dockerfile) != -1, (
        "No `cp .../koplugin.zip /app/calibre-web-automated/cps/static/` line "
        "in the Dockerfile. Without it the plugin download button 404s."
    )


def test_the_koreader_tree_is_removed_after_the_zip_is_staged(dockerfile: str) -> None:
    """RED if someone reorders the delete above the copy.

    The build stays green either way; only the runtime download breaks, so
    ordering is the thing worth pinning.
    """
    copy_at = _copy_line_index(dockerfile)
    remove_at = _remove_line_index(dockerfile)
    if remove_at == -1:
        pytest.skip("Dockerfile no longer removes the koreader/ tree")
    assert copy_at != -1, "koreader/ is deleted but koplugin.zip is never staged"
    assert copy_at < remove_at, (
        f"Dockerfile removes {KOREADER_TREE} at line {remove_at + 1} but stages "
        f"koplugin.zip into cps/static at line {copy_at + 1}. The copy must come "
        "first or the image ships without the plugin the download button serves."
    )


def test_the_removal_does_not_hard_fail_a_build(dockerfile: str) -> None:
    """Every other step in this RUN degrades to a warning when its input is
    missing (`else echo "Warning: ..."`). The removal is chained onto the same
    `&&`, so a non-forcing `rm` would turn an absent tree into a failed build
    for a directory the image does not need."""
    remove_at = _remove_line_index(dockerfile)
    if remove_at == -1:
        pytest.skip("Dockerfile no longer removes the koreader/ tree")
    line = dockerfile.splitlines()[remove_at]
    flags = _remove_flags(line)
    forcing = "f" in flags.replace("--", " ").replace("-", " ") or "force" in flags
    assert forcing, (
        f"`{line.strip()}` removes the koreader tree without -f. If the tree is "
        "ever absent the whole RUN fails and the image does not build."
    )


def test_nothing_under_cps_reads_the_koreader_tree_at_runtime(dockerfile: str) -> None:
    """Guard the premise of the delete: if application code ever starts
    reading the in-image koreader/ tree, removing it becomes a live bug.

    Matches the absolute in-image path and the two relative shapes this repo
    builds paths with (`... / "koreader"` and `join(..., "koreader")`), because
    code reaching the tree relatively would pass an absolute-path-only grep and
    then raise FileNotFoundError in the published image only.

    `cps/templates/kosync_plugin.html` and `cps/services/reading_position.py`
    both mention `/koreader/plugins/`, but those are paths on the user's
    e-reader, not in the container, and `cps/progress_syncing/checksums/
    koreader.py` is a module name. None of those are path joins, so none match.
    """
    offenders = []
    for path in (REPO_ROOT / "cps").rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".html", ".js"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable file in a build tree
            continue
        if RUNTIME_READ_RE.search(text):
            offenders.append(path.relative_to(REPO_ROOT))
    assert not offenders, (
        "These files reference the in-image koreader/ tree, which the "
        f"Dockerfile deletes: {offenders}. Either stop reading it or stop "
        "deleting it."
    )
