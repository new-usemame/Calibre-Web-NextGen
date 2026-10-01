#!/usr/bin/env bash
# Prepare writable runtime directories without trying to change ownership when
# the container was explicitly started as an arbitrary non-root UID.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  exit 0
fi

uid="$(id -u)"
if [ "$uid" = "0" ]; then
  for dir in "$@"; do
    # Preserve the normal LinuxServer image ownership contract.
    install -d -o abc -g abc "$dir"
  done
else
  echo "[cwa-init] running as uid ${uid} (not root); creating runtime directories with current ownership"
  for dir in "$@"; do
    mkdir -p -- "$dir"
  done
fi
