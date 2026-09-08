"""The Highlights payload must name assignable devices before their first assignment."""

from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.mark.parametrize("has_annotation", [False, True])
def test_payload_includes_unreferenced_assignable_devices_and_excludes_other_users(monkeypatch, has_annotation):
    from cps import annotations as ann, ub

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        monkeypatch.setattr(ub, "session", session)
        monkeypatch.setattr(ann, "current_user", SimpleNamespace(id=1))
        monkeypatch.setattr(ann, "_resolve_book_or_404", lambda _: SimpleNamespace(id=223))
        monkeypatch.setattr(ann, "_resolve_annotation_anchor", lambda *_: (None, "unresolved"))
        for public_id, owner, active, label in [
            ("origin", 1, False, "Old reader"),
            ("assignable", 1, True, "PocketBook"),
            ("foreign", 2, True, "Other user's reader"),
        ]:
            session.add(ub.Device(public_id=public_id, user_id=owner, kind="koreader",
                                  display_name=label, active=active, created_by="auto"))
        session.flush()
        rows = []
        if has_annotation:
            row = ub.Annotation(user_id=1, book_id=223, annotation_id="highlight",
                                source="koreader", origin_device_id=session.query(ub.Device).filter_by(public_id="origin").one().id)
            session.add(row)
            rows.append(row)
        session.commit()
        monkeypatch.setattr(ann, "_load_user_annotations", lambda *_: rows)
        app = flask.Flask(__name__)
        app.register_blueprint(ann.annotations_bp)
        with app.test_request_context("/annotations/223/data.json"):
            response = ann.annotations_data.__wrapped__(223)
        body = response.get_json()
        assert body["devices"]["assignable"]["label"] == "PocketBook"
        assert "foreign" not in body["devices"]
        if has_annotation:
            assert body["annotations"][0]["origin_device_id"] == "origin"
        # At capacity, existing attribution must survive; unreferenced
        # assignment choices only use the remaining space.
        monkeypatch.setattr(ann, "MAX_DEVICE_LIST_LIMIT", 1)
        with app.test_request_context("/annotations/223/data.json"):
            bounded = ann.annotations_data.__wrapped__(223).get_json()
        assert list(bounded["devices"]) == (["origin"] if has_annotation else ["assignable"])
        if has_annotation:
            assert bounded["annotations"][0]["origin_device_id"] == "origin"
    engine.dispose()
