# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Reflow — AI-assisted PDF to reflowable EPUB conversion.

A deterministic skeleton does the work; a vision model edits *structure only*, on
the pages the deterministic pass could not resolve; a hard word-preservation gate
refuses any page whose words changed. See ``gate.py`` for why that shape.

Nothing in this package imports Flask. The task (``cps/tasks/reflow.py``) and the
API (``cps/api/reflow.py``) are the only places that know about the app.
"""
