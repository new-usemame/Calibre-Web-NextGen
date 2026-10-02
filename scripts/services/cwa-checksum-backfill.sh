#!/usr/bin/env bash
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
#
# Fills in missing KOReader sync checksums once the web app has created their table.

# shellcheck source=_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

CWA_APP_PATHS="${CWA_APP_PATHS:-$CWA_SCRIPTS/app_paths.py}"
if ! library_path="$("$CWA_PYTHON" "${CWA_APP_PATHS}" calibre_library_dir)"; then
  echo "[cwa-checksum-backfill] ERROR: runtime library path resolver failed; refusing to inspect an unknown database"
  exit 1
fi
case "$library_path" in
  ""|"/"|*$'\n'*|*$'\r'*|*/../*|*/..)
    echo "[cwa-checksum-backfill] ERROR: runtime library path resolver returned an unsafe path; refusing to continue"
    exit 1 ;;
  /*) ;;
  *)
    echo "[cwa-checksum-backfill] ERROR: runtime library path resolver returned a non-absolute path; refusing to continue"
    exit 1 ;;
esac
metadata_db="${library_path%/}/metadata.db"
metadata_uri="$("$CWA_PYTHON" - "$metadata_db" <<'PY'
import sys
from pathlib import Path

print(Path(sys.argv[1]).as_uri() + "?mode=ro")
PY
)"

# Poll for database schema to be initialized (up to 30 seconds)
max_attempts=30
attempt=0
db_ready=false

while [ $attempt -lt $max_attempts ]; do
  if [ -f "$metadata_db" ] && sqlite3 "$metadata_uri" "SELECT name FROM sqlite_master WHERE type='table' AND name='book_format_checksums';" 2>/dev/null | grep -q "book_format_checksums"; then
    echo "[cwa-checksum-backfill] Database schema ready (attempt $((attempt + 1)))"
    db_ready=true
    break
  fi

  attempt=$((attempt + 1))
  sleep 1
done

if [ "$db_ready" = false ]; then
  echo "[cwa-checksum-backfill] WARNING: Database schema not ready after ${max_attempts}s, proceeding anyway..."
fi

echo "[cwa-checksum-backfill] Checking for missing KOReader sync checksums..."

# Run the checksum generation script (fills in missing checksums)
if cwa_run_as "$CWA_PYTHON" "$CWA_SCRIPTS/generate_book_checksums.py" --library-path "$library_path" --batch-size 50; then
  echo "[cwa-checksum-backfill] Checksum generation/backfill completed successfully"
else
  echo "[cwa-checksum-backfill] WARNING: Checksum generation encountered errors but continuing..."
  echo "[cwa-checksum-backfill] You can manually run: $CWA_PYTHON $CWA_SCRIPTS/generate_book_checksums.py"
fi

echo "[cwa-checksum-backfill] Service complete"
