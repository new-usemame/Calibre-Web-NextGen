#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Give an image-neutral commit an immutable tag only when :dev demonstrably
# names the newest image-relevant ancestor.  A missing/failed intervening build
# must fail here; copying a stale floating tag would manufacture a verdict for
# backend bytes that the subject commit does not contain.
set -euo pipefail

image="${1:?usage: alias-e2e-image.sh IMAGE SOURCE_SHA SUBJECT_SHA}"
source_sha="${2:?usage: alias-e2e-image.sh IMAGE SOURCE_SHA SUBJECT_SHA}"
subject_sha="${3:?usage: alias-e2e-image.sh IMAGE SOURCE_SHA SUBJECT_SHA}"
repo_root="${GITHUB_WORKSPACE:-$(git rev-parse --show-toplevel)}"
classifier="${CLASSIFIER_PATH:-$repo_root/scripts/ci_path_classification.py}"

if ! [[ "$source_sha" =~ ^[0-9a-f]{40,64}$ && "$subject_sha" =~ ^[0-9a-f]{40,64}$ ]]; then
  echo "ERROR: image source and subject must be full commit IDs" >&2
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

source_digest="$(manifest_digest "$image:sha-$source_sha" || true)"
dev_digest="$(manifest_digest "$image:dev" || true)"
if ! [[ "$source_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "ERROR: immutable image $image:sha-$source_sha is missing or malformed" >&2
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
