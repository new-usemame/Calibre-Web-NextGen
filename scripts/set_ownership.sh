#!/bin/bash
# Set ownership of every directory CWA needs to write to, walking each once.
#
# Called by the cwa-init s6 unit at every container start.
#
# The list is a fixed floor (/config, plus the narrow set of app-tree dirs the
# runtime user writes) and the three paths resolved from environment,
# dirs.json, or defaults. dirs.json ships
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
# scripts/auto_library.py can rewrite dirs.json in place at runtime when the
# library environment override is unset, so a crash mid-write can leave it
# unparseable -- which must not be able to silently reduce this pass to nothing.
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
CWA_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CWA_APP_PATHS="${CWA_APP_PATHS:-${CWA_SCRIPT_DIR}/app_paths.py}"
CWA_UID="${CWA_UID:-$(id -u)}"
CWA_RESOLVED_DIRS=()

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
  local configured
  case "$1" in
    "${CWA_CONFIG_ROOT}"|"${CWA_CONFIG_ROOT}"/*)
      return 0 ;;
  esac
  for configured in ${CWA_RESOLVED_DIRS[@]+"${CWA_RESOLVED_DIRS[@]}"}; do
    configured="$(normalise "$configured")"
    case "$1" in
      "$configured"|"$configured"/*) return 0 ;;
    esac
  done
  return 1
}

# Echo the three resolved runtime directories, one per line. app_paths owns
# per-key environment -> dirs.json -> compiled-default precedence for every
# scripts/ and shell consumer.
read_configured_dirs() {
  CWA_APP_ROOT="${CWA_APP_ROOT}" CWA_DIRS_JSON="${CWA_DIRS_JSON}" \
    "${CWA_PYTHON}" "${CWA_APP_PATHS}" all
}

# Defence in depth for the CLI boundary. app_paths owns the validation
# contract; this check makes a missing, replaced, or broken resolver unable to
# hand root's recursive chown an empty/relative/traversing path.
valid_resolved_dir() {
  local p="$1"
  p="$(normalise "$p")"
  [ -n "$p" ] || return 1
  case "$p" in
    /*) ;;
    *) return 1 ;;
  esac
  case "$p" in
    "/"|*$'\n'*|*$'\r'*|*/../*|*/..) return 1 ;;
  esac
  return 0
}

# Lexically collapse separators and dot components without resolving symlinks.
# Bash owns this final trust boundary because CWA_CONFIG_ROOT and a replacement
# resolver can both supply values that did not pass through app_paths.py.
normalise() {
  local p="$1"
  local before
  local double_slash="//"
  local slash="/"
  local dot_component="/./"

  while :; do
    before="$p"
    p="${p//$double_slash/$slash}"
    p="${p//$dot_component/$slash}"
    if [ "$p" != "/" ] && [ "${#p}" -gt 1 ] && [ "${p: -2}" = "/." ]; then
      p="${p%/.}"
    fi
    [ "$p" = "$before" ] && break
  done
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
  local dir resolved_dirs resolved_count

  # Started as an arbitrary non-root user (`--user`, `--userns=keep-id`):
  # nothing below can succeed. LSIO's init-adduser is skipped on that path, so
  # `abc` keeps its build-time 911:1001, and changing a file's owner to a
  # different uid needs CAP_CHOWN. Every directory would report EPERM in turn.
  #
  # There is also nothing to repair: files under a bind mount already belong to
  # the uid we are running as. So say it once and skip the walk, rather than
  # emitting one failure line per directory that reads like a broken container.
  # See #947.
  if [ "${CWA_UID}" != "0" ]; then
    log "running as uid ${CWA_UID} (not root); skipping ownership pass — files keep their current owner"
    return 0
  fi

  if ! valid_resolved_dir "${CWA_CONFIG_ROOT}"; then
    log "ERROR: CWA_CONFIG_ROOT must be a non-empty absolute path without '..' components; refusing ownership pass"
    return 1
  fi

  if ! resolved_dirs="$(read_configured_dirs)"; then
    log "ERROR: runtime path resolver failed; refusing ownership pass"
    return 1
  fi
  if [ -z "${resolved_dirs}" ]; then
    log "ERROR: runtime path resolver returned no paths; refusing ownership pass"
    return 1
  fi

  resolved_count=0
  while IFS= read -r dir; do
    if ! valid_resolved_dir "$dir"; then
      log "ERROR: runtime path resolver returned unsafe path '${dir}'; refusing ownership pass"
      return 1
    fi
    CWA_RESOLVED_DIRS+=("$dir")
    candidates+=("$dir")
    resolved_count=$((resolved_count + 1))
  done <<< "${resolved_dirs}"
  if [ "${resolved_count}" -ne 3 ]; then
    log "ERROR: runtime path resolver returned ${resolved_count} paths instead of 3; refusing ownership pass"
    return 1
  fi

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
