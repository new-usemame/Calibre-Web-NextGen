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
