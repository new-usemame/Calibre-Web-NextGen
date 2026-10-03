# SPDX-License-Identifier: GPL-3.0-or-later
"""Library contention must end a conversion task with an explicit failure."""

from contextlib import contextmanager
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_busy_conversion_records_a_terminal_failure(monkeypatch, caplog):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    import convert_library

    @contextmanager
    def busy(*_args, **_kwargs):
        raise convert_library.ownership.LibraryBusyError("ingest owns maintenance")
        yield

    monkeypatch.setattr(convert_library.ownership, "maintenance", busy)
    try:
        result = convert_library.main()
    except RuntimeError:
        result = "escaped without a task result"
    assert result == 2, "busy conversion did not return a retryable failure"
    assert "NextGen Convert Library Service - Run Failed:" in caplog.text
    assert "NextGen Convert Library Service - Run Ended:" in caplog.text
    assert convert_library._maintenance_fd is None


@pytest.mark.parametrize("outcome", ["Run Failed", "Run Cancelled"])
def test_conversion_task_reports_failure_without_polling_forever(monkeypatch, tmp_path, outcome):
    import requests
    from cps.tasks import ops
    from cps.services.worker import STAT_FAIL

    task = ops.TaskConvertLibraryRun()
    task.log_path = str(tmp_path / "convert-library.log")
    Path(task.log_path).write_text(f"NextGen Convert Library Service - {outcome}: busy\n")
    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ops.helper, "get_internal_api_url", lambda _route: "http://fixture/")

    def unexpected_poll(_seconds):
        raise AssertionError("terminal conversion failure kept polling")

    monkeypatch.setattr(ops.time, "sleep", unexpected_poll)
    task.run(None)
    assert task.stat == STAT_FAIL, "failed conversion was reported as success"
