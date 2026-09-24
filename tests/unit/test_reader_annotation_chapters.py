# SPDX-License-Identifier: GPL-3.0-or-later
"""A native highlight belongs to one chapter, even when span IDs repeat."""
import json
from pathlib import Path
import subprocess

import pytest


@pytest.mark.unit
def test_classic_overlays_and_navigation_resolve_one_unambiguous_spine_section():
    """Drive real JS callbacks: wrong chapters stay unpainted, jumps use the
    resolved section href, and missing/ambiguous paths never guess a chapter.
    """
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([
        'node', str(root / 'tests/fixtures/js/classic_annotation_chapters.cjs'),
        str(root / 'cps/static/js/reading/annotations.js'),
    ], check=True, capture_output=True, text=True)
    observations = json.loads(result.stdout)
    assert observations['delayed'] == [['display', 'b/chapter.xhtml'], ['display', 'section-1-cfi']]
    assert observations['rejected'] == observations['delayed']
    assert observations['cfiOnly'] == [['display', 'stored-cfi']]
    assert observations['selection'] == {
        'start_kobospan': 'kobo.1.1', 'end_kobospan': 'kobo.1.1',
        'start_offset': 0, 'end_offset': 5, 'highlighted_text': 'hello',
        'chapter_filename': 'Text/chapter.xhtml',
    }
    assert observations['exact'] == [['display', 'b/chapter.xhtml'], ['paint', 'section-1-cfi']]
    assert observations['ambiguous'] == []
    assert observations['missing'] == []
    assert observations['encoded'] == [['paint', 'section-0-cfi'], ['display', 'section-0-cfi']]


@pytest.mark.unit
def test_classic_reader_identity_reuses_browser_storage_and_survives_denial():
    """Real JS must reuse the SPA UUID or preserve the write without an ID."""
    root = Path(__file__).resolve().parents[2]
    subprocess.run([
        'node', str(root / 'tests/fixtures/js/classic_reader_identity.cjs'), str(root),
    ], check=True, capture_output=True, text=True)
