# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""An s6 service's code: its run file plus the scripts/services body it execs (#2094)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
S6_ROOT = REPO_ROOT / "root" / "etc" / "s6-overlay" / "s6-rc.d"
SERVICES_DIR = REPO_ROOT / "scripts" / "services"


def service_script(service: str) -> Path:
    """Path of the service body that a thin run file execs."""
    return SERVICES_DIR / f"{service}.sh"


def service_source(service: str) -> str:
    """The run file's text followed by its service body, when it has one."""
    text = (S6_ROOT / service / "run").read_text(encoding="utf-8")
    script = service_script(service)
    if script.is_file():
        text += "\n" + script.read_text(encoding="utf-8")
    return text
