# SPDX-License-Identifier: GPL-3.0-or-later
"""Native buffer failures and canonical/thread ownership at the ICU boundary."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

import pytest

from cps import nordic_collation as nordic
from cps.unicode_collation import unicode_initial, unicode_sort_key


def test_normalization_and_nordic_index_do_not_collapse_distinct_letters():
    for language in ('sv', 'fi', 'da', 'nb', 'nn'):
        assert unicode_sort_key('Åland', language) == unicode_sort_key('A\u030aland', language)
        assert unicode_sort_key('Ängel', language) == unicode_sort_key('A\u0308ngel', language)
        # Marks out of canonical order must also form one primary cohort.
        assert unicode_sort_key('A\u0301\u0323', language) == unicode_sort_key('A\u0323\u0301', language)
        assert unicode_initial('A\u030aland', language) == 'Å'
    assert unicode_sort_key('Å', 'sv') != unicode_sort_key('A', 'sv')
    assert unicode_initial('Ängel', 'fi') == 'Ä'
    assert unicode_initial('Ægir', 'da') == 'Æ'
    assert unicode_initial('Aalto', 'nb') == 'Å'
    assert unicode_initial('きく', 'sv') == 'き'


def test_parallel_readers_keep_separate_native_owners_and_one_language_policy():
    barrier = threading.Barrier(2)
    def read(language):
        first = nordic.context(language)
        barrier.wait()
        return id(first), [unicode_sort_key('Åland', language) for _ in range(100)], first is nordic.context(language)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(read, ['sv', 'sv']))
    assert a[0] != b[0]
    assert a[1] == b[1]
    assert a[2] and b[2]


def test_missing_icu_keeps_the_complete_legacy_cohort_and_warns(monkeypatch, caplog):
    monkeypatch.setattr(nordic, '_LOCAL', threading.local())
    def unavailable():
        raise nordic.NativeUnavailable('missing platform library')
    monkeypatch.setattr(nordic, '_API', unavailable)
    nordic._api.cache_clear()
    try:
        assert [unicode_sort_key(x, 'sv') for x in ('Aalto', 'Åland', 'Zulu')] == ['aalto', 'aland', 'zulu']
        assert 'retaining legacy order' in caplog.text
    finally:
        nordic._api.cache_clear()


@pytest.mark.parametrize('fault', ['zero', 'unbounded', 'unterminated'])
def test_bad_native_key_fails_instead_of_returning_mixed_type_fallback(fault):
    def key(collator, source, length, output, capacity):
        if fault == 'zero': return 0
        if fault == 'unbounded': return nordic._MAX_KEY_BYTES + 1
        output[0] = output[1] = 1
        return 2
    context = nordic._Context.__new__(nordic._Context)
    context.api = SimpleNamespace(key=key)
    context.collator = 1
    with pytest.raises(nordic.NativeKeyError):
        context.sort_key('Å')


def test_native_key_resize_uses_required_size_including_terminator():
    capacities = []
    def key(collator, source, length, output, capacity):
        capacities.append(capacity)
        if capacity < 64: return 64
        for i in range(63): output[i] = 1
        output[63] = 0
        return 64
    context = nordic._Context.__new__(nordic._Context)
    context.api = SimpleNamespace(key=key)
    context.collator = 1
    assert context.sort_key('Å') == b'\x01' * 63
    assert capacities == [32, 64]


def test_failed_native_initialization_releases_handles_immediately():
    closed = []
    context = nordic._Context.__new__(nordic._Context)
    def open_collator(language, status):
        status._obj.value = 1
        return 123
    api = SimpleNamespace(open=open_collator, close=closed.append)
    with pytest.raises(nordic.NativeUnavailable, match='initialization error'):
        context.__init__(api, 'sv')
    # Keep the failed object alive: eventual GC alone must not close this gate.
    assert closed == [123]
    assert not context._cleanup.alive


def test_missing_index_data_closes_locale_and_collator_handles():
    closed = []
    real_api = nordic._api()
    context = nordic._Context.__new__(nordic._Context)
    proxy = SimpleNamespace(**{name: getattr(real_api, name) for name in
        ('open', 'strength', 'attribute', 'key', 'data_open', 'data_close')})
    proxy.close = lambda handle: (closed.append('collator'), real_api.close(handle))
    proxy.data_close = lambda handle: (closed.append('locale'), real_api.data_close(handle))
    proxy.exemplars = lambda *args: None
    with pytest.raises(nordic.NativeUnavailable, match='index exemplar set is null'):
        context.__init__(proxy, 'sv')
    assert closed == ['locale', 'collator']
    assert not context._cleanup.alive
