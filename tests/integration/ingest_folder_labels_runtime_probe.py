#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise folder labels through the built image's processor and Calibre API."""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from calibre_ingest_runtime_probe import _book_count, _processor


CALIBRE_PROBE = '''import json, sys
from calibre.db.legacy import LibraryDatabase
db = LibraryDatabase(sys.argv[1])
cache = db.new_api
if sys.argv[2] == 'create':
    cache.create_custom_column('owner', 'Owner', 'text', is_multiple=True)
elif sys.argv[2] == 'seed':
    cache.set_field('tags', {int(sys.argv[3]): ['Existing', 'james']})
else:
    result = []
    for book_id in sorted(cache.all_book_ids()):
        result.append({'id': book_id, 'tags': list(cache.field_for('tags', book_id)),
                       'owner': list(cache.field_for('#owner', book_id, default_value=[]) or [])})
    print('CWNG_FIELDS=' + json.dumps(result))
db.close()
'''


def fields(root, action='read', book_id=None):
    (root / 'library').mkdir(exist_ok=True)
    probe = root / 'fields.py'
    probe.write_text(CALIBRE_PROBE)
    command = ['calibre-debug', '-e', str(probe), '--', str(root / 'library'), action]
    if book_id is not None:
        command.append(str(book_id))
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    if action == 'read':
        line = next(line for line in result.stdout.splitlines() if line.startswith('CWNG_FIELDS='))
        return json.loads(line.split('=', 1)[1])


def source(root, fixture, relative):
    path = root / 'ingest' / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(fixture, path)
    return path


def ingest(module, root, source_path, *, target='tags', nested=False, converted=None):
    processor = _processor(module, root, source_path)
    processor.ingest_folder = str(root / 'ingest')
    processor.cwa_settings.update(auto_ingest_folder_label_target=target,
                                  auto_ingest_folder_label_nested=nested)
    processor.add_book_to_library(str(converted or source_path))
    return processor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--single-root', type=Path)
    parser.add_argument('--single-source', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, '/app/calibre-web-automated/scripts')
    import ingest_processor as module
    # object.__new__ in the companion deliberately bypasses startup. Load the
    # production lock just as initialize_runtime does before testing writers.
    module._load_fork_cps_imports()
    assert module.metadata_db_write_lock.__module__ == 'cps.services.calibre_db_lock'

    if args.single_root:
        # Direct calls bypass the s6 service's exit-2 retry queue. Cooperate with
        # the same typed pre-commit busy result while retaining the exact input;
        # ordinary/terminal import failures must still escape immediately.
        deadline = time.monotonic() + 90
        while True:
            try:
                ingest(module, args.single_root, args.single_source)
                return
            except (module.LibraryBusyError, TimeoutError):
                assert args.single_source.exists(), "busy import lost its source"
                if time.monotonic() >= deadline:
                    raise
                print('CWNG_RETRY_BUSY=' + str(args.single_source), flush=True)
                time.sleep(0.2)

    with tempfile.TemporaryDirectory(prefix='cwng-folder-labels-') as directory:
        root = Path(directory)
        assert fields(root) == []  # Initialize Calibre's schema as startup does.
        first = source(root, args.fixture, 'James/scifi/book.epub')
        ingest(module, root, first)
        initial = fields(root)
        assert len(initial) == 1 and initial[0]['tags'] == ['James'], initial
        book_id = initial[0]['id']
        fields(root, 'seed', book_id)
        second = source(root, args.fixture, 'Other/book.epub')
        ingest(module, root, second)
        replay = fields(root)
        assert len(replay) == 1 and set(replay[0]['tags']) == {'Existing', 'james', 'Other'}, replay
        ingest(module, root, first, nested=True)
        nested = fields(root)
        assert len(nested) == 1 and set(nested[0]['tags']) == {'Existing', 'james', 'Other', 'scifi'}, nested
        root_source = source(root, args.fixture, 'root.epub')
        ingest(module, root, root_source)
        assert fields(root) == nested
        disabled = source(root, args.fixture, 'Disabled/book.epub')
        ingest(module, root, disabled, target='disabled')
        assert fields(root) == nested
        fields(root, 'create')
        owner_source = source(root, args.fixture, 'Alice/fantasy/book.epub')
        converted = root / 'conversion-stage' / 'not-the-label.epub'
        converted.parent.mkdir()
        shutil.copy2(args.fixture, converted)
        ingest(module, root, owner_source, target='#owner', nested=True, converted=converted)
        custom = fields(root)
        assert len(custom) == 1 and set(custom[0]['owner']) == {'Alice', 'fantasy'}, custom
        assert custom[0]['tags'] == nested[0]['tags']
        stale = source(root, args.fixture, 'Private/book.epub')
        before = fields(root)
        failed = False
        try:
            ingest(module, root, stale, target='#removed')
        except (module.PreserveIngestSourceError, module.RetryIngestSourceError):
            failed = True
        assert failed and stale.exists() and fields(root) == before
        assert _book_count(root / 'library' / 'metadata.db') == 1
        first_time_stale = source(root, args.fixture, 'NewPrivate/new.epub')
        with first_time_stale.open('ab') as stream:
            stream.write(b'\nDistinct unimported stale-target source\n')
        failed_new = False
        try:
            ingest(module, root, first_time_stale, target='#removed')
        except (module.PreserveIngestSourceError, module.RetryIngestSourceError):
            failed_new = True
        assert failed_new and first_time_stale.exists() and fields(root) == before
        jobs = []
        for index in range(6):
            parallel_source = source(root, args.fixture, f'Parallel{index}/book{index}.epub')
            jobs.append(subprocess.Popen([sys.executable, __file__, '--fixture', str(args.fixture),
                        '--single-root', str(root), '--single-source', str(parallel_source)],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
        failures = []
        for index, job in enumerate(jobs):
            output, _ = job.communicate(timeout=120)
            print(f'CWNG_PARALLEL_{index}_EXIT={job.returncode}\n{output}', flush=True)
            if job.returncode:
                failures.append(output)
        assert not failures, '\n'.join(failures)
        parallel = fields(root)
        assert len(parallel) == 1 and all(f'Parallel{i}' in parallel[0]['tags'] for i in range(6)), parallel
        print('CWNG_FOLDER_LABELS=' + json.dumps({'initial': initial, 'duplicate_replay': replay,
              'nested': nested, 'custom_original_source': custom, 'stale_target_retained': failed,
              'first_time_stale_retained': failed_new, 'parallel': parallel,
              'root_noop': True, 'disabled_noop': True}, sort_keys=True))


if __name__ == '__main__':
    main()
