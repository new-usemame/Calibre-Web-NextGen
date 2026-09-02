#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Resolve a mutable/awaited registry tag once, then hand Docker an immutable
# digest reference.  There is deliberately no fallback tag: absence means the
# subject image is unknown, so the caller must fail without running a verdict.
set -euo pipefail

usage="usage: resolve-e2e-image.sh IMAGE TAG [ATTEMPTS] [DELAY_SECONDS] [PRODUCER_REPOSITORY PRODUCER_SHA PRODUCER_EVENT API_URL]"
image="${1:?$usage}"
tag="${2:?$usage}"
attempts="${3:-240}"
delay_seconds="${4:-30}"
producer_repository="${5:-}"
producer_sha="${6:-}"
producer_event="${7:-}"
api_url="${8:-}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
producer_check="$script_dir/check-e2e-image-producer.py"

if ! [[ "$attempts" =~ ^[1-9][0-9]*$ && "$delay_seconds" =~ ^[0-9]+$ ]]; then
  echo "attempts must be positive and delay must be non-negative" >&2
  exit 2
fi
if [[ -n "$producer_repository" ]] && [[ -z "$producer_sha" || -z "$producer_event" || -z "$api_url" ]]; then
  echo "$usage" >&2
  exit 2
fi

tagged_ref="${image}:${tag}"
for ((attempt = 1; attempt <= attempts; attempt++)); do
  digest="$({ docker buildx imagetools inspect "$tagged_ref" \
    --format '{{json .Manifest.Digest}}' 2>/dev/null || true; } | tr -d '"[:space:]')"
  if [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "resolved ${tagged_ref} to ${digest} on attempt ${attempt}/${attempts}" >&2
    printf '%s@%s\n' "$image" "$digest"
    exit 0
  fi
  if [[ -n "$producer_repository" ]]; then
    if python3 "$producer_check" \
      "$producer_repository" "$producer_sha" "$producer_event" "$api_url"; then
      :
    else
      exit $?
    fi
  fi
  if (( attempt < attempts )); then
    echo "${tagged_ref} is not published yet (attempt ${attempt}/${attempts}); waiting ${delay_seconds}s" >&2
    sleep "$delay_seconds"
  fi
done

echo "ERROR: ${tagged_ref} never resolved to a manifest digest after ${attempts} attempts" >&2
if [[ -n "$producer_repository" ]]; then
  echo "The exact Build & Push producer remained live through the bounded wait; see its run URL above." >&2
fi
echo "Refusing to run E2E because the triggering commit's image cannot be identified." >&2
exit 1
