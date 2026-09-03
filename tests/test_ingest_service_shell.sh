#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

mkdir -p "$tmpdir/watch" "$tmpdir/processing" "$tmpdir/recent"
processor_log="$tmpdir/processor.log"
import_log="$tmpdir/import.log"
post_batch_log="$tmpdir/post-batch.log"
touch "$processor_log"
touch "$import_log"
touch "$post_batch_log"

stub="$tmpdir/processor-stub.sh"
cat > "$stub" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$1" >> "$PROCESSOR_LOG"
if [ "${PROCESSOR_EXIT_CODE:-0}" = "0" ]; then
        digest=$(python3 - "$1" <<'PY'
import hashlib
import sys
with open(sys.argv[1], "rb") as stream:
    print(hashlib.sha256(stream.read()).hexdigest())
PY
)
        marker="$PROCESSOR_CONTENT_DB/$digest"
        if [ ! -e "$marker" ]; then
                printf '%s\n' "$1" >> "$IMPORT_LOG"
                : > "$marker"
        fi
fi
if [ "${PROCESSOR_CRASH_AFTER_RECORD:-0}" = "1" ]; then
        # Model the processor after its Calibre transaction committed both the
        # format and content marker, before source deletion/queue rewrite.
        kill -KILL "$CRASH_TARGET_PID"
        exit 137
fi
if [ "${PROCESSOR_EXIT_CODE:-0}" = "0" ]; then
        rm -f -- "$1"
fi
exit "${PROCESSOR_EXIT_CODE:-0}"
STUB
chmod +x "$stub"

post_batch_stub="$tmpdir/post-batch-stub.sh"
cat > "$post_batch_stub" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf 'post-batch\n' >> "$POST_BATCH_LOG"
STUB
chmod +x "$post_batch_stub"

export WATCH_FOLDER="$tmpdir/watch"
export CWA_INGEST_SERVICE_TEST_MODE=1
export CWA_INGEST_PROCESSING_DIR="$tmpdir/processing"
export CWA_INGEST_RECENT_DIR="$tmpdir/recent"
export CWA_INGEST_RETRY_QUEUE="$tmpdir/retry_queue"
export CWA_INGEST_STATUS_FILE="$tmpdir/status"
export CWA_INGEST_RECENT_EVENT_TTL=2
export CWA_INGEST_BATCH_DIRTY_FILE="$tmpdir/batch_dirty"
export CWA_INGEST_BATCH_LAST_SUCCESS_FILE="$tmpdir/batch_last_success"
export CWA_INGEST_BATCH_QUIET_SECONDS=1
export CWA_INGEST_POST_BATCH_CMD="$post_batch_stub"
export CWA_INGEST_PROCESSOR_CMD="$stub"
export PROCESSOR_LOG="$processor_log"
export IMPORT_LOG="$import_log"
export PROCESSOR_CONTENT_DB="$tmpdir/content-markers"
export POST_BATCH_LOG="$post_batch_log"
export PROCESSOR_EXIT_CODE=0
mkdir -p "$PROCESSOR_CONTENT_DB"
unset CALIBRE_CONFIG_DIRECTORY

# shellcheck disable=SC1091
source "$REPO_ROOT/root/etc/s6-overlay/s6-rc.d/cwa-ingest-service/run" >/dev/null

if [ "$CALIBRE_CONFIG_DIRECTORY" != "/config/.config/calibre-runtime" ]; then
        printf 'Expected ingest service to export the abc-safe Calibre config; got: %s\n' \
                "${CALIBRE_CONFIG_DIRECTORY:-unset}" >&2
        exit 1
fi

assert_contains() {
        local haystack="$1"
        local needle="$2"
        if [[ "$haystack" != *"$needle"* ]]; then
                printf 'Expected output to contain: %s\nActual output:\n%s\n' "$needle" "$haystack" >&2
                exit 1
        fi
}

assert_processor_invocations() {
        local expected="$1"
        local actual
        actual=$(wc -l < "$processor_log" | tr -d ' ')
        if [ "$actual" != "$expected" ]; then
                printf 'Expected %s processor invocations, saw %s\n' "$expected" "$actual" >&2
                printf 'Processor log:\n' >&2
                cat "$processor_log" >&2
                exit 1
        fi
}

assert_import_count() {
        local expected="$1"
        local actual
        actual=$(wc -l < "$import_log" | tr -d ' ')
        if [ "$actual" != "$expected" ]; then
                printf 'Expected %s actual imports, saw %s\n' "$expected" "$actual" >&2
                cat "$import_log" >&2
                exit 1
        fi
}

assert_marker_count() {
        local dir="$1"
        local expected="$2"
        local actual
        actual=$(find "$dir" -type f | wc -l | tr -d ' ')
        if [ "$actual" != "$expected" ]; then
                printf 'Expected %s marker(s) in %s, saw %s\n' "$expected" "$dir" "$actual" >&2
                find "$dir" -type f -print >&2
                exit 1
        fi
}

missing_path="$tmpdir/watch/missing.epub"
output=$(handle_event "$missing_path" 2>&1)
assert_contains "$output" "Skipping stale event for missing file"
assert_processor_invocations 0
assert_marker_count "$CWA_INGEST_RECENT_DIR" 1

output=$(handle_event "$missing_path" 2>&1)
assert_contains "$output" "Skipping duplicate recent event"
assert_processor_invocations 0
assert_marker_count "$CWA_INGEST_RECENT_DIR" 1

sleep 3
printf 'replacement\n' > "$missing_path"
output=$(handle_event "$missing_path" 2>&1)
assert_contains "$output" "Starting Ingest Processor"
assert_processor_invocations 1
assert_marker_count "$CWA_INGEST_PROCESSING_DIR" 0

odd_path="$tmpdir/watch/odd name [1] ; test.epub"
printf 'odd\n' > "$odd_path"
output=$(handle_event "$odd_path" 2>&1)
assert_contains "$output" "Starting Ingest Processor"
assert_processor_invocations 2
assert_marker_count "$CWA_INGEST_PROCESSING_DIR" 0

if ! grep -Fxq "$odd_path" "$processor_log"; then
        printf 'Expected odd path to be passed safely to processor stub\n' >&2
        cat "$processor_log" >&2
        exit 1
fi

output=$(handle_event "$odd_path" 2>&1)
assert_contains "$output" "Skipping duplicate recent event"
assert_processor_invocations 2

printf 'odd changed content\n' > "$odd_path"
output=$(handle_event "$odd_path" 2>&1)
assert_contains "$output" "Starting Ingest Processor"
assert_processor_invocations 3
assert_marker_count "$CWA_INGEST_PROCESSING_DIR" 0

export PROCESSOR_EXIT_CODE=2
busy_path="$tmpdir/watch/busy.epub"
printf 'busy\n' > "$busy_path"
chmod 0755 "$tmpdir"
if id abc >/dev/null 2>&1; then
        chown abc:abc "$CWA_INGEST_RETRY_QUEUE"
fi
chmod 0640 "$CWA_INGEST_RETRY_QUEUE"
queue_attrs_before=$(python3 - "$CWA_INGEST_RETRY_QUEUE" <<'PY'
import os, stat, sys
value = os.stat(sys.argv[1], follow_symlinks=False)
print(value.st_uid, value.st_gid, stat.S_IMODE(value.st_mode))
PY
)
handle_event "$busy_path" >/dev/null 2>&1 || true
assert_processor_invocations 4
assert_marker_count "$CWA_INGEST_PROCESSING_DIR" 0
if ! grep -Fq "$busy_path" "$CWA_INGEST_RETRY_QUEUE"; then
        printf 'Expected busy path to remain in retry queue\n' >&2
        cat "$CWA_INGEST_RETRY_QUEUE" >&2
        exit 1
fi
queue_attrs_after=$(python3 - "$CWA_INGEST_RETRY_QUEUE" <<'PY'
import os, stat, sys
value = os.stat(sys.argv[1], follow_symlinks=False)
print(value.st_uid, value.st_gid, stat.S_IMODE(value.st_mode))
PY
)
if [ "$queue_attrs_before" != "$queue_attrs_after" ]; then
        printf 'Queue owner/mode changed across atomic rewrite: %s -> %s\n' \
                "$queue_attrs_before" "$queue_attrs_after" >&2
        exit 1
fi
if command -v cwa-as-abc >/dev/null 2>&1; then
        queue_size=$(cwa-as-abc env PYTHONPATH="$REPO_ROOT" \
                CWA_INGEST_RETRY_QUEUE="$CWA_INGEST_RETRY_QUEUE" python3 -c \
                'from cps.cwa_functions import get_ingest_queue_size; print(get_ingest_queue_size())' | tail -n 1)
else
        queue_size=$(PYTHONPATH="$REPO_ROOT" python3 -c \
                'from cps.cwa_functions import get_ingest_queue_size; print(get_ingest_queue_size())' | tail -n 1)
fi
if [ "$queue_size" != "1" ]; then
        printf 'Expected web queue-size reader to see one row, saw %s\n' "$queue_size" >&2
        exit 1
fi

abandoned_temp="${CWA_INGEST_RETRY_QUEUE}.tmp.abandoned"
: > "$abandoned_temp"
cleanup_queue_transaction_temps
if [ -e "$abandoned_temp" ]; then
        printf 'Expected abandoned queue transaction to be swept\n' >&2
        exit 1
fi

rm -f "$busy_path"
process_retry_queue >/dev/null 2>&1
if [ -s "$CWA_INGEST_RETRY_QUEUE" ]; then
        printf 'Expected vanished retry path to be dropped from queue\n' >&2
        cat "$CWA_INGEST_RETRY_QUEUE" >&2
        exit 1
fi
queue_attrs_after_drain=$(python3 - "$CWA_INGEST_RETRY_QUEUE" <<'PY'
import os, stat, sys
value = os.stat(sys.argv[1], follow_symlinks=False)
print(value.st_uid, value.st_gid, stat.S_IMODE(value.st_mode))
PY
)
if [ "$queue_attrs_before" != "$queue_attrs_after_drain" ]; then
        printf 'Queue owner/mode changed across drain rewrite: %s -> %s\n' \
                "$queue_attrs_before" "$queue_attrs_after_drain" >&2
        exit 1
fi

# A directory-fsync error is detected only after os.replace has installed the
# complete new queue. The failure must report that state accurately instead of
# promising that the original queue survived.
post_replace_path="$tmpdir/watch/post-replace.epub"
printf 'post replace\n' > "$post_replace_path"
printf 'prior row\n' > "$CWA_INGEST_RETRY_QUEUE"
if post_replace_output=$(queue_retry_file "$post_replace_path" after-replace 2>&1); then
        printf 'Expected injected post-replace directory-fsync failure\n' >&2
        exit 1
else
        post_replace_status=$?
fi
if [ "$post_replace_status" -eq 0 ]; then
        printf 'Expected post-replace failure to return nonzero\n' >&2
        exit 1
fi
assert_contains "$post_replace_output" "replacement installed"
assert_contains "$post_replace_output" "crash durability is uncertain"
if [[ "$post_replace_output" == *"original preserved"* ]]; then
        printf 'Post-replace failure falsely claimed that the original survived\n' >&2
        exit 1
fi
if ! grep -Fq "$post_replace_path" "$CWA_INGEST_RETRY_QUEUE"; then
        printf 'Expected the complete replacement queue to be installed before fsync failure\n' >&2
        cat "$CWA_INGEST_RETRY_QUEUE" >&2
        exit 1
fi
: > "$CWA_INGEST_RETRY_QUEUE"
rm -f "$post_replace_path"

# F-1fdb7c: once Calibre has committed the database row and content marker, a
# crash before source deletion/queue acknowledgement may invoke the processor
# again, but must not execute a second import. Format-file writes are separate
# filesystem effects and are not covered by that database transaction.
crash_path="$tmpdir/watch/crash-after-add.epub"
printf 'whole book\n' > "$crash_path"
printf '%s\n' "$crash_path" > "$CWA_INGEST_RETRY_QUEUE"
export PROCESSOR_EXIT_CODE=0
export PROCESSOR_CRASH_AFTER_RECORD=1
(
        export CRASH_TARGET_PID="$BASHPID"
        process_retry_queue >/dev/null 2>&1
) &
crash_drain_pid=$!
wait "$crash_drain_pid" 2>/dev/null || true
unset PROCESSOR_CRASH_AFTER_RECORD CRASH_TARGET_PID
# A real container restart gives the service a fresh /tmp, including the
# processing-marker directory. Reproduce that boundary before the next drain.
find "$CWA_INGEST_PROCESSING_DIR" -type f -name '*.processing' -delete

if [ ! -s "$CWA_INGEST_RETRY_QUEUE" ]; then
        printf 'Crash model must leave the original queue entry unacknowledged\n' >&2
        exit 1
fi
assert_processor_invocations 5
assert_import_count 4
process_retry_queue >/dev/null 2>&1
assert_processor_invocations 6
assert_import_count 4
if [ -s "$CWA_INGEST_RETRY_QUEUE" ]; then
        printf 'Expected completed crash-recovery entry to be acknowledged\n' >&2
        cat "$CWA_INGEST_RETRY_QUEUE" >&2
        exit 1
fi
if [ -e "$crash_path" ]; then
        printf 'Expected completed crash-recovery source to be removed without re-import\n' >&2
        exit 1
fi

# F-1fdb7c: an unclassified processor failure is transient. It must remain in
# the queue; only success or an explicitly terminal disposition may ack it.
transient_path="$tmpdir/watch/transient.epub"
printf 'retry me\n' > "$transient_path"
printf '%s\n' "$transient_path" > "$CWA_INGEST_RETRY_QUEUE"
export PROCESSOR_EXIT_CODE=7
process_retry_queue >/dev/null 2>&1 || true
assert_processor_invocations 7
if ! grep -Fq "$transient_path" "$CWA_INGEST_RETRY_QUEUE"; then
        printf 'Expected transient processor failure to remain queued\n' >&2
        exit 1
fi
rm -f "$transient_path"
: > "$CWA_INGEST_RETRY_QUEUE"

# Exit 3 is the processor's explicit fail-closed disposition: quarantine
# itself failed, so the watched source stays put and is not hidden in a retry
# row that this terminal drain would immediately discard.
terminal_path="$tmpdir/watch/quarantine-failed.epub"
printf 'must remain\n' > "$terminal_path"
export PROCESSOR_EXIT_CODE=3
handle_event "$terminal_path" >/dev/null 2>&1 || true
assert_processor_invocations 8
if [ ! -e "$terminal_path" ]; then
        printf 'Expected terminal quarantine failure to retain its source\n' >&2
        exit 1
fi
if grep -Fq "$terminal_path" "$CWA_INGEST_RETRY_QUEUE"; then
        printf 'Expected terminal quarantine failure not to enter retry queue\n' >&2
        exit 1
fi

rm -f "$missing_path" "$odd_path" "$busy_path" "$terminal_path"
printf 'dirty\n' > "$CWA_INGEST_BATCH_DIRTY_FILE"
touch "$CWA_INGEST_BATCH_LAST_SUCCESS_FILE"
sleep 2
output=$(maybe_run_post_batch_follow_up 2>&1)
assert_contains "$output" "Post-batch follow-up triggered"
assert_contains "$output" "Post-batch follow-up completed"
if [ -e "$CWA_INGEST_BATCH_DIRTY_FILE" ]; then
        printf 'Expected dirty marker to be cleared after successful post-batch follow-up\n' >&2
        exit 1
fi
if [ "$(wc -l < "$post_batch_log" | tr -d ' ')" != "1" ]; then
        printf 'Expected exactly one post-batch invocation\n' >&2
        cat "$post_batch_log" >&2
        exit 1
fi
maybe_run_post_batch_follow_up >/dev/null 2>&1
if [ "$(wc -l < "$post_batch_log" | tr -d ' ')" != "1" ]; then
        printf 'Expected clean state not to retrigger post-batch follow-up\n' >&2
        cat "$post_batch_log" >&2
        exit 1
fi

sidecar_path="$tmpdir/watch/metadata.cwa.json"
output=$(handle_event "$sidecar_path" 2>&1)
assert_contains "$output" "not a standalone ingest candidate"
assert_processor_invocations 8

# Fork #1740: the live event path must accept Calibre formats regardless of
# extension case without changing Bash matching rules for the rest of the
# long-running watcher.
export PROCESSOR_EXIT_CODE=0
uppercase_path="$tmpdir/watch/Book.EPUB"
printf 'uppercase extension\n' > "$uppercase_path"
output=$(handle_event "$uppercase_path" 2>&1)
assert_contains "$output" "Starting Ingest Processor"
assert_processor_invocations 9
if ! grep -Fxq "$uppercase_path" "$processor_log"; then
        printf 'Expected uppercase-extension path to reach processor stub\n' >&2
        cat "$processor_log" >&2
        exit 1
fi

# F-8ebeb7: exercise the real production 6/0.5s sampling relationship. A
# writer that pauses for more than the old single 0.5s comparison and then
# resumes is stalled, not complete, and the startup sweep must leave it for
# the watcher's eventual CLOSE_WRITE.
stalled_path="$tmpdir/watch/00-stalled-copy.epub"
printf 'partial\n' > "$stalled_path"
(
        sleep 1.1
        printf 'resumed\n' >> "$stalled_path"
        sleep 4
) &
stalled_writer_pid=$!
startup_ingest_sweep >/dev/null 2>&1 || true
kill "$stalled_writer_pid" 2>/dev/null || true
wait "$stalled_writer_pid" 2>/dev/null || true
if grep -Fxq "$stalled_path" "$processor_log"; then
        printf 'Startup sweep treated a stalled, partially written file as settled\n' >&2
        exit 1
fi
