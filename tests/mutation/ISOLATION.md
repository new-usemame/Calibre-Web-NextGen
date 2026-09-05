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
