# Diagnostic execution policy

The macOS backend reports UNVERIFIED observations and exits nonzero.
Git-writing selections that change shared refs, common Git configuration,
hooks, or another worktree are UNSUPPORTED. This includes mutation-induced
writes, not just writes in the normal implementation. Do not select them.

A detached linked worktree has a private index and HEAD, but shares refs and
common configuration. Reset/clean does not restore those shared resources.
The harness does not infer safety from source scanning: dynamic subprocess
commands and deliberately damaged code make that inference unreliable.
Tests may create and mutate their own independent temporary repositories.
They must not modify the sweep's shared Git state or external repositories.

Outside the boundary: temporary directories, the venv, home, common Git data,
Docker, databases, network, ports, caches, services and escaped processes.
This is an explicit unsupported-use policy, not enforcement of arbitrary writes.

## Legacy recovery

The CLI refuses an existing legacy journal, including malformed journals.
The old location is the system temporary directory, then
cwng-mutation/<repository-key>/active.json. The repository key is the first
20 hex digits of SHA-256 over os.fsencode(the canonical source repository path).
No journal or recovery copy is rewritten, restored, or deleted by the new CLI.

Preserve the journal, any recovery copies it names, and current source bytes.
Inspect the recorded target and compare it with the recovery copy and the
intended committed content. Recover any needed work before archiving the
journal outside the active location. Do not simply delete an unresolved journal.
The new CLI has no --clear-journal command and creates no recovery journal.
A different TMPDIR can hide old temporary state: use the original temporary
directory when checking for a previous run.

## Command line

Use the pinned absolute venv interpreter to run tests/mutation/mutate.py:

    mutate.py --seed COMMIT --file relative/file.py --old OLD --new NEW --test tests/test_file.py
    mutate.py --seed COMMIT --spec mutants.json --evidence-dir /chosen/external/directory

--repo defaults to the checkout containing the harness. --seed is required and
resolved once to a commit for the whole sweep. Local uncommitted changes are
neither included nor reset. There is no --allow-dirty mode.
Each specification item has file, old, new, and test (one target or a list).
v1 accepts repository-relative test paths/node IDs, not arbitrary pytest flags.
An ERROR stops the sweep. Diagnostics always exit 1, including passing selections.

--timeout defaults to 1800 seconds per execution phase. Provenance has its own
bounded watchdog. --evidence-dir must be outside the source checkout; by default
it is in the temporary cwng-mutation-isolated/<repository-key>/evidence directory,
where this key is the first 16 hex digits of SHA-256 over the canonical path's
UTF-8 encoding. Each output names its durable evidence file. Temporary storage
retention is outside this boundary; choose persistent external storage when needed.

The direct-mutation API, backup/restore path and old observation type are removed.
All CLI execution goes through IsolatedSweep and run_checked_mutation.

## Confirmed defeat vectors (known, reproducible, NOT fixed)

A repository that carries any of the following can make this backend report
`TESTS_PASSED` for a mutant that is in truth caught. All four are reproduced by
`tests/mutation/leg7_probe.py`; run it to see each subject beside its control.

1. `sitecustomize.py` in the repository. It runs in the child at interpreter
   startup, after the parent rechecks the mutated bytes and before the target is
   imported, so it can restore the original source.
2. A code object compiled with the target's filename. The execution witness
   accepts a matching frame filename, so `compile(src, target_path, "exec")`
   followed by `exec` satisfies it without the target ever being loaded.
3. A `sys.meta_path` loader that reads the intended mutated source and then
   compiles different code under the target's filename.
4. A `SourceFileLoader.source_to_code` replacement doing the same.

Vectors 3 and 4 also refute content hashing at the loader as a fix: both read the
intended mutant, so a read-hash matches, and then execute something else.

These are limitations of in-process observation, not oversights. Establishing
that the bytes an interpreter executed are the intended bytes is not achievable
by an observer living inside the process it observes. Under the diagnostic
contract above this backend never emits an authoritative verdict, so a defeated
observation stays UNVERIFIED and cannot become a `caught` or `SURVIVED`; the cost
is a misleading diagnostic, not a false verdict. The authoritative backend is a
separate, kernel-enforced execution boundary.

Scope note, corrected 2026-09-05 after judge review. An earlier version of this
paragraph claimed none of these can be produced by a mutation. **That was wrong
for three of the four.** `--new` is arbitrary unvalidated replacement text: the
CLI validates that `file`, `old` and `new` are strings and that paths are
repository-relative, but never constrains the replacement itself
(`mutate.py:73`, `_cli_mutants` at `:933-947`). So a mutation spec targeting any
executed `.py` can insert a `sys.meta_path` finder, a `source_to_code` override,
or a `compile`-with-target-filename call. Vectors 2, 3 and 4 are therefore
reachable by a mutation. Only vector 1 is not, because creating a *new*
`sitecustomize.py` file is outside what a byte replacement can do.

What does still hold is the contract above: this backend never emits an
authoritative verdict, so any of these produces a misleading UNVERIFIED
diagnostic rather than a false `caught`/`SURVIVED`.

The repository's own source carries none of this machinery — no
`sitecustomize.py`, no `usercustomize.py`, no `.pth` files, no `source_to_code`
override, no production `sys.meta_path` manipulation. But note that reassurance
is scoped to the repository while the vectors act on the *interpreter*: the venv
each phase runs under carries an editable-install finder live in `sys.meta_path`
and `.pth` startup hooks of its own. The harness defends that specific case with
a `PYTHONPATH` prepend and a three-shape preflight carrying its own negative
control, and `:15` already places the venv outside the isolation boundary.

## Import witness scope

The three preflight shapes witness cps package roots only. They do not establish
where an arbitrary mutation target was imported. Their collection can write
files, so its state is scrubbed and the mutant reapplied/rechecked before launch.

Measured baseline and mutant pytest interpreters separately profile actual
Python calls from the requested target's canonical source path. Missing target
execution, a matching relative module path outside the execution tree, or a
disabled profiler rejects the assessment. Targets must be Python source files.
An import performed only in a child process, native code, renamed foreign code,
or a target loaded before instrumentation is not a supported witness.
Same relative module paths from another location are conservatively rejected.
No import is forced merely to make this check pass. This witnesses observed
target code; it does not prove every dependency or execution path is confined.
Deliberate instrumentation tampering and tokenless escaped writers remain
outside this diagnostic boundary.

Frozen result objects prevent ordinary assignment only. They are not an authority
boundary: Python can forge their fields. Presentation checks the concrete result,
its diagnostic status/authority/exit fields, allowed signal and evidence digest,
then returns a literal nonzero status. A forged instance or duck-typed result is
rejected before presentation.
