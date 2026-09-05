# The mutation harness

Change one line of code, run the tests, report whether the tests noticed.

    mutate.py --seed COMMIT --file relative/file.py --old OLD --new NEW --test tests/test_file.py
    mutate.py --seed COMMIT --spec mutants.json

`--seed` is required and resolved once for the whole sweep. `--spec` takes a JSON list of
`{file, old, new, test}` items. Paths are repository-relative. Use the project's own venv
interpreter by absolute path.

## What isolation you get, and why it matters

Each phase — collection, baseline, mutant — runs against a **disposable worktree** at the pinned
seed, scrubbed clean before every phase. Your checkout is never written to.

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

Results are reported `UNVERIFIED`: the harness tells you what the tests did, and does not claim to
have proved anything beyond that. The exit status is nonzero so a script cannot mistake a diagnostic
run for a passing build.

## What it does not check

Outside the boundary: temporary directories, the virtualenv, your home directory, shared Git data,
Docker, databases, network services, ports, caches, and any process or daemon the tests hand work to
outside the phase. It is not hermetic and does not claim to be.

Selections that write to **shared Git state** — refs, common config, hooks, or another worktree —
are unsupported. A linked worktree has its own index and HEAD but shares those, and scrubbing does
not restore them. Tests may create and mutate their own temporary repositories.

**Known limitations, reproducible.** Code that hooks Python's import machinery can make the harness
report the wrong result: a repository `sitecustomize.py`, a code object compiled with the target's
filename, a `sys.meta_path` finder, or a `SourceFileLoader.source_to_code` override. All four are
reproduced by `leg7_probe.py`. They require the repository itself to carry that machinery, which
this one does not, and closing them is not worth the complexity for a test-quality tool — but if you
ever see a result you cannot explain, this is a place to look.

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
