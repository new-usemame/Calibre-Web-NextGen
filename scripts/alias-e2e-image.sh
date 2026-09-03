#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Give an image-neutral commit an immutable tag only when :dev demonstrably
# names the newest image-relevant ancestor.  A missing/failed intervening build
# must fail here; copying a stale floating tag would manufacture a verdict for
# backend bytes that the subject commit does not contain.
set -euo pipefail

usage="usage: alias-e2e-image.sh IMAGE SOURCE_SHA SUBJECT_SHA [ATTEMPTS] [DELAY_SECONDS] [PRODUCER_REPOSITORY PRODUCER_EVENT API_URL [RECOVERY_OUTPUT]]"
image="${1:?$usage}"
source_sha="${2:?$usage}"
subject_sha="${3:?$usage}"
attempts="${4:-1}"
delay_seconds="${5:-30}"
producer_repository="${6:-}"
producer_event="${7:-}"
api_url="${8:-}"
recovery_output="${9:-}"
repo_root="${GITHUB_WORKSPACE:-$(git rev-parse --show-toplevel)}"
classifier="${CLASSIFIER_PATH:-$repo_root/scripts/ci_path_classification.py}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
producer_check="$script_dir/check-e2e-image-producer.py"

if ! [[ "$source_sha" =~ ^[0-9a-f]{40,64}$ && "$subject_sha" =~ ^[0-9a-f]{40,64}$ ]]; then
  echo "ERROR: image source and subject must be full commit IDs" >&2
  exit 2
fi
if ! [[ "$attempts" =~ ^[1-9][0-9]*$ && "$delay_seconds" =~ ^[0-9]+$ ]]; then
  echo "attempts must be positive and delay must be non-negative" >&2
  exit 2
fi
if [[ -n "$producer_repository" ]] && [[ -z "$producer_event" || -z "$api_url" ]]; then
  echo "$usage" >&2
  exit 2
fi
if [[ -n "$recovery_output" && -z "$producer_repository" ]]; then
  echo "$usage" >&2
  exit 2
fi

expected_source="$(python3 "$classifier" \
  --repo-root "$repo_root" --latest-image-commit "$subject_sha")"
if [[ "$source_sha" != "$expected_source" ]]; then
  echo "ERROR: refusing image alias: $source_sha is not the newest image-relevant ancestor of $subject_sha" >&2
  echo "Expected source: $expected_source" >&2
  exit 1
fi
if ! git -C "$repo_root" merge-base --is-ancestor "$source_sha" "$subject_sha"; then
  echo "ERROR: refusing image alias: source $source_sha is not an ancestor of $subject_sha" >&2
  exit 1
fi

manifest_digest() {
  docker buildx imagetools inspect "$1" \
    --format '{{json .Manifest.Digest}}' 2>/dev/null \
    | tr -d '"[:space:]'
}

source_digest=""
for ((attempt = 1; attempt <= attempts; attempt++)); do
  source_digest="$(manifest_digest "$image:sha-$source_sha" || true)"
  if [[ "$source_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    break
  fi
  if [[ -n "$producer_repository" ]]; then
    if python3 "$producer_check" --terminal-exit-code 75 \
      "$producer_repository" "$source_sha" "$producer_event" "$api_url"; then
      :
    else
      producer_status=$?
      if [[ "$producer_status" == 75 && -n "$recovery_output" ]]; then
        printf 'recovery_build=true\n' >> "$recovery_output"
        echo "Source image is terminally unavailable; building exact subject $subject_sha instead." >&2
        exit 0
      fi
      # Preserve the aliaser's ordinary one-bit failure contract outside the
      # workflow recovery mode. Unknown API state must never authorize a copy
      # or be mistaken for a known terminal producer.
      exit 1
    fi
  fi
  if (( attempt < attempts )); then
    echo "$image:sha-$source_sha is not published yet (attempt ${attempt}/${attempts}); waiting ${delay_seconds}s" >&2
    sleep "$delay_seconds"
  fi
done

dev_digest="$(manifest_digest "$image:dev" || true)"
if ! [[ "$source_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "ERROR: immutable image $image:sha-$source_sha is missing or malformed" >&2
  if [[ -n "$producer_repository" ]]; then
    echo "The exact source producer remained live through the bounded wait; see its run URL above." >&2
  fi
  exit 1
fi
if [[ "$dev_digest" != "$source_digest" ]]; then
  echo "ERROR: refusing image alias: :dev does not name source commit $source_sha" >&2
  echo "The newest image-relevant build is missing, failed, or has not advanced :dev." >&2
  exit 1
fi

docker buildx imagetools create \
  -t "$image:sha-$subject_sha" \
  "$image@$source_digest"
