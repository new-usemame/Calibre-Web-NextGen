# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The web reader's box is the account Browser's box, never a stray row's.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 15 ("The
web reader is the account's one ``webreader`` device", ``Browser``). OBSERVED
on the household (user 107, 2026-10-12): legacy inactive ``webreader`` rows
(all unchecked) have lower ids than the account Browser (checked). The resume
read asked "is the Browser checked?" of the first ``webreader`` row it found,
got an unchecked legacy row, and answered ``resume: null``: the web reader
reopened at its own old CFI instead of the newer shared place.

The account Browser is the row ``device_registry.upsert_webreader_device``
returns (``kind='webreader'``, ``created_by=ACCOUNT_BROWSER``). Every case
drives the real ``GET /api/v1/books/<id>/bookmark`` with the gates on.
"""

from datetime import timedelta

import pytest

from cps import ub
from tests.unit.kobo_state_world import read_column_class  # noqa: F401  (fixture)
from tests.unit.share_place_span_world import (  # noqa: F401  (fixture)
    _now, after_now, span_rows, sw,
)
from tests.unit.test_share_place_web_gates import (  # noqa: F401  (fixtures)
    browser, cfi_of, file_backed, opened, resume, rows, save, w,
)

pytestmark = pytest.mark.unit

SWITCH = "CWNG_SHARE_PLACE_GATES"


@pytest.fixture(autouse=True)
def gates_on(monkeypatch):
    monkeypatch.setenv(SWITCH, "1")


def stray_browsers(w, *, checked, count=2):
    """Legacy web-reader rows from before the account Browser existed:
    inactive aliases the browser-source migration left (created_by
    "browser-alias"), created first, so their ids are lower."""
    strays = []
    for index in range(count):
        device = ub.Device(user_id=w.reader.id, kind="webreader",
                           display_name="Browser %d" % index, active=False,
                           created_by="browser-alias", share_place=checked)
        w.session.add(device)
        strays.append(device)
    w.session.commit()
    return strays


def _newer_exact_shared_place(w, rows):
    """A checked device reports a newer place KOReader can name exactly."""
    w.koreader("koreader-1", "KOReader", checked=True)
    w.push(rows["late"]["xpointer"], 60, client_at=after_now(2))


def test_control_with_only_the_account_browser_a_checked_one_resumes(w, rows):
    """Today's behaviour (spec section 15, resume) with no legacy rows."""
    browser(w, checked=True)
    save(w, 20, cfi_of(w, rows["early"]))
    _newer_exact_shared_place(w, rows)

    served = resume(w)["resume"]

    assert served is not None and served["mode"] == "automatic"
    assert served["percentage"] == pytest.approx(60)
    assert served["cfi"] == cfi_of(w, rows["late"])


def test_control_with_only_the_account_browser_an_unchecked_one_is_not_resumed(w, rows):
    browser(w, checked=False)
    own = cfi_of(w, rows["early"])
    save(w, 20, own)
    _newer_exact_shared_place(w, rows)

    assert opened(w) == (own, None)


def test_a_checked_account_browser_resumes_past_older_unchecked_legacy_rows(w, rows):
    strays = stray_browsers(w, checked=False)
    me = browser(w, checked=True)
    assert all(stray.id < me.id for stray in strays)
    save(w, 20, cfi_of(w, rows["early"]))
    _newer_exact_shared_place(w, rows)

    served = resume(w)["resume"]

    assert served is not None, "the account Browser is checked"
    assert served["mode"] == "automatic"
    assert served["percentage"] == pytest.approx(60)
    assert served["cfi"] == cfi_of(w, rows["late"])


def test_an_unchecked_account_browser_is_not_resumed_by_a_checked_legacy_row(w, rows):
    stray_browsers(w, checked=True)
    browser(w, checked=False)
    own = cfi_of(w, rows["early"])
    save(w, 20, own)
    _newer_exact_shared_place(w, rows)

    assert opened(w) == (own, None)


def test_a_legacy_rows_own_position_does_not_hold_back_the_shared_place(w, rows):
    """The "is the shared place newer than mine?" comparison reads the
    account Browser's journal, not a legacy row's. A legacy row's place,
    newer than the shared report, must not suppress the resume."""
    from cps.services.place_journal import write_report
    (stray,) = stray_browsers(w, checked=False, count=1)
    # The legacy row's journal is the older row in the table (it predates the
    # account Browser), but carries a clock later than the shared report.
    later = _now() + timedelta(hours=1)
    write_report(device_id=stray.id, book_id=w.book.id, client_at=later,
                 received_at=later,
                 place=dict(progress_percent=90.0, cfi=None, location_source=None,
                            location_type=None, location_value=None),
                 status=None, status_supplied=False)
    w.session.commit()
    browser(w, checked=True)
    save(w, 20, cfi_of(w, rows["early"]))
    _newer_exact_shared_place(w, rows)

    served = resume(w)["resume"]

    assert served is not None and served["mode"] == "automatic"
    assert served["percentage"] == pytest.approx(60)
