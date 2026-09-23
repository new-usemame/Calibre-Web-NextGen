# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The runtime image installs exactly the OCR packages the operator approved.

Reflow's source recovery reads picture-only and damaged PDF pages with the
Tesseract CLI (cps/services/reflow/ocr.py). Without it in the image every such
page stays a facsimile. Project rule 6 makes a new dependency an operator
decision, and the decision recorded on 2026-09-22
(state/pdf2epub-ai/DECISIONS.md) is precise: ``tesseract-ocr``,
``tesseract-ocr-eng`` and ``tesseract-ocr-osd`` from Ubuntu, installed with
``--no-install-recommends``; ``tesseract-ocr-all`` is NOT approved (it pulls
every language pack, ~hundreds of MB).

This parses the runtime stage's ``apt-get install`` invocations -- the install
contract of the image -- so both directions of drift go red: removing the
engine (OCR silently becomes unavailable in production) and widening it past
the approval (a new dependency nobody approved). The image-level proof --
``tesseract --list-langs`` inside a built image -- is the Docker build, which
CI runs; this is the part a unit run can see.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"

APPROVED = {"tesseract-ocr", "tesseract-ocr-eng", "tesseract-ocr-osd"}


def _runtime_stage():
    """The instructions of the last FROM stage (the image that ships)."""
    # Docker drops whole comment lines before it joins continuations, so a
    # comment inside a continued RUN is not part of the command. Do the same.
    text = "\n".join(line for line in DOCKERFILE.read_text().splitlines()
                     if not line.lstrip().startswith("#"))
    # Join line continuations so each instruction is one logical line.
    logical = re.sub(r"\\\n", " ", text)
    stages, current = [], None
    for line in logical.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"FROM\s", stripped, re.I):
            current = []
            stages.append(current)
        elif current is not None:
            current.append(stripped)
    assert stages, "no FROM stage found in the Dockerfile"
    return stages[-1]


def _apt_installs(instructions):
    """Each ``apt-get install`` in the stage: (flags, packages)."""
    installs = []
    for instruction in instructions:
        if not re.match(r"RUN\s", instruction, re.I):
            continue
        body = instruction[3:]
        for command in re.split(r"&&|;|\|\|", body):
            # apt package lists are plain words; shlex would trip on unrelated
            # escapes elsewhere in the same RUN.
            words = [w.strip("'\"") for w in command.split()]
            if len(words) >= 2 and words[0] == "apt-get" and "install" in words:
                args = words[words.index("install") + 1:]
                installs.append(([a for a in args if a.startswith("-")],
                                 [a for a in args if not a.startswith("-")]))
    return installs


def test_the_runtime_image_installs_exactly_the_approved_ocr_packages():
    installs = _apt_installs(_runtime_stage())
    assert installs, "the runtime stage installs no apt packages; the parser is lost"
    ocr = [(flags, [p for p in packages if p.startswith("tesseract")])
           for flags, packages in installs]
    installed = {p for _, packages in ocr for p in packages}
    assert installed == APPROVED, (
        "runtime OCR packages are %s; the operator approved exactly %s "
        "(tesseract-ocr-all is not approved)" % (sorted(installed), sorted(APPROVED)))
    for flags, packages in ocr:
        if packages:
            assert "--no-install-recommends" in flags, (
                "OCR packages must be installed with --no-install-recommends "
                "(recommends would widen the approved package set)")
