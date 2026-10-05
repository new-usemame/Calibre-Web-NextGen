# SPDX-License-Identifier: GPL-3.0-or-later
"""Original legal MOBI framing; unsupported forms cannot reach processing."""
import importlib.util
from pathlib import Path
import struct
import os
import subprocess
import sys

import pytest


@pytest.fixture
def mobi():
    path = Path(__file__).resolve().parents[2] / 'cps/services/acquisition/mobi.py'
    spec = importlib.util.spec_from_file_location('acquisition_mobi_preflight', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def original():
    return (Path(__file__).resolve().parents[1] / 'fixtures/sample_books/test_original_direct.mobi').read_bytes()


def offsets(data):
    return [struct.unpack_from('>I', data, 78 + i * 8)[0]
            for i in range(struct.unpack_from('>H', data, 76)[0])]


def save(tmp_path, data):
    path = tmp_path / 'source.part'
    path.write_bytes(data)
    return path


@pytest.mark.parametrize('fixture', ['test_original_direct.mobi', 'test_original_direct_uncompressed.mobi'])
def test_original_legal_mobi6_is_admitted_without_changing_bytes(mobi, original, tmp_path, fixture):
    original = (Path(__file__).resolve().parents[1] / 'fixtures/sample_books' / fixture).read_bytes()
    path = save(tmp_path, original)
    assert mobi.validate_mobi(path) == 'mobi'
    assert path.read_bytes() == original


@pytest.mark.parametrize('fault', ['html', 'truncated-table', 'empty', 'no-records',
    'table-overlap', 'duplicate-offset', 'descending-offset', 'offset-past-end',
    'first-header-truncated', 'wrong-header-magic', 'header-past-record',
    'text-records-past-end', 'text-expansion-limit', 'record-size-limit', 'bad-exth'])
def test_forged_or_truncated_packages_are_refused(mobi, original, tmp_path, fault):
    data = bytearray(original); records = offsets(data); first = records[0]
    if fault == 'html': data = bytearray(b'<html>provider error</html>')
    elif fault == 'empty': data = bytearray()
    elif fault == 'truncated-table': data = data[:80]
    elif fault == 'no-records': struct.pack_into('>H', data, 76, 0)
    elif fault == 'table-overlap': struct.pack_into('>I', data, 78, 78)
    elif fault == 'duplicate-offset': struct.pack_into('>I', data, 86, first)
    elif fault == 'descending-offset': struct.pack_into('>I', data, 86, first - 1)
    elif fault == 'offset-past-end': struct.pack_into('>I', data, 78, len(data) + 1)
    elif fault == 'first-header-truncated': struct.pack_into('>I', data, 86, first + 24)
    elif fault == 'wrong-header-magic': data[first + 16:first + 20] = b'NOPE'
    elif fault == 'header-past-record': struct.pack_into('>I', data, first + 20, len(data))
    elif fault == 'text-records-past-end': struct.pack_into('>H', data, first + 8, 65535)
    elif fault == 'text-expansion-limit': struct.pack_into('>I', data, first + 4, 201 * 1024 * 1024)
    elif fault == 'record-size-limit': struct.pack_into('>H', data, first + 10, 65535)
    else:
        exth = first + 16 + struct.unpack_from('>I', data, first + 20)[0]
        data[exth:exth + 4] = b'NOPE'
    with pytest.raises(mobi.MobiError, match='invalid_mobi'):
        mobi.validate_mobi(save(tmp_path, data))


@pytest.mark.parametrize('fault', ['encrypted', 'kf8', 'huff', 'palmdoc-only', 'hybrid'])
def test_unsupported_binary_families_are_not_advertised_as_plain_mobi6(mobi, original, tmp_path, fault):
    data = bytearray(original); first = offsets(data)[0]
    if fault == 'encrypted': struct.pack_into('>H', data, first + 12, 2)
    elif fault == 'kf8': struct.pack_into('>I', data, first + 104, 8)
    elif fault == 'huff': struct.pack_into('>H', data, first, 17480)
    elif fault == 'palmdoc-only': data[60:68] = b'TEXTREAD'
    else:
        exth = first + 16 + struct.unpack_from('>I', data, first + 20)[0]
        # A complete EXTH 121 payload advertises another MOBI/KF8 header.
        data[exth:exth + 24] = b'EXTH' + struct.pack('>IIIII', 24, 1, 121, 12, 3)
    with pytest.raises(mobi.MobiError, match='unsupported_book_format'):
        mobi.validate_mobi(save(tmp_path, data))


def test_newer_generator_does_not_misclassify_an_ordinary_mobi6_book(mobi, original, tmp_path):
    data = bytearray(original); first = offsets(data)[0]
    # The generator version at36 may exceed the format version at104.
    struct.pack_into('>I', data, first + 36, 8)
    assert mobi.validate_mobi(save(tmp_path, data)) == 'mobi'


def test_size_policy_and_regular_file_boundary_fail_closed(mobi, original, tmp_path):
    path = save(tmp_path, original)
    with pytest.raises(mobi.MobiError, match='invalid_book_size'):
        mobi.validate_mobi(path, max_bytes=len(original) - 1)
    link = tmp_path / 'link.mobi'; link.symlink_to(path)
    with pytest.raises(mobi.MobiError, match='source_not_regular'):
        mobi.validate_mobi(link)
    with pytest.raises(mobi.MobiError, match='source_not_regular'):
        mobi.validate_mobi(tmp_path)


@pytest.mark.parametrize('fault', ['flagged-missing-exth', 'title-outside-record', 'linked-database'])
def test_header_metadata_uses_documented_record_offsets(mobi, original, tmp_path, fault):
    data = bytearray(original); first = offsets(data)[0]
    if fault == 'flagged-missing-exth':
        exth = first + 16 + struct.unpack_from('>I', data, first + 20)[0]
        struct.pack_into('>I', data, first + 128, 0x40)
        struct.pack_into('>I', data, first + 84, exth - first)
        data[exth:exth + 4] = b'NOPE'
    elif fault == 'title-outside-record':
        struct.pack_into('>II', data, first + 84, len(data), 100)
    else:
        struct.pack_into('>I', data, 72, 1)
    with pytest.raises(mobi.MobiError, match='invalid_mobi'):
        mobi.validate_mobi(save(tmp_path, data))


def test_fifo_is_refused_without_waiting_for_a_writer(mobi, tmp_path):
    path = tmp_path / 'not-a-book'; os.mkfifo(path)
    module_path = Path(mobi.__file__)
    script = """import importlib.util,sys
spec=importlib.util.spec_from_file_location('fifo_mobi',sys.argv[1]); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
try: m.validate_mobi(sys.argv[2])
except m.MobiError as e: print(str(e));sys.exit(0 if str(e)=='source_not_regular' else 2)
sys.exit(3)
"""
    process = subprocess.Popen([sys.executable, '-c', script, str(module_path), str(path)], stdout=subprocess.PIPE, text=True)
    blocked = False
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        blocked = True
        # Unblock our own read-only FIFO child without killing it.
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK); os.close(fd)
        process.wait(timeout=3)
    output = process.stdout.read(); process.stdout.close()
    assert not blocked, 'Nonregular input waited for a writer before refusal'
    assert process.returncode == 0 and output.strip() == 'source_not_regular'
