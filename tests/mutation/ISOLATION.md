# The mutation harness

Change one line of code, run the tests, report whether the tests noticed.

    python tests/mutation/mutate.py --backend container --seed COMMIT --file relative/file.py --old OLD --new NEW --test tests/test_file.py
    python tests/mutation/mutate.py --backend container --seed COMMIT --spec mutants.json

`--seed` is required and resolved once for the whole sweep. `--spec` takes a JSON list of
`{file, old, new, test}` items. Paths are repository-relative. Use the project's own venv
interpreter (Python 3.12+ with pytest installed); use its absolute path when needed.
Docker must have a local Linux image, by default `python:3.12` (`docker pull python:3.12`).
The harness copies pytest into each phase. Use `--image IMAGE` when your tests need
additional Python packages or system libraries installed in the image.

The container tests also require a reachable Linux Docker daemon and a local `python:3.12`
image. Run `docker pull python:3.12` before enabling them. They skip when the CLI, daemon,
or image is unavailable; they never pull an image automatically.

## What isolation you get, and why it matters

Each phase — collection, baseline, mutant — starts from the pinned commit. The container
backend copies a Git archive into a fresh container; the macOS backend scrubs a disposable
worktree before every phase. Your checkout is never written to. A failed baseline stops
the sweep before the mutation is tested.

This exists because of a real bug, not a hypothetical one. When the harness mutated the live tree
and restored it afterwards, leftover state from one phase could silently change the next phase's
result, so the tool would report a mutation as caught when it wasn't, or the reverse. Nobody had to
do anything wrong for that to happen.

The proof is a poison suite that deliberately leaks state through seven channels — ignored files,
tracked files, collateral writes, the index, HEAD, bytecode, and a delayed child process. Against a
shared tree each one changes a clean result; against the isolated lifecycle none of them do. Run it
with `CWNG_POISON_BOUNDARY=shared` to watch it fail on purpose.

Only committed state is used. There is no `--allow-dirty`; local uncommitted changes are neither
included nor reset.

## Backends

`--backend macos` (default) runs phases as local processes. `--backend container` runs each phase in
its own Docker container, which is how you run this on a Linux server rather than a developer Mac.
If Docker is not reachable the run stops with a message telling you so — it does not quietly fall
back to something weaker.

Container results are `caught` (an ordinary test failed after a passing baseline), `SURVIVED`
(the tests still passed), or `ERROR` (the sweep could not complete). Exit codes are 0 when
all mutations are caught, 1 when any survive, and 2 for an error. Skipped-only selections
and collection/setup failures are errors, not caught mutations.

The legacy macOS output retains `UNVERIFIED`: its cleanup cannot guarantee that detached
children stop writing after a phase. That backend still exits nonzero. Container results
omit this label because ordinary test outcomes already say what a reader needs.

Container scratch defaults to `/tmp`, independently of pytest's external-volume scratch.
Override it with `--scratch-dir DIR` (CLI) or `CWNG_DOCKER_SCRATCH` (CLI and tests), choosing
a writable directory shared with Docker. An unresponsive create fails after five seconds
with advice to check sharing. Each created container is labelled and removed at phase end.
The daemon must remain available for cleanup; do not forcibly kill the host runner.

`--timeout` bounds each test phase (default 1800 seconds). JSON results go to
`--evidence-dir DIR`, outside the source checkout; the default is temporary storage.
An error stops the sweep, and there is no automatic backend fallback.

## What it does not check

Container-local files and descendants are discarded. Only a dedicated output directory is
bind-mounted; the host virtualenv, source checkout, Git metadata and Docker socket are not.
Network access is disabled. Work delegated to external databases, network services, shared
ports, remote service managers or other daemons is not reset. This is not hermeticity.
The macOS backend also leaves host temporary files, the virtualenv, home and caches outside
its disposable worktree.

On macOS, selections that write to **shared Git state** — refs, common config, hooks, or another worktree —
are unsupported. A linked worktree has its own index and HEAD but shares those, and scrubbing does
not restore them. Tests may create and mutate their own temporary repositories.

**Known limitations, reproducible.** Code that hooks Python's import machinery can make the harness
report the wrong result: a repository `sitecustomize.py`, a code object compiled with the target's
filename, a `sys.meta_path` finder, or a `SourceFileLoader.source_to_code` override. All four are
reproduced by `leg7_probe.py`. The harness runs trusted code and specs; it does not try to
detect deliberate substitution of the code being tested.

On macOS a process that calls `setsid()` and clears its environment can outlive its phase. The
container backend does not have that problem.

## Recovering from the old design

The previous harness kept a journal in the system temporary directory at
`cwng-mutation/<repository-key>/active.json`, where the key is the first 20 hex digits of SHA-256
over the canonical repository path. If one exists, the CLI refuses to run rather than abandoning it.
Preserve the journal and any recovery copies it names, compare the recorded target against the
committed content, recover anything you need, then archive the journal out of the way. Nothing
rewrites or deletes it for you. A different `TMPDIR` can hide old state, so check under the original
one. There is no `--clear-journal`.
