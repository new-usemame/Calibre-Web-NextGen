"""Execute cache path resolution and filesystem I/O in an isolated interpreter."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]

CACHE_IO = r"""
import importlib, json, pathlib, sys, types
# Load the real small modules without starting the application factory. This
# keeps the test limited to path resolution and cache I/O, not shared app.db.
package = types.ModuleType('cps')
package.__path__ = [str(pathlib.Path(sys.argv[1]) / 'cps')]
sys.modules['cps'] = package
constants = importlib.import_module('cps.constants')
fs = importlib.import_module('cps.fs')
expected = pathlib.Path(sys.argv[2])
assert pathlib.Path(constants.CACHE_DIR) == expected, (constants.CACHE_DIR, expected)
cache = fs.FileSystem()
path = pathlib.Path(cache.get_cache_file_path('abcdef.bin', 'derived'))
assert path == expected / 'derived' / 'ab' / 'abcdef.bin'
path.write_bytes(b'cached derived payload')
assert cache.get_cache_file_exists('abcdef.bin', 'derived')
assert path.read_bytes() == b'cached derived payload'
thumb = pathlib.Path(cache.get_cache_file_path('existing.jpg', constants.CACHE_TYPE_THUMBNAILS))
assert thumb == pathlib.Path(constants.CONFIG_DIR) / 'thumbnails' / 'existing.jpg'
assert thumb.read_bytes() == b'persisted thumbnail'
cache.delete_cache_file('abcdef.bin', 'derived')
assert not path.exists()
assert thumb.read_bytes() == b'persisted thumbnail'
print(json.dumps({'cache': str(expected), 'thumbnail': str(thumb)}))
"""


@pytest.mark.parametrize('explicit_override', [False, True])
def test_runtime_cache_io_stays_in_writable_config_and_preserves_thumbnails(tmp_path, explicit_override):
    config = tmp_path / 'config'
    thumbnails = config / 'thumbnails'
    thumbnails.mkdir(parents=True)
    (thumbnails / 'existing.jpg').write_bytes(b'persisted thumbnail')
    expected = tmp_path / 'explicit-cache' if explicit_override else config / 'cache'
    env = dict(os.environ, CALIBRE_DBPATH=str(config))
    env.pop('CACHE_DIR', None)
    if explicit_override:
        env['CACHE_DIR'] = str(expected)
    result = subprocess.run([sys.executable, '-c', CACHE_IO, str(ROOT), str(expected)],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['cache'] == str(expected)
