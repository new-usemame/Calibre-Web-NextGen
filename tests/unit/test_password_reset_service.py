# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
def test_mail_enqueue_failure_rolls_back_without_committing_new_password():
    from cps import helper

    target = SimpleNamespace(id=2, name="reader", email="reader@example.test", password="old-hash")
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = target

    with patch.object(helper.ub, "session", session), \
         patch.object(helper.config, "get_mail_server_configured", return_value=True), \
         patch.object(helper.config, "config_password_min_length", 8, create=True), \
         patch.object(helper, "generate_random_password", return_value="Temp123!"), \
         patch.object(helper, "generate_password_hash", return_value="new-hash"), \
         patch.object(helper, "send_registration_mail", side_effect=RuntimeError("queue down")):
        result = helper.reset_password(2)

    assert result == (0, None)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()
