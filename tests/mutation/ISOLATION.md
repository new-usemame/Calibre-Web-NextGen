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
