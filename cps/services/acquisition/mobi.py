# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded admission for the direct MOBI 6 acquisition slice.

This checks container framing and a small set of format-family markers.  It
does not decompress or certify that a book can be rendered or imported.
"""

import os
import stat
import struct


class MobiError(ValueError):
    """A source was refused by the MOBI preflight."""


_PDB_HEADER_SIZE = 78
_MAX_RECORDS = 10_000
_MAX_FIRST_RECORD = 2 * 1024 * 1024
_MAX_TEXT_LENGTH = 200 * 1024 * 1024


def _invalid():
    return MobiError('invalid_mobi')


def _read_exact(file, size):
    data = file.read(size)
    if len(data) != size:
        raise _invalid()
    return data


def _check_exth(record, offset):
    """Validate bounded EXTH framing and reject a declared KF8 companion."""
    if offset + 12 > len(record) or record[offset:offset + 4] != b'EXTH':
        raise _invalid()
    length, count = struct.unpack_from('>II', record, offset + 4)
    end = offset + length
    if length < 12 or end > len(record):
        raise _invalid()

    cursor = offset + 12
    for _ in range(count):
        if cursor + 8 > end:
            raise _invalid()
        kind, item_length = struct.unpack_from('>II', record, cursor)
        if item_length < 8 or cursor + item_length > end:
            raise _invalid()
        if kind == 121:
            if item_length != 12:
                raise _invalid()
            companion = struct.unpack_from('>I', record, cursor + 8)[0]
            if companion != 0xFFFFFFFF:
                raise MobiError('unsupported_book_format')
        cursor += item_length
    if cursor != end:
        raise _invalid()


def validate_mobi(path, *, max_bytes=100 * 1024 * 1024):
    """Return ``'mobi'`` for conservatively framed, ordinary DRM-free MOBI6.

    Only bounded portions are read: the fixed PDB header, its record table,
    and record zero (capped at 2 MiB).  No book text is read or decompressed.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise MobiError('invalid_book_size')

    flags = os.O_RDONLY
    if hasattr(os, 'O_BINARY'):
        flags |= os.O_BINARY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    if hasattr(os, 'O_NONBLOCK'):
        flags |= os.O_NONBLOCK
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise MobiError('source_not_regular')
        fd = os.open(os.fspath(path), flags)
    except (OSError, TypeError, ValueError):
        raise MobiError('source_not_regular') from None

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise MobiError('source_not_regular')
        size = info.st_size
        if size > max_bytes:
            raise MobiError('invalid_book_size')
        if size == 0:
            raise _invalid()

        with os.fdopen(fd, 'rb', closefd=False) as file:
            header = _read_exact(file, _PDB_HEADER_SIZE)
            if header[60:68] == b'TEXTREAD':
                raise MobiError('unsupported_book_format')
            if header[60:68] != b'BOOKMOBI':
                raise _invalid()

            app_info, sort_info = struct.unpack_from('>II', header, 52)
            next_list = struct.unpack_from('>I', header, 72)[0]
            if app_info or sort_info or next_list:
                raise _invalid()
            record_count = struct.unpack_from('>H', header, 76)[0]
            if not 1 <= record_count <= _MAX_RECORDS:
                raise _invalid()
            table_size = record_count * 8
            table_end = _PDB_HEADER_SIZE + table_size
            if table_end > size:
                raise _invalid()
            table = _read_exact(file, table_size)

            offsets = [struct.unpack_from('>I', table, index * 8)[0]
                       for index in range(record_count)]
            previous = table_end - 1
            for offset in offsets:
                if offset <= previous or offset >= size:
                    raise _invalid()
                previous = offset

            first_size = (offsets[1] if record_count > 1 else size) - offsets[0]
            if not 16 <= first_size <= _MAX_FIRST_RECORD:
                raise _invalid()
            file.seek(offsets[0])
            record = _read_exact(file, first_size)

        if record[16:20] != b'MOBI' or len(record) < 148:
            raise _invalid()
        mobi_length, mobi_type, _encoding, _uid, generator_version = struct.unpack_from('>IIIII', record, 20)
        format_version = struct.unpack_from('>I', record, 104)[0]
        if format_version != 6:
            raise MobiError('unsupported_book_format')
        if generator_version < format_version:
            raise _invalid()
        if mobi_length < 132 or 16 + mobi_length > len(record):
            raise _invalid()
        if mobi_type != 2:
            raise MobiError('unsupported_book_format')

        compression, _unused, text_length, text_records, record_size, _position = struct.unpack_from(
            '>HHIHHI', record, 0)
        encryption = struct.unpack_from('>H', record, 12)[0]
        if encryption != 0:
            raise MobiError('unsupported_book_format')
        if compression not in (1, 2):
            raise MobiError('unsupported_book_format')
        if text_length == 0 or text_length > _MAX_TEXT_LENGTH:
            raise _invalid()
        if not 1 <= text_records <= record_count - 1 or not 1 <= record_size <= 4096:
            raise _invalid()

        exth_offset = 16 + mobi_length
        # These offsets are from the start of the PalmDOC record, including
        # its 16-byte prefix, as in Calibre's BookHeader reader.
        exth_flags = struct.unpack_from('>I', record, 128)[0]
        full_name_offset, full_name_length = struct.unpack_from('>II', record, 84)
        if (full_name_offset < exth_offset or not full_name_length
                or full_name_offset + full_name_length > len(record)):
            raise _invalid()
        if exth_flags & 0x40 or record[exth_offset:exth_offset + 4] == b'EXTH':
            _check_exth(record, exth_offset)
        return 'mobi'
    except OSError:
        raise MobiError('source_not_regular') from None
    finally:
        os.close(fd)
