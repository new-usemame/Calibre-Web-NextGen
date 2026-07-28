# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Marks tests/ as a package so its subdirectories stay namespaced.

Keep this file. Without it, pytest's prepend import mode puts `tests/`
itself on `sys.path`, which promotes every directory under it to a
top-level module — and `tests/docker/` then outranks the installed
Docker SDK, so `import docker` lands here and the SDK's submodules go
missing. That took out the whole Docker integration suite once already.

Helpers under tests/ are imported as `tests.<module>`; nothing should
add `tests/` to `sys.path`. `tests/unit/test_tests_tree_import_hygiene.py`
holds the line.
"""
