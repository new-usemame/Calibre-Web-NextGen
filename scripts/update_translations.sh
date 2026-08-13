#!/bin/bash
set -euo pipefail

# Resolve repo root (script_dir/..)
SCRIPT_DIR="$( cd -- "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$ROOT_DIR"

CONFIG="$ROOT_DIR/babel.cfg"
POT="$ROOT_DIR/messages.pot"
DUPLICATE_FIXER="$SCRIPT_DIR/fix_po_duplicates.py"

# Set up Python environment
PYTHON_CMD="python3"
PYBABEL_CMD=("$PYTHON_CMD" "-m" "babel.messages.frontend")

# Check if virtual environment exists and is usable
if [ -f "$ROOT_DIR/.venv/bin/python" ]; then
    if "$ROOT_DIR/.venv/bin/python" -c "import sys" >/dev/null 2>&1; then
        PYTHON_CMD="$ROOT_DIR/.venv/bin/python"
        PYBABEL_CMD=("$PYTHON_CMD" "-m" "babel.messages.frontend")
    else
        echo "[!] Warning: venv python is not usable; falling back to system python"
    fi
fi

# Get the latest version from GitHub releases
echo "[i] Fetching latest version from GitHub..."
VERSION=$(curl -s https://api.github.com/repos/crocodilestick/Calibre-Web-Automated/releases/latest | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/' || echo "unknown")
if [ "$VERSION" = "unknown" ] || [ -z "$VERSION" ]; then
    echo "[!] Warning: Could not fetch version from GitHub, using fallback"
    VERSION="dev"
fi

echo "[i] Using config: $CONFIG"
echo "[i] Generating POT: $POT"
echo "[i] Using Python: $PYTHON_CMD"
echo "[i] Project version: $VERSION"

# 0. Snapshot the committed POT before extract overwrites it, so step 1c can
# carry its POT-Creation-Date forward. See freeze_pot_creation_date.py for why
# that date is the thing that makes community translation PRs go CONFLICTING.
POT_PREV=""
if [ -f "$POT" ]; then
    POT_PREV="$(mktemp)"
    cp "$POT" "$POT_PREV"
    # shellcheck disable=SC2064  # expand POT_PREV now, not at trap time
    trap "rm -f '$POT_PREV'" EXIT
fi

# 1. Extract messages
#
# --add-location=file keeps the source FILENAME on each `#:` reference and drops
# the line NUMBER. The filename is what a translator uses ("where does this
# string appear?"); the line number is pure churn, and it is the largest
# remaining conflict generator in this repo — 85,347 line-numbered `#:` lines
# across the POT and 28 catalogs, every one of them a candidate conflict line.
#
# freeze_pot_creation_date.py (step 1c) already removed the header half of this
# problem, and measured on PR #938 that location churn merged cleanly there. It
# does — when only ONE side moved. The case that conflicts is when BOTH sides
# shift line numbers on the SAME `#:` line, which is exactly what a feature
# branch does: it adds strings to cps/spa_strings.py (shifting every later
# anchor) while main independently edits another instrumented file, so both
# rewrite `#: cps/cover_picker.py:138 cps/spa_strings.py:483`. Same line, two
# edits, unavoidable conflict — no unchanged line exists to split the hunk.
# Observed 2026-08-10 blocking fork PRs #1508 and #1515, both on nl/messages.po,
# with zero translated content in dispute.
#
# Safe because nothing reads source locations. The three catalog readers in this
# repo — cps/api/i18n.py (SPA catalog), cps/render_template.py (missing-string
# notice) and scripts/generate_translation_status.py (status table) — use only
# msgid/msgstr/flags/obsolete. Verified by control: extracting with and without
# this flag and msgmerging both into nl/messages.po produced byte-identical
# (msgid, msgstr) pairs. test_po_source_location_churn.py pins both halves.
#
# msgmerge takes locations from the POT, so this one flag fans out to all 28
# locales; there is deliberately no second flag on the msgmerge call (the POT
# stays the single control point, and `--add-location=TYPE` is not portable
# across the gettext builds this runs on).
"${PYBABEL_CMD[@]}" extract -F "$CONFIG" -o "$POT" \
    --add-location=file \
    --project="Calibre-Web Automated" \
    --version="$VERSION" \
    --msgid-bugs-address="https://github.com/crocodilestick/Calibre-Web-Automated" \
    --copyright-holder="Calibre-Web Automated Contributors" \
    . || { echo "pybabel extract failed"; exit 1; }

# 1b. Repair babel's mis-detected python-format flags before they fan out.
# babel reads the literal percent in "{pct}% read" as a format spec (`% r` =
# space-flag + `r` conversion) and flags the entry python-format on top of
# python-brace-format. That inverts `msgfmt --check`: a translation keeping the
# phantom spec passes, one correctly dropping it fatals — which is how a corrupt
# Russian string reached screen-reader users (#936). Must run BEFORE msgmerge,
# since msgmerge is what copies POT flags into every locale.
"$PYTHON_CMD" "$SCRIPT_DIR/fix_pot_format_flags.py" "$POT" \
    || { echo "fix_pot_format_flags failed"; exit 1; }

# 1c. Carry the previous POT-Creation-Date forward. babel restamps it from the
# wall clock on every run, msgmerge fans it into all 28 locales, and the bot
# commits it — ~99 .po commits/month for a field nothing reads. Because gettext
# emits POT-Creation-Date and PO-Revision-Date on ADJACENT lines, that bot bump
# collides with the PO-Revision-Date a translator's editor stamps on save: git
# cannot split adjacent changed lines into separate hunks, so the merge
# conflicts. That was the ONLY conflict on PR #938. Must run before the
# msgmerge loop below, which is what copies the header into every locale.
if [ -n "$POT_PREV" ]; then
    "$PYTHON_CMD" "$SCRIPT_DIR/freeze_pot_creation_date.py" "$POT" "$POT_PREV" \
        || { echo "freeze_pot_creation_date failed"; exit 1; }
fi

# 2. Merge updates
shopt -s nullglob
for po in "$ROOT_DIR"/cps/translations/*/LC_MESSAGES/messages.po; do
    echo "[i] Updating $po"
    
    # Try msgmerge, but capture any failures
    # Never let gettext guess a translation for a new msgid from a merely
    # similar old sentence. Those guesses are marked fuzzy, excluded by msgfmt
    # and the SPA catalog, and previously made translation status look fuller
    # while users still saw English (#879). New strings stay empty until a
    # locale's translation is reviewed.
    if ! msgmerge --no-fuzzy-matching --update "$po" "$POT" 2>/dev/null; then
        echo "[!] msgmerge failed for $po, checking for duplicates..."
        
        # Check if the error is related to duplicates
        msgmerge_output=$(msgmerge --no-fuzzy-matching --update "$po" "$POT" 2>&1 || true)
        if echo "$msgmerge_output" | grep -q "duplicate message definition"; then
            echo "[i] Duplicate messages detected in $po, attempting to fix..."
            
            if [ -f "$DUPLICATE_FIXER" ]; then
                $PYTHON_CMD "$DUPLICATE_FIXER" "$po" || {
                    echo "[!] Warning: Failed to fix duplicates in $po automatically"
                    echo "[!] Manual intervention may be required"
                    continue
                }
                
                # Try msgmerge again after fixing duplicates
                echo "[i] Retrying msgmerge for $po after duplicate fix..."
                if ! msgmerge --no-fuzzy-matching --update "$po" "$POT"; then
                    echo "[!] msgmerge still failed for $po even after duplicate fix"
                    continue
                fi
            else
                echo "[!] Warning: Duplicate fixer script not found at $DUPLICATE_FIXER"
                echo "[!] Please fix duplicates manually or ensure the script is available"
                continue
            fi
        else
            echo "[!] msgmerge failed for $po with non-duplicate errors:"
            echo "$msgmerge_output"
            continue
        fi
    fi
done

# Existing fuzzy SPA entries are never safe to compile, and newly introduced
# ones indicate that a manual edit bypassed the no-fuzzy merge policy. Keep the
# all-locale catalog invariant explicit instead of silently dropping them.
"$PYTHON_CMD" "$SCRIPT_DIR/check_spa_fuzzy.py"

# 3. Final validation and compile
for po in "$ROOT_DIR"/cps/translations/*/LC_MESSAGES/messages.po; do
    mo="${po%.po}.mo"
    echo "[i] Compiling $po -> $mo"
    
    # Final validation before compilation
    if ! msgfmt --check "$po" >/dev/null 2>&1; then
        echo "[!] ERROR: $po still has errors after duplicate fixing:"
        msgfmt --check "$po" 2>&1 || true
        echo "[!] Skipping compilation of $po"
        continue
    fi
    
    msgfmt "$po" -o "$mo" || { echo "msgfmt compilation failed for $po"; exit 1; }
done

echo "[✓] Translation update complete."
