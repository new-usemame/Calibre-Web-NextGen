# SPDX-License-Identifier: GPL-3.0-or-later
import pytest
from tests.unit.test_share_place_kosync_gates import world, _register, _share, _put
from tests.unit.test_share_place_kosync_behavior import _listing

pytestmark = pytest.mark.unit

def test_share_opt_out_cannot_resume_shared_projection(world, monkeypatch):
    monkeypatch.setenv('CWNG_SHARE_PLACE_GATES', '1')
    world.position(world.reader, 2, 80)
    _put(world, 20, device_id='kindle-a', name='Kindle A')
    device = _register(world, 'kindle-a', 'Kindle A')
    _share(world, device, True)
    first = _listing(world, limit=1)
    assert first['next_cursor']
    _share(world, device, False)
    second = _listing(world, cursor=first['next_cursor'], limit=1)
    fresh = _listing(world)
    assert [b['progress'] for b in fresh['books'] if b['book_id']==2] == [None]
    assert second['books'][0]['progress'] is None, second
