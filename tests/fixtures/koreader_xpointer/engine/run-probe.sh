#!/bin/sh
# Run probe.lua inside KOReader's own crengine (see ../README section in
# tests/fixtures/README.md). Prints the probe's JSON on stdout.
#
# usage: KOREADER_PROBE_DIR=/abs/dir run-probe.sh <epub-abs-path> <mode> [input-json-abs] > out.json
#   KOREADER_PROBE_DIR holds the extracted KOReader linux-arm64 release
#   (its lib/koreader tree) plus a copy of probe.lua.
set -eu
EPUB="$1"; MODE="$2"; IN="${3:-}"
KO="${KOREADER_PROBE_DIR:?set KOREADER_PROBE_DIR}"
EDIR=$(dirname "$EPUB"); EBASE=$(basename "$EPUB")
ARGS=""
MOUNT_IN=""
if [ -n "$IN" ]; then MOUNT_IN="-v $(dirname "$IN"):/in:ro"; ARGS="/in/$(basename "$IN")"; fi
# shellcheck disable=SC2086
docker run --rm --platform linux/arm64 -v "$KO:/ko" -v "$EDIR:/ep:ro" $MOUNT_IN \
  -w /ko/lib/koreader -e HOME=/tmp python:3.12-slim \
  sh -c "./luajit /ko/probe.lua '/ep/$EBASE' $MODE $ARGS 2>/dev/null | sed -n 's/^@@JSON@@//p'"
