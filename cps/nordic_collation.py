# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional Nordic collation using ICU already supplied by the platform.

No package is installed or required by this adapter. Other languages keep the
existing collation policy. Native keys are transient, never stored in a DB.
"""
import ctypes
import ctypes.util
from functools import lru_cache
import logging
import re
import sys
import threading
import weakref

_LOG = logging.getLogger(__name__)
_LOCAL = threading.local()
_LANGUAGES = frozenset(('sv', 'fi', 'da', 'nb', 'nn'))
_MAX_KEY_BYTES = 16 * 1024 * 1024


class NativeUnavailable(RuntimeError):
    """The platform does not supply the required native capability."""


class NativeKeyError(RuntimeError):
    """Fail the query rather than mix native BLOB and fallback TEXT keys."""


class _API:
    def __init__(self):
        name = ctypes.util.find_library('icui18n') or ctypes.util.find_library('icucore')
        if not name:
            raise NativeUnavailable('ICU shared library not found')
        self.library = ctypes.CDLL(name)
        match = re.search(r'\.so\.(\d+)(?:\.|$)', name)
        suffix = '_' + match.group(1) if match else ''
        pointer, integer = ctypes.c_void_p, ctypes.c_int32
        chars = ctypes.POINTER(ctypes.c_uint16)
        error = ctypes.POINTER(integer)

        def bind(name, arguments, result):
            function = getattr(self.library, name + suffix)
            function.argtypes = arguments
            function.restype = result
            return function

        self.open = bind('ucol_open', [ctypes.c_char_p, error], pointer)
        self.close = bind('ucol_close', [pointer], None)
        self.strength = bind('ucol_setStrength', [pointer, integer], None)
        self.attribute = bind('ucol_setAttribute', [pointer, integer, integer, error], None)
        self.key = bind('ucol_getSortKey', [pointer, chars, integer,
                                        ctypes.POINTER(ctypes.c_uint8), integer], integer)
        self.data_open = bind('ulocdata_open', [ctypes.c_char_p, error], pointer)
        self.data_close = bind('ulocdata_close', [pointer], None)
        self.exemplars = bind('ulocdata_getExemplarSet',
                             [pointer, pointer, integer, integer, error], pointer)
        self.set_close = bind('uset_close', [pointer], None)
        self.item_count = bind('uset_getItemCount', [pointer], integer)
        self.item = bind('uset_getItem', [pointer, integer, error, error,
                                       chars, integer, error], integer)


@lru_cache(maxsize=1)
def _api():
    try:
        return _API()
    except (OSError, AttributeError, NativeUnavailable) as error:
        _LOG.warning('Nordic sorting unavailable; retaining legacy order: %s', error)
        return None


def _check(status):
    # Negative ICU codes are warnings. Tailoring is checked separately below.
    if status.value > 0:
        raise NativeUnavailable('ICU initialization error %s' % status.value)


class _Context:
    def __init__(self, api, language):
        self.api = api
        status = ctypes.c_int32()
        self.collator = api.open(language.encode('ascii'), ctypes.byref(status))
        if not self.collator:
            raise NativeUnavailable('ICU collator is null')
        self._cleanup = weakref.finalize(self, api.close, self.collator)
        try:
            _check(status)
            api.strength(self.collator, 0)  # UCOL_PRIMARY
            status = ctypes.c_int32()
            # UCOL_NORMALIZATION_MODE=4, UCOL_ON=17: also normalize non-FCD text.
            api.attribute(self.collator, 4, 17, ctypes.byref(status))
            _check(status)
            self.labels = self._labels(language)
            tail = ('Z', 'Å', 'Ä', 'Ö') if language in ('sv', 'fi') else ('Z', 'Æ', 'Ø', 'Å')
            keys = [self.sort_key(letter) for letter in tail]
            if not set(tail).issubset(self.labels) or any(a >= b for a, b in zip(keys, keys[1:])):
                raise NativeUnavailable('ICU Nordic tailoring/index data missing')
            self.label_keys = sorted(((label, self.sort_key(label)) for label in self.labels),
                                     key=lambda pair: len(pair[1]), reverse=True)
        except Exception:
            self._cleanup()
            raise

    def sort_key(self, value):
        encoding = 'utf-16-le' if sys.byteorder == 'little' else 'utf-16-be'
        raw = value.encode(encoding, errors='surrogatepass')
        source = (ctypes.c_uint16 * (len(raw) // 2)).from_buffer_copy(raw)
        capacity = min(_MAX_KEY_BYTES, max(32, len(raw) * 2 + 16))
        for _ in range(2):
            output = (ctypes.c_uint8 * capacity)()
            required = self.api.key(self.collator, source, len(source), output, capacity)
            if required <= 0 or required > _MAX_KEY_BYTES:
                raise NativeKeyError('ICU could not produce a bounded sort key')
            if required > capacity:
                capacity = required
                continue
            if output[required - 1] != 0:
                raise NativeKeyError('ICU sort key lacks its terminator')
            return bytes(output[:required - 1])
        raise NativeKeyError('ICU sort-key size changed during allocation')

    def _labels(self, language):
        status = ctypes.c_int32()
        data = self.api.data_open(language.encode('ascii'), ctypes.byref(status))
        if not data:
            raise NativeUnavailable('ICU locale data is null')
        labels, charset = [], None
        try:
            _check(status)
            status = ctypes.c_int32()
            charset = self.api.exemplars(data, None, 0, 2, ctypes.byref(status))  # ES_INDEX
            _check(status)
            if not charset:
                raise NativeUnavailable('ICU index exemplar set is null')
            count = self.api.item_count(charset)
            if not 0 < count <= 128:
                raise NativeUnavailable('Unexpected Nordic exemplar set size')
            for index in range(count):
                start, end, status = ctypes.c_int32(), ctypes.c_int32(), ctypes.c_int32()
                buffer = (ctypes.c_uint16 * 64)()
                length = self.api.item(charset, index, ctypes.byref(start), ctypes.byref(end),
                                       buffer, len(buffer), ctypes.byref(status))
                _check(status)
                if not 0 <= length <= len(buffer):
                    raise NativeUnavailable('Unexpected Nordic index label size')
                if length:
                    encoding = 'utf-16-le' if sys.byteorder == 'little' else 'utf-16-be'
                    labels.append(bytes(buffer)[:length * 2].decode(encoding))
                elif 0 <= start.value <= end.value <= 0x10ffff and end.value - start.value < 128:
                    labels.extend(chr(point) for point in range(start.value, end.value + 1))
                else:
                    raise NativeUnavailable('Unexpected Nordic index label range')
            return labels
        finally:
            if charset:
                self.api.set_close(charset)
            self.api.data_close(data)

    def initial(self, value):
        key = self.sort_key(value)
        for label, prefix in self.label_keys:
            if key.startswith(prefix):
                return label
        return None  # Preserve existing non-Latin/numeric bucket policy in caller.


def context(language):
    """At most five native contexts per execution owner; no request state."""
    if language not in _LANGUAGES:
        return None
    contexts = getattr(_LOCAL, 'contexts', None)
    if contexts is None:
        contexts = _LOCAL.contexts = {}
    if language not in contexts:
        api = _api()
        try:
            contexts[language] = _Context(api, language) if api else None
        except (NativeUnavailable, NativeKeyError, UnicodeError) as error:
            _LOG.warning('Nordic %s sorting unavailable; retaining legacy order: %s', language, error)
            contexts[language] = None
    return contexts[language]
