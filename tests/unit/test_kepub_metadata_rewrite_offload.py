"""Regression coverage for KEPUB metadata embedding request isolation."""

from types import SimpleNamespace

from cps import helper
from cps.services import parallel


def test_only_epub_zip_rewrite_is_offloaded(monkeypatch):
    """Context-bound preparation stays caller-side; pure zip I/O hops."""
    events = []
    in_offload = False

    class Query:
        def filter(self, *_args):
            events.append(("db", in_offload))
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return ["custom-column"]

    def run_blocking(job):
        nonlocal in_offload
        events.append(("offload", in_offload))
        in_offload = True
        try:
            return job()
        finally:
            in_offload = False

    monkeypatch.setattr(
        helper.calibre_db,
        "session",
        SimpleNamespace(query=lambda *_args: Query()),
    )
    monkeypatch.setattr(helper, "current_user", SimpleNamespace(locale="en"))
    monkeypatch.setattr(helper, "_", lambda text: events.append(("gettext", in_offload)) or text)
    monkeypatch.setattr(
        helper,
        "get_content_opf",
        lambda path: events.append(("read-opf", in_offload)) or ("tree", "content.opf"),
    )
    monkeypatch.setattr(
        helper,
        "create_new_metadata_backup",
        lambda *args, **kwargs: events.append(("create-metadata", in_offload)) or "package",
    )
    monkeypatch.setattr(
        helper,
        "replace_metadata",
        lambda tree, package: events.append(("replace-metadata", in_offload)) or b"content",
    )
    monkeypatch.setattr(helper, "get_temp_dir", lambda: "/tmp/calibre-web")
    monkeypatch.setattr(helper, "uuid4", lambda: "generated-id")
    monkeypatch.setattr(parallel, "run_blocking", run_blocking)
    monkeypatch.setattr(
        helper,
        "updateEpub",
        lambda *args: events.append(("update-epub", in_offload, args)),
    )

    result = helper.do_kepubify_metadata_replace(SimpleNamespace(), "/books/source.kepub")

    assert result == ("/tmp/calibre-web", "generated-id")
    assert [(event[0], event[1]) for event in events] == [
        ("db", False),
        ("db", False),
        ("read-opf", False),
        ("gettext", False),
        ("create-metadata", False),
        ("replace-metadata", False),
        ("offload", False),
        ("update-epub", True),
    ]
    assert events[-1][2] == (
        "/books/source.kepub",
        "/tmp/calibre-web/generated-id.kepub",
        "content.opf",
        b"content",
    )
