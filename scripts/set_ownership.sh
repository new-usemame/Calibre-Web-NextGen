#!/bin/bash
# Set ownership of every directory CWA needs to write to, walking each once.
#
# Called by the cwa-init s6 unit at every container start.
#
# The list is a fixed floor (/config, plus the narrow set of app-tree dirs the
# runtime user writes) and whatever dirs.json declares. dirs.json ships
# calibre_library_dir=/calibre-library and
# tmp_conversion_dir=/config/.cwa_conversion_tmp, and the old inline version of
# this logic hardcoded /calibre-library and /config on top of that -- so every
# start chowned the whole library twice and re-walked a subtree of /config that
# /config's own recursive pass had already covered (#874).
#
# The floor is not optional. dirs.json declares neither /config nor the app-tree
# writables, and all are load-bearing:
#
#   * /config holds app.db and user_profiles.json, which cwa-init writes as root
#     *after* the early chown at the top of the unit. This pass is the only thing
#     that hands them to the runtime user; without it, profile-picture uploads
#     fail with EACCES on a fresh install.
#
# The app tree at /app/calibre-web-automated ships from the image owned by the
# build-time abc (uid 911); the linuxserver base then usermods abc to $PUID at
# runtime, which orphans the tree for any install using the documented PUID.
# The whole tree used to be chowned -R here to repair that -- ~1820 entries,
# 2.5-26s of wall time on a fresh container, and on overlayfs every chown copies
# the file up into the writable layer, so it cost disk too (#941). Almost none
# of it needs re-owning: the static tree is world-readable and every directory
# world-traversable (`find ... ! -perm -o+r` and `... -type d ! -perm -o+x` are
# both empty), so Python imports and template reads work regardless of owner.
# Only the dirs the runtime user *writes* under the app tree need ownership.
#
# cps/cache is such a dir; it is created and chowned earlier in the
# cwa-init unit (before first-run app.db creation needs it), so it is not
# repeated here. The rest of the tree (dirs.json, the code) is written only by
# root or never, so orphaned build-time ownership is harmless.
#
# scripts/auto_library.py also rewrites dirs.json in place at runtime, so a crash
# mid-write can leave it unparseable -- which must not be able to silently reduce
# this pass to nothing.
#
# Every path is env-overridable so the logic is testable without a container;
# see tests/unit/test_set_ownership.py.

set -uo pipefail

CWA_APP_ROOT="${CWA_APP_ROOT:-/app/calibre-web-automated}"
CWA_CONFIG_ROOT="${CWA_CONFIG_ROOT:-/config}"
CWA_DIRS_JSON="${CWA_DIRS_JSON:-${CWA_APP_ROOT}/dirs.json}"
CWA_OWNER_USER="${CWA_OWNER_USER:-abc}"
CWA_CHOWN="${CWA_CHOWN:-chown}"
CWA_PYTHON="${CWA_PYTHON:-python3}"

# The app-tree directories the runtime user writes to. These, not the whole
# tree, are the floor's app-tree contribution (#941). cps/cache is handled
# earlier in cwa-init, so it is intentionally absent here.
CWA_APP_WRITABLE_DIRS="${CWA_APP_WRITABLE_DIRS:-${CWA_CONFIG_ROOT}/metadata_change_logs ${CWA_CONFIG_ROOT}/metadata_temp}"

log() { echo "[cwa-init] $*"; }

# True when NETWORK_SHARE_MODE is set to any of the accepted truthy spellings.
# Lowercased via tr rather than ${v,,} so the script stays runnable under the
# bash 3.2 a macOS dev box ships; the container is on bash 5.
network_share_mode() {
  local v
  v="$(printf '%s' "${NETWORK_SHARE_MODE:-}" | tr '[:upper:]' '[:lower:]')"
  case "$v" in
    true|1|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

# Bind-mounted trees we must not touch when they live on a network share.
share_exempt() {
  case "$1" in
    "${CWA_CONFIG_ROOT}"|"${CWA_CONFIG_ROOT}"/*|/calibre-library|/calibre-library/*|/cwa-book-ingest|/cwa-book-ingest/*)
      return 0 ;;
    *) return 1 ;;
  esac
}

# Echo the absolute directories declared in dirs.json, one per line. A missing or
# unparseable file yields nothing; the caller keeps its own floor.
read_dirs_json() {
  [ -f "${CWA_DIRS_JSON}" ] || return 0
  "${CWA_PYTHON}" - "${CWA_DIRS_JSON}" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, str) and value.startswith("/"):
                print(value)
except Exception:
    pass
PY
}

# Strip trailing slashes so /config/ and /config compare equal.
normalise() {
  local p="$1"
  while [ "${#p}" -gt 1 ] && [ "${p: -1}" = "/" ]; do p="${p%/}"; done
  printf '%s' "$p"
}

# Reduce a list of paths to the minimal set that still covers all of them: drop
# exact duplicates, and drop any path already contained in another, since
# chown -R on /config already covers /config/.cwa_conversion_tmp.
dedupe_paths() {
  local -a in=("$@")
  local -a out=()
  local p q keep seen o

  for p in "${in[@]}"; do
    p="$(normalise "$p")"
    [ -n "$p" ] || continue
    keep=1
    for q in "${in[@]}"; do
      q="$(normalise "$q")"
      [ -n "$q" ] || continue
      [ "$p" = "$q" ] && continue
      # p is strictly inside q -> q's recursive walk already covers p
      case "$p" in "$q"/*) keep=0; break ;; esac
    done
    if [ "$keep" = "1" ]; then
      seen=0
      for o in ${out[@]+"${out[@]}"}; do [ "$o" = "$p" ] && seen=1 && break; done
      [ "$seen" = "0" ] && out+=("$p")
    fi
  done

  printf '%s\n' ${out[@]+"${out[@]}"}
}

main() {
  local -a candidates=("${CWA_CONFIG_ROOT}")
  local dir

  # The app tree is world-readable and never re-walked (#941); only the dirs the
  # runtime user writes under it are chowned. They ship in the image but a
  # missing one must not turn into a soft chown failure, so ensure they exist.
  for dir in ${CWA_APP_WRITABLE_DIRS}; do
    [ -n "$dir" ] || continue
    mkdir -p "$dir" 2>/dev/null || true
    candidates+=("$dir")
  done

  while IFS= read -r dir; do
    [ -n "$dir" ] && candidates+=("$dir")
  done < <(read_dirs_json)

  local -a requiredDirs=()
  while IFS= read -r dir; do
    [ -n "$dir" ] && requiredDirs+=("$dir")
  done < <(dedupe_paths "${candidates[@]}")

  local dirs
  dirs="$(printf ', %s' ${requiredDirs[@]+"${requiredDirs[@]}"})"
  dirs="${dirs:2}"
  log "Preparing to set ownership of everything in ${dirs} to ${CWA_OWNER_USER}:${CWA_OWNER_USER}..."

  for dir in ${requiredDirs[@]+"${requiredDirs[@]}"}; do
    if network_share_mode && share_exempt "$dir"; then
      log "NETWORK_SHARE_MODE=true detected; skipping chown of ${dir}"
      continue
    fi

    if "${CWA_CHOWN}" -R "${CWA_OWNER_USER}:${CWA_OWNER_USER}" "$dir"; then
      log "Successfully set permissions for '${dir}'!"
    else
      log "Service could not successfully set permissions for '${dir}' (see errors above)."
    fi
  done
}

main "$@"
