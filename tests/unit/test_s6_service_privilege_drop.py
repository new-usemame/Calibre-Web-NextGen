"""Source-pin that the auto-zipper service drops privileges to abc
before invoking auto_zip.py.

Background — issue #162: the auto-zipper service called
``python3 /app/calibre-web-automated/scripts/auto_zip.py`` directly,
without ``s6-setuidgid abc``. The resulting nightly .zip archives in
``/config/processed_books/fixed_originals/`` were owned by ``root:root``
while .epub outputs in the same directory — produced by
cwa-ingest-service, which already drops privileges — were owned by
PUID:PGID. The mismatch broke host-side cleanup and backup workflows
for any deployment where PUID isn't 0.

Since #947 the drop is spelled ``cwa-as-abc`` rather than a bare
``s6-setuidgid abc``, because an unconditional drop is fatal when the
container itself is started as an unprivileged user. These tests pin the
drop, not the spelling — see DROP_TO_ABC below.

This test is narrow on purpose: it pins the exact regression the user
reported. A broader audit of every long-running service is tracked in
``notes/s6-privilege-drop-audit.md`` — several services (e.g.
metadata-change-detector) run as root but rely on downstream Python
helpers calling ``os.chown`` themselves, which is structurally
different from the auto_zip pattern and needs per-service analysis.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTO_ZIPPER_RUN = (
    REPO_ROOT
    / "root"
    / "etc"
    / "s6-overlay"
    / "s6-rc.d"
    / "cwa-auto-zipper"
    / "run"
)
AS_ABC_HELPER = REPO_ROOT / "root" / "usr" / "local" / "bin" / "cwa-as-abc"

# Two spellings satisfy issue #162, and both must keep doing so.
#
# ``s6-setuidgid abc`` is the original, unconditional drop. ``cwa-as-abc``
# (added for #947) is a chainloader that performs exactly that drop when the
# container runs as root — the case #162 was reported in — and skips it only
# when PID 1 is already an unprivileged uid, where the drop is impossible
# rather than merely unnecessary. Pinning the literal string instead of the
# behaviour made this file fail the moment the indirection landed, which is
# the test being wrong, not the code.
#
# test_the_helper_still_performs_the_drop below is what keeps the second
# spelling honest: if someone hollows out cwa-as-abc, this file fails even
# though the service scripts never changed.
DROP_TO_ABC = r"(?:s6-setuidgid\s+abc|cwa-as-abc)"


def test_the_helper_still_performs_the_drop():
    """``cwa-as-abc`` is only an acceptable stand-in for a bare
    ``s6-setuidgid abc`` for as long as it actually chainloads one when
    running as root. Without this, gutting the helper would silently
    un-fix #162 across every service while the pins below stayed green.

    tests/unit/test_947_non_root_container_starts.py executes the helper
    with a stubbed PATH to prove both branches; this is the cheap source
    pin that keeps *this* regression file self-contained."""
    assert AS_ABC_HELPER.exists(), f"missing {AS_ABC_HELPER}"
    body = AS_ABC_HELPER.read_text()
    assert re.search(r'id\s+-u', body), (
        "cwa-as-abc no longer branches on the current uid — it can no "
        "longer be assumed to drop privileges when root."
    )
    assert re.search(r"exec\s+s6-setuidgid\s+abc\s+\"\$@\"", body), (
        "cwa-as-abc no longer chainloads `s6-setuidgid abc` — the services "
        "that call it are no longer dropping to the app user, which "
        "regresses issue #162."
    )


def test_cwa_auto_zipper_invokes_auto_zip_under_s6_setuidgid():
    """Every uncommented invocation of auto_zip.py in the auto-zipper
    run script must drop privileges to ``abc`` first."""
    assert AUTO_ZIPPER_RUN.exists(), f"missing {AUTO_ZIPPER_RUN}"
    text = AUTO_ZIPPER_RUN.read_text()
    assert "auto_zip.py" in text, "cwa-auto-zipper run script no longer references auto_zip.py"

    setuid_pattern = re.compile(
        rf"\b{DROP_TO_ABC}\b.*python3?\b.*auto_zip\.py"
    )
    offenders = []
    saw_invocation = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if "auto_zip.py" not in line:
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            continue  # documentation comment, not an invocation
        saw_invocation = True
        if not setuid_pattern.search(line):
            offenders.append(f"line {lineno}: {stripped}")
    assert saw_invocation, (
        "no live invocation of auto_zip.py found — did the service get "
        "renamed or rewritten?"
    )
    assert not offenders, (
        "cwa-auto-zipper invokes auto_zip.py without dropping to `abc` "
        "(neither `s6-setuidgid abc` nor `cwa-as-abc`) — "
        f"would regress issue #162: {offenders}"
    )


def test_cwa_ingest_service_still_uses_s6_setuidgid_for_python():
    """Sanity-anchor for the comparison case in the #162 bug report:
    cwa-ingest-service drops privs before invoking ingest_processor.py,
    which is why .epub outputs in fixed_originals are PUID-owned. If
    this assertion breaks, the regression test above loses its
    comparator."""
    run = REPO_ROOT / "root" / "etc" / "s6-overlay" / "s6-rc.d" / "cwa-ingest-service" / "run"
    assert run.exists(), f"missing {run}"
    text = run.read_text()
    assert re.search(
        rf"{DROP_TO_ABC}\s+(?:\S+\s+)*python3?\s+/app/calibre-web-automated/scripts/ingest_processor\.py",
        text,
    ), (
        "cwa-ingest-service no longer drops to `abc` before invoking "
        "ingest_processor.py. The structural comparison underlying issue "
        "#162 has changed — re-evaluate the auto-zipper fix."
    )
