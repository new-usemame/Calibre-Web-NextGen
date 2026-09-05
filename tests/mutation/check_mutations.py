# SPDX-License-Identifier: GPL-3.0-or-later
"""Sequential, temporary-copy mutation experiments for the soundness checks."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
FAST = ('integrity or collection_accounting or execution_rejects or execution_summary or '
        'execution_all_xfailed or execution_xfail_notrun or durable_evidence or durability_failure or '
        'evidence_cannot or noop_is_refused')
FAIL_TEXT = 'if phase.returncode == 1 and counts.get("failed", 0) < 1:'
FAIL_CALL = 'if phase.returncode == 1 and actual_failures < 1:'
CASES = [
    ('anchor', [('before.count(anchor) != 1', 'False')]),
    ('noop_prepare', [('if after == before:', 'if False:')]),
    ('stale_source', [('if target.read_bytes() != plan.before:', 'if False:')]),
    ('write_verification', [('if target.read_bytes() != plan.after:', 'if False:')]),
    ('collection_errors', [('if report["collection_errors"]:', 'if False:')]),
    ('selected_count', [('report["selected_count"] != len(nodes)', 'False')]),
    ('numerator', [('if selected != len(nodes):', 'if False:')]),
    ('denominator', [('if total != selected + len(report["deselected"]) or deselected != len(report["deselected"]):', 'if False:')]),
    ('exit_filter', [('if phase.returncode not in (0, 1):', 'if False:')]),
    ('summary_agreement', [('if counts.get(outcome, 0) != count:', 'if False:')]),
    ('failed_summary', [(FAIL_TEXT, 'if False:')]),
    ('failed_call', [(FAIL_CALL, 'if False:')]),
    ('COMPOSED_failed_summary_and_call', [(FAIL_TEXT, 'if False:'), (FAIL_CALL, 'if False:')]),
    ('zero_exit_failure', [('if phase.returncode == 0 and actual_failures:', 'if False:')]),
    ('baseline', [('if baseline and phase.returncode != 0:', 'if False:')]),
    ('execution_eligibility', [('if not supporting:', 'if False:')]),
    ('execution_count', [('executed = sum("call" in events for events in by_node.values())', 'executed = len(expected)')]),
    ('file_sync', [('            os.fsync(stream.fileno())', '            pass')]),
    ('atomic_rename', [('            os.replace(temporary, path)', '            shutil.copyfile(temporary, path)')]),
    ('directory_sync', [('                os.fsync(directory_fd)', '                pass')]),
    ('full_sync', [('                fcntl.fcntl(stream.fileno(), fcntl.F_FULLFSYNC)', '                pass')]),
    ('disposable_evidence', [('if directory.is_relative_to(sweep.entry.resolve()):', 'if False:')]),
    ('evidence_integrity', [('if digest != result.evidence_sha256:', 'if False:')]),
    ('nonzero_terminal', [('exit_code: int = field(default=1, init=False)', 'exit_code: int = field(default=0, init=False)')]),
    ('equivalent_integer_bound', [(FAIL_CALL, 'if phase.returncode == 1 and actual_failures <= 0:')]),
    ('noop_apply', [('if plan.after == plan.before:', 'if False:')]),
    ('schema_version', [('report["version"] != 1', 'False')]),
    ('baseline_mutated', [('baseline, report = _run_pytest(sweep, targets, environment, timeout, target=plan.relative)',
                          'baseline, report = _run_pytest(sweep, targets, environment, timeout, mutation=plan, target=plan.relative)')]),
    ('mutation_not_applied', [('if mutation is not None:', 'if False:')]),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', nargs='*')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = (HERE / 'mutate.py').read_text()
    output = Path(args.output)
    output.write_text('Mutation experiment — Mac/APFS only; retries 0; no maxfail.\n'
                      'Each variant runs in a temporary module copy with the absolute venv interpreter.\n'
                      'Test file: tests/unit/test_mutation_harness.py; flags -q -o addopts= '
                      '-p no:rerunfailures -p no:flaky --tb=no.\n'
                      'Default selection: ' + FAST + '\n'
                      'baseline_mutated/mutation_not_applied select execution_real_clean only.\n\n')
    with tempfile.TemporaryDirectory(prefix='mutation-checks-') as temp:
        directory = Path(temp) / 'tests' / 'mutation'
        directory.mkdir(parents=True)
        for name in ('provenance_probe.py', 'pytest_evidence.py'):
            shutil.copyfile(HERE / name, directory / name)
        module = directory / 'mutate.py'
        for name, replacements in CASES:
            if args.only and name not in args.only:
                continue
            modified = source
            for old, new in replacements:
                if modified.count(old) != 1:
                    raise RuntimeError('non-unique mutation anchor: ' + name)
                modified = modified.replace(old, new, 1)
            compile(modified, '<check-mutant>', 'exec')
            module.write_text(modified)
            selection = 'execution_real_clean' if name in ('baseline_mutated', 'mutation_not_applied') else FAST
            proc = subprocess.run([sys.executable, '-m', 'pytest', 'tests/unit/test_mutation_harness.py',
                '-k', selection, '-q', '-o', 'addopts=', '-p', 'no:rerunfailures', '-p', 'no:flaky', '--tb=no'],
                cwd=HERE.parents[1], capture_output=True, text=True, timeout=120,
                env={**os.environ, 'CWNG_CHECK_MUTANT': str(module), 'PYTEST_ADDOPTS': '', 'PYTHONDONTWRITEBYTECODE': '1'})
            summary = next((line for line in reversed(proc.stdout.splitlines())
                            if re.search(r'\d+ (?:passed|failed).* in ', line)), 'no test summary')
            status = {0: 'SURVIVOR', 1: 'KILLED'}.get(proc.returncode, 'INVALID')
            line = f'{name}: {status}; {summary}'
            print(line, flush=True)
            with output.open('a') as stream:
                stream.write(line + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            if status == 'INVALID':
                raise RuntimeError('mutation experiment failed to execute: ' + name)


if __name__ == '__main__':
    main()
