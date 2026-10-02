#!/usr/bin/env bash
# The ImageMagick policy is image-level state; only container root can replace it.
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: configure_image_policy.sh <policy-file> <default-policy>" >&2
  exit 2
fi

policy_file="$1"
default_policy="$2"

if [ "$(id -u)" != "0" ]; then
  echo "[cwa-init] running as uid $(id -u) (not root); keeping the image ImageMagick policy"
  exit 0
fi

rm -rf "$policy_file"
ln -s "$default_policy" "$policy_file"
