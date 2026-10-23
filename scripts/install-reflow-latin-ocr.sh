#!/bin/sh
# Install a pinned optical network; application conversions never download data.
# Upstream tessdata_best is Apache-2.0. Keep its license beside the derived data.
set -eu
output=${1:?usage: install-reflow-latin-ocr.sh TESSDATA_DIRECTORY}
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT HUP INT TERM
revision=e12c65a915945e4c28e237a9b52bc4a8f39a0cec
base=https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/$revision
curl --fail --location --retry 2 "$base/script/Latin.traineddata" -o "$scratch/Latin.traineddata"
curl --fail --location --retry 2 "$base/LICENSE" -o "$scratch/LICENSE"
hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi
}
checksum=$(hash_file "$scratch/Latin.traineddata")
test "${checksum%% *}" = cf7216998bf7f363f18e3def04cc6b28ae954d2a5a3bdad09f743c9d3538ef35
combine_tessdata -e "$scratch/Latin.traineddata" "$scratch/LatinRaw.lstm" \
  "$scratch/LatinRaw.lstm-unicharset" "$scratch/LatinRaw.lstm-recoder" "$scratch/LatinRaw.version"
combine_tessdata "$scratch/LatinRaw."
checksum=$(hash_file "$scratch/LatinRaw.traineddata")
test "${checksum%% *}" = 28acb38d9613595a7ae88a427bc7481c2ee488b960ea6baa31945f5ce0248b61
mkdir -p "$output"
install -m 644 "$scratch/LatinRaw.traineddata" "$output/LatinRaw.traineddata"
install -m 644 "$scratch/LICENSE" "$output/LatinRaw.LICENSE"
