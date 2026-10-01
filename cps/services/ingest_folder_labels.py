# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared validation and path handling for ingest-folder labels."""

import os
from pathlib import Path


TARGET_DISABLED = "disabled"
TARGET_TAGS = "tags"
NESTED_SETTING = "auto_ingest_folder_label_nested"
TARGET_SETTING = "auto_ingest_folder_label_target"


class IngestFolderLabelError(ValueError):
    """Configuration or source path cannot safely produce folder labels."""


def eligible_custom_column_options(columns):
    """Serialize existing Calibre tag-like text columns as stable lookups."""
    result = []
    for column in columns:
        label = getattr(column, "label", None)
        if (not isinstance(label, str) or not label.strip()
                or getattr(column, "datatype", None) != "text"
                or not bool(getattr(column, "is_multiple", False))
                or bool(getattr(column, "mark_for_delete", False))):
            continue
        result.append({
            "lookup": "#" + label,
            "name": str(getattr(column, "name", None) or label),
        })
    return sorted(result, key=lambda item: (item["name"].casefold(), item["lookup"]))


def validate_target(target, custom_column_options):
    """Return normalized target or raise a clear configuration error."""
    if not isinstance(target, str):
        raise IngestFolderLabelError("Folder-label target must be a string")
    target = target.strip()
    if target in (TARGET_DISABLED, TARGET_TAGS):
        return target
    if any(ord(character) < 32 for character in target):
        raise IngestFolderLabelError("Folder-label target is invalid")
    allowed = {item["lookup"] for item in custom_column_options}
    if target.startswith("#") and target in allowed:
        return target
    raise IngestFolderLabelError(
        "Choose Tags or an existing comma-separated text custom column"
    )


def folder_label_values(source_path, ingest_root, *, nested=False) -> list[str] | None:
    """Derive folder values from the original path, rejecting symlink escapes.

    The relative path is calculated lexically so a file reached through an
    in-root alias keeps the folder the operator used. Real paths are checked
    separately to ensure the alias cannot point outside the configured root.
    """
    try:
        root_lexical = Path(os.path.abspath(os.fspath(ingest_root)))
        source_lexical = Path(os.path.abspath(os.fspath(source_path)))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise IngestFolderLabelError("Ingest source path is invalid") from exc
    try:
        relative = source_lexical.relative_to(root_lexical)
    except ValueError:
        # Acquisition and other explicit imports can use sources outside the
        # watched folder. They do not have a folder-derived value.
        return None
    try:
        root_real = Path(ingest_root).resolve(strict=True)
        source_real = Path(source_path).resolve(strict=True)
        source_real.relative_to(root_real)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise IngestFolderLabelError(
            "Ingest source must resolve to a file inside the configured ingest folder"
        ) from exc

    parts = relative.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise IngestFolderLabelError("Ingest source path is invalid")
    folders = parts[:-1]
    selected = folders if nested else folders[:1]
    return [part for part in selected if part.strip()]


def additive_values(existing_values, new_values):
    """Append only distinct values, preserving existing spelling and order."""
    values = list(existing_values or [])
    known = {
        str(value).strip().casefold()
        for value in values
        if value is not None and str(value).strip()
    }
    additions = []
    for raw in new_values or []:
        value = str(raw).strip()
        key = value.casefold()
        if value and key not in known:
            known.add(key)
            additions.append(value)
    return values + additions
