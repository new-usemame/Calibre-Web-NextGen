"""Behavioural tests for ``scripts/set_ownership.sh`` (#874).

These drive the real bash, with ``chown`` replaced by a stub that records the
paths it was asked to walk, so each test asserts what the startup pass actually
does rather than what the source text looks like.

Two regressions are pinned here:

* **The duplicate walks the reporter measured.** dirs.json declares
  ``calibre_library_dir=/calibre-library`` and
  ``tmp_conversion_dir=/config/.cwa_conversion_tmp``, and the old inline block
  hardcoded ``/calibre-library`` *and* ``/config`` on top of that -- so a large
  library was chowned twice and a subtree of /config re-walked after /config's
  own recursive pass.

* **The floor.** The obvious "just build the list from dirs.json" fix drops
  ``/config`` and the app tree, both of which are load-bearing: ``/config``
  holds ``app.db`` and ``user_profiles.json``, which cwa-init writes *as root*
  after the early chown, and the app tree ships owned by the build-time uid 911
  that the base image usermods away from at runtime. dirs.json declares neither,
  and ``scripts/auto_library.py`` rewrites it in place at runtime, so a truncated
  file must not be able to silently reduce the pass to nothing.

A marker-based skip was considered and rejected on measurement: the app tree is
only expensive to walk on a *fresh* container (it arrives owned by the build-time
uid), and a marker kept in the image's writable layer is necessarily absent
exactly then -- so it would have skipped only the already-cheap restart case.
That cost is tracked separately; see the follow-up linked from #874.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SET_OWNERSHIP = REPO_ROOT / "scripts" / "set_ownership.sh"

CHOWN_STUB = """#!/bin/sh
# Records every invocation, one line per call, then succeeds.
echo "$@" >> "$CWA_TEST_CHOWN_LOG"
exit 0
"""

FAILING_CHOWN_STUB = """#!/bin/sh
echo "$@" >> "$CWA_TEST_CHOWN_LOG"
exit 1
"""


class Harness:
    """A fake container filesystem plus a chown that only takes notes."""

    def __init__(self, tmp_path: Path, chown_stub: str = CHOWN_STUB):
        self.tmp = tmp_path
        self.app_root = tmp_path / "app"
        self.config_root = tmp_path / "config"
        self.library = tmp_path / "calibre-library"
        self.ingest = tmp_path / "cwa-book-ingest"
        self.conv_tmp = self.config_root / ".cwa_conversion_tmp"
        for d in (self.app_root, self.config_root, self.library, self.ingest, self.conv_tmp):
            d.mkdir(parents=True, exist_ok=True)

        self.chown_log = tmp_path / "chown.log"
        self.chown = tmp_path / "chown-stub"
        self.chown.write_text(chown_stub)
        self.chown.chmod(0o755)

        self.dirs_json = self.app_root / "dirs.json"
        self.write_dirs_json(
            {
                "ingest_folder": str(self.ingest),
                "calibre_library_dir": str(self.library),
                "tmp_conversion_dir": str(self.conv_tmp),
            }
        )

    def write_dirs_json(self, payload) -> None:
        if payload is None:
            if self.dirs_json.exists():
                self.dirs_json.unlink()
            return
        if isinstance(payload, str):
            self.dirs_json.write_text(payload)
        else:
            self.dirs_json.write_text(json.dumps(payload))

    def run(self, **env_overrides) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update(
            {
                "CWA_APP_ROOT": str(self.app_root),
                "CWA_CONFIG_ROOT": str(self.config_root),
                "CWA_DIRS_JSON": str(self.dirs_json),
                # The real runtime user is `abc`; it does not exist on a test box,
                # so own the fake tree as whoever is running the suite.
                "CWA_OWNER_USER": str(os.getuid()),
                "CWA_CHOWN": str(self.chown),
                "CWA_TEST_CHOWN_LOG": str(self.chown_log),
            }
        )
        env.pop("NETWORK_SHARE_MODE", None)
        env.update({k: str(v) for k, v in env_overrides.items()})
        return subprocess.run(
            ["bash", str(SET_OWNERSHIP)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def chowned_paths(self) -> list[str]:
        """Paths passed to a recursive chown, in order, one entry per call.

        Trailing slashes are stripped: ``chown -R /lib`` and ``chown -R /lib/``
        walk the same tree twice, and a test that compared them as raw strings
        would call that a pass.
        """
        if not self.chown_log.exists():
            return []
        paths = []
        for line in self.chown_log.read_text().splitlines():
            parts = line.split()
            if "-R" not in parts:
                continue  # the marker's own non-recursive chown
            path = parts[-1]
            while len(path) > 1 and path.endswith("/"):
                path = path[:-1]
            paths.append(path)
        return paths

    def reset_log(self) -> None:
        if self.chown_log.exists():
            self.chown_log.unlink()


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def test_set_ownership_script_exists_and_is_executable():
    assert SET_OWNERSHIP.exists(), f"missing {SET_OWNERSHIP}"
    assert os.access(SET_OWNERSHIP, os.X_OK), f"{SET_OWNERSHIP} must be executable"


# --------------------------------------------------------------------------
# The reported symptom: redundant walks (#874)
# --------------------------------------------------------------------------


def test_library_is_chowned_exactly_once(harness: Harness):
    """The reporter's headline: /calibre-library was walked twice because it was
    both hardcoded in the list and declared in dirs.json."""
    harness.run()
    walked = harness.chowned_paths()
    assert walked.count(str(harness.library)) == 1, (
        f"library must be walked exactly once, got {walked}"
    )


def test_the_same_path_declared_under_two_keys_is_walked_once(harness: Harness):
    """The shape of the reported bug: the library reachable twice through the
    list (there: hardcoded + dirs.json) must still be walked once."""
    harness.write_dirs_json(
        {
            "calibre_library_dir": str(harness.library),
            "legacy_library_alias": str(harness.library),
        }
    )
    harness.run()
    walked = harness.chowned_paths()
    assert walked.count(str(harness.library)) == 1, (
        f"library must be walked exactly once, got {walked}"
    )


def test_conversion_tmp_is_not_walked_separately_from_config(harness: Harness):
    """/config/.cwa_conversion_tmp is inside /config, whose recursive pass
    already covers it."""
    walked = (harness.run(), harness.chowned_paths())[1]
    assert str(harness.config_root) in walked
    assert str(harness.conv_tmp) not in walked, (
        f"conversion tmp is a subtree of /config and must not be re-walked: {walked}"
    )


def test_every_path_is_walked_at_most_once(harness: Harness):
    harness.run()
    walked = harness.chowned_paths()
    assert len(walked) == len(set(walked)), f"duplicate walks: {walked}"


def test_nested_dirs_json_entries_collapse_into_their_parent(harness: Harness):
    """A dirs.json that declares both a tree and something inside it should
    produce one walk, not two."""
    nested = harness.library / "sub" / "deeper"
    nested.mkdir(parents=True)
    harness.write_dirs_json(
        {
            "calibre_library_dir": str(harness.library),
            "weird_nested_dir": str(nested),
        }
    )
    harness.run()
    walked = harness.chowned_paths()
    assert str(harness.library) in walked
    assert str(nested) not in walked


def test_trailing_slash_does_not_defeat_deduplication(harness: Harness):
    harness.write_dirs_json(
        {
            "calibre_library_dir": str(harness.library),
            "same_dir_with_slash": str(harness.library) + "/",
        }
    )
    harness.run()
    walked = harness.chowned_paths()
    assert walked.count(str(harness.library)) == 1, walked


# --------------------------------------------------------------------------
# The floor: dirs.json declares neither /config nor the app tree
# --------------------------------------------------------------------------


def test_config_and_app_root_are_always_walked(harness: Harness):
    harness.run()
    walked = harness.chowned_paths()
    assert str(harness.config_root) in walked, (
        "/config holds app.db and user_profiles.json, both written as root after "
        f"the early chown; got {walked}"
    )
    assert str(harness.app_root) in walked, (
        f"the app tree ships owned by the build-time uid; got {walked}"
    )


def test_missing_dirs_json_still_walks_the_floor(harness: Harness):
    harness.write_dirs_json(None)
    harness.run()
    walked = harness.chowned_paths()
    assert str(harness.config_root) in walked
    assert str(harness.app_root) in walked


def test_truncated_dirs_json_still_walks_the_floor(harness: Harness):
    """auto_library.py rewrites dirs.json in place; a crash mid-write leaves
    unparseable JSON. That must not silently reduce the pass to nothing."""
    harness.write_dirs_json('{"ingest_folder":"/cwa-book-')
    result = harness.run()
    walked = harness.chowned_paths()
    assert result.returncode == 0
    assert str(harness.config_root) in walked
    assert str(harness.app_root) in walked


def test_non_absolute_dirs_json_values_are_ignored(harness: Harness):
    harness.write_dirs_json(
        {"calibre_library_dir": str(harness.library), "junk": "not-a-path"}
    )
    harness.run()
    assert "not-a-path" not in harness.chowned_paths()


def test_log_line_names_the_directories(harness: Harness):
    result = harness.run()
    assert "Preparing to set ownership of everything in" in result.stdout
    assert str(harness.config_root) in result.stdout
    # regression: an empty array used to render "everything in  to abc:abc"
    assert "everything in  to" not in result.stdout
    assert "everything in," not in result.stdout


# --------------------------------------------------------------------------
# NETWORK_SHARE_MODE parity with the previous inline block
# --------------------------------------------------------------------------


@pytest.mark.parametrize("truthy", ["true", "True", "TRUE", "1", "yes", "on"])
def test_network_share_mode_skips_bind_mounts(tmp_path: Path, truthy: str):
    h = Harness(tmp_path)
    # The exemption matches the real mount points by name.
    h.library = Path("/calibre-library")
    h.write_dirs_json(
        {"calibre_library_dir": "/calibre-library", "ingest_folder": "/cwa-book-ingest"}
    )
    h.run(NETWORK_SHARE_MODE=truthy)
    walked = h.chowned_paths()
    assert "/calibre-library" not in walked
    assert "/cwa-book-ingest" not in walked


def test_network_share_mode_still_walks_the_app_tree(harness: Harness):
    """The app tree is inside the image, never on the share."""
    harness.run(NETWORK_SHARE_MODE="true")
    assert str(harness.app_root) in harness.chowned_paths()


def test_falsey_network_share_mode_walks_everything(harness: Harness):
    harness.run(NETWORK_SHARE_MODE="false")
    walked = harness.chowned_paths()
    assert str(harness.library) in walked
    assert str(harness.config_root) in walked


# --------------------------------------------------------------------------
# The init unit must actually call the script
# --------------------------------------------------------------------------


def test_cwa_init_invokes_set_ownership():
    run_script = (
        REPO_ROOT / "root" / "etc" / "s6-overlay" / "s6-rc.d" / "cwa-init" / "run"
    )
    body = run_script.read_text()
    assert "scripts/set_ownership.sh" in body, (
        "cwa-init must delegate the ownership pass to set_ownership.sh"
    )
    assert "declare -a requiredDirs=(" not in body, (
        "the inline duplicate-prone list must not come back"
    )
