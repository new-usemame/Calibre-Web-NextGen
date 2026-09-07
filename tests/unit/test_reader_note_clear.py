# SPDX-License-Identifier: GPL-3.0-or-later
"""Clears are known empty values; omission must retain unknown provenance."""
import json
from types import SimpleNamespace

import pytest
from flask import Flask

from tests.unit.test_annotations_edit_delete_endpoint import memory_db, _seed


@pytest.mark.unit
def test_classic_null_patch_clears_but_omission_leaves_never_set_null(memory_db, monkeypatch):
    from cps import annotations as ann, ub

    book = SimpleNamespace(id=1)
    monkeypatch.setattr(ann, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ann, "_resolve_book_or_404", lambda _: book)
    monkeypatch.setattr(ann, "_observe_webreader_request_device", lambda: None)
    monkeypatch.setattr(ann, "_fanout_to_sync_targets", lambda *args: None)
    monkeypatch.setattr(ub, "session_commit", memory_db.commit)
    app = Flask(__name__)
    # Run the actual route body via HTTP; authentication is outside this contract.
    app.add_url_rule('/annotations/<int:book_id>/<annotation_id>',
                     view_func=ann.annotations_edit.__wrapped__, methods=['PATCH'])
    _seed(memory_db, annotation_id='cleared', note='Remember this')
    _seed(memory_db, annotation_id='never-set')
    client = app.test_client()
    response = client.patch('/annotations/1/cleared', json={'note_text': None})
    assert response.status_code == 200
    assert response.json['note_text'] == ''
    response = client.patch('/annotations/1/never-set', json={'highlight_color': 'green'})
    assert response.status_code == 200
    assert response.json['note_text'] is None
    memory_db.expire_all()
    values = dict(memory_db.query(ub.Annotation.annotation_id, ub.Annotation.note_text).all())
    assert values == {'cleared': '', 'never-set': None}
    # Current SPA/classic string-shaped clear has the same persistent result.
    assert client.patch('/annotations/1/cleared', json={'note_text': ''}).status_code == 200
    memory_db.expire_all()
    assert memory_db.query(ub.Annotation).filter_by(annotation_id='cleared').one().note_text == ''


@pytest.mark.unit
@pytest.mark.parametrize('anchor', [
    {'start_kobospan': 'kobo.1.1'},
    {'cfi_range': 'epubcfi(/6/4!/4/2,/1:0,/1:9)'},
], ids=['classic', 'spa'])
def test_create_distinguishes_explicit_empty_from_never_set(memory_db, anchor):
    from cps.annotations import create_annotation
    book = SimpleNamespace(id=1, uuid='book', data=[])
    values = []
    for note in ({'note_text': None}, {'note_text': ''}, {}):
        row = create_annotation({**anchor, **note}, user_id=7, book=book,
                                session=memory_db, commit=memory_db.commit)
        memory_db.refresh(row)
        values.append(row.note_text)
    assert values == ['', '', None]


@pytest.mark.unit
def test_empty_and_null_have_no_note_in_read_consumers(memory_db):
    from cps import annotations as ann
    from cps.services.annotation_portable import to_portable, validate_portable_payload
    from cps.services.kobo_annotation_authority import _fallback_object, _emergency_object

    row = _seed(memory_db)
    row.annotation_type = 'highlight'
    row.content_id = None
    def outputs():
        portable = to_portable(row)
        assert validate_portable_payload(portable) is None
        assert not portable['note_text']
        assert not json.loads(ann.render_json('Book', 1, 7, [row]))['annotations'][0]['note_text']
        assert not ann._data_json_row(row, None, None)['note_text']
        return (ann.render_markdown('Book', [row]), ann.render_csv([row]),
                _fallback_object(row, 'uuid'), _emergency_object(row, 'uuid'))
    absent = outputs()
    row.note_text = ''
    assert outputs() == absent


@pytest.mark.unit
@pytest.mark.parametrize('popup', ['create', 'edit'])
def test_classic_popup_sends_empty_string(popup):
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([
        'node', str(root / 'tests/fixtures/js/classic_note_payload.cjs'),
        str(root / 'cps/static/js/reading/annotations.js'), popup,
    ], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)['note_text'] == ''
