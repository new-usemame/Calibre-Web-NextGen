#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Resolve a mutable/awaited registry tag once, then hand Docker an immutable
# digest reference.  There is deliberately no fallback tag: absence means the
# subject image is unknown, so the caller must fail without running a verdict.
set -euo pipefail

image="${1:?usage: resolve-e2e-image.sh IMAGE TAG [ATTEMPTS] [DELAY_SECONDS]}"
tag="${2:?usage: resolve-e2e-image.sh IMAGE TAG [ATTEMPTS] [DELAY_SECONDS]}"
attempts="${3:-240}"
delay_seconds="${4:-30}"

if ! [[ "$attempts" =~ ^[1-9][0-9]*$ && "$delay_seconds" =~ ^[0-9]+$ ]]; then
  echo "attempts must be positive and delay must be non-negative" >&2
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
  if (( attempt < attempts )); then
    echo "${tagged_ref} is not published yet (attempt ${attempt}/${attempts}); waiting ${delay_seconds}s" >&2
    sleep "$delay_seconds"
  fi
done

echo "ERROR: ${tagged_ref} never resolved to a manifest digest after ${attempts} attempts" >&2
echo "Refusing to run E2E because the triggering commit's image cannot be identified." >&2
exit 1
