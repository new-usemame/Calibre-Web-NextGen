#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Does a Kobo re-download a book when the server sends a ChangedEntitlement?

Nobody knows. That one unmeasured firmware behaviour is currently deciding two
separate things in this repository, in opposite directions:

* ``cps/admin.py``'s resend helper and its regression test treat "a
  ChangedEntitlement does not re-deliver the file" as a PROBLEM to work around,
  and the KEPUB repair task inherits it — if true, the repair never reaches any
  device that already has the book, which is the only population it exists for
  (finding ``F-3e383a``).
* ``notes/3649-kobo-format-change-entitlement-design.md`` treats the same claim
  as the DESIRABLE behaviour that makes metadata-only edits cheap, and rejected
  an always-NewEntitlement patch on those grounds. Its own verify plan lists
  measuring it as step 3.

Both are assumptions. This script is the measurement, written now so that the
moment the device is reachable it is one command rather than an afternoon of
rediscovering the access recipe.

WHAT IT DOES
------------
1. refuses to run unless the chosen book has ZERO annotations (arm A) — the
   whole point is to learn the delivery behaviour without risking a highlight;
2. records the on-device file's size, mtime and hash BEFORE;
3. bumps ONLY ``Books.last_modified`` server-side — deliberately NOT deleting
   the ``kobo_synced_books`` row, because deleting the user's last row resets
   the whole sync token and would answer a different question;
4. waits for you to sync the device;
5. records the file AFTER, and says which way the answer went.

WHAT IT NEVER DOES
------------------
* never writes to the device — every device command is a read;
* never touches a book that has annotations;
* never deletes a ``kobo_synced_books`` row;
* nothing at all without ``--go`` EXCEPT one read-only, ``mode=ro`` count of
  the book's annotations, which has to happen first so a dry run can tell you
  whether the real run would refuse.

DEVICE ACCESS, the recipe this fleet already paid for
-----------------------------------------------------
* plain ``ssh host 'cmd'`` connects, authenticates and returns NOTHING;
* ``-tt`` fixes that but opens an interactive shell, so commands go on STDIN;
* the device has no ``sqlite3`` and no ``scp``/``sftp-server``, so anything
  larger than a line comes back as ``gzip -c | base64`` between markers.

Only the first two matter here — this script reads three small values.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys

SSHPASS = "/opt/homebrew/bin/sshpass"
DEVICE_READ = (
    "ls -l {path} 2>/dev/null; "
    "md5sum {path} 2>/dev/null || echo 'no-md5sum'; "
    "exit\n"
)


def _credential():
    """Read the Kobo root password from the environment `secret exec` provides."""
    raw = os.environ.get("SECRET", "")
    try:
        parsed = json.loads(raw)
        return (parsed.get("username") or parsed.get("user") or "root",
                parsed.get("password") or parsed.get("secret") or parsed.get("value") or "")
    except Exception:
        return "root", raw.strip()


def read_device_file(host, path, *, dry_run):
    """Size, mtime and hash of one file on the device. Read-only."""
    user, password = _credential()
    # shlex.quote, because this string is interpreted by a SHELL on the device.
    # `--device-path` comes from the command line, so an unquoted path containing
    # `;` or a backtick would run on the Kobo — and this script's whole promise is
    # that every device command is a read. Quoting is what makes that true rather
    # than merely intended.
    command = DEVICE_READ.format(path=shlex.quote(path))
    if dry_run:
        print(f"    [dry run] would ssh -tt {user}@{host} and send: {command.strip()!r}")
        return None
    if not password:
        raise SystemExit("no credential in $SECRET — run this under `secret exec`")

    # `sshpass -e` reads the password from SSHPASS in the child environment, NOT
    # from argv. That is not cosmetic: subprocess.TimeoutExpired embeds the whole
    # command list in its message, so with `-p` a timeout prints the device's root
    # password in cleartext — into terminal scrollback, into any log, and into the
    # agent transcript this is designed to be run from. The standing rule is that a
    # secret value never reaches a transcript, and `-p` breaks it on the script's
    # own expected failure path (a Kobo that suspends mid-read hangs to the wall
    # clock, because ConnectTimeout only covers TCP connect).
    child_env = dict(os.environ)
    child_env["SSHPASS"] = password

    # The host key is PINNED to a file next to this script rather than discarded.
    # `--host` moves with DHCP, so the address is not proof of identity: if the
    # lease has moved, `accept-new` against a persistent file still refuses a
    # CHANGED key, which is exactly the case that would otherwise hand a root
    # password to whatever now answers on that address. (That is not
    # hypothetical — a LAN sweep during development found a different dropbear
    # host answering on :22.)
    known_hosts = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               ".kobo_known_hosts")
    try:
        result = subprocess.run(
            [SSHPASS, "-e", "ssh", "-tt",
             "-o", "StrictHostKeyChecking=accept-new",
             "-o", f"UserKnownHostsFile={known_hosts}",
             "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR", f"{user}@{host}"],
            input=command, capture_output=True, text=True, timeout=60,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        # `from None` matters: chaining would re-print the original message, and
        # the whole point is that no exception carries the command line.
        raise SystemExit(
            f"device read timed out after 60s against {host}"
        ) from None
    return ((result.stdout or "") + (result.stderr or "")).strip()


def annotations_for_book(app_db, book_id):
    """How many annotations exist for this book, across ALL users."""
    import sqlite3

    conn = sqlite3.connect(f"file:{app_db}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM annotation WHERE book_id = ?", (book_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def bump_last_modified(metadata_db, book_id, *, dry_run):
    """The exact thing the KEPUB repair does, and nothing else.

    NOT deleting the kobo_synced_books row is the point: that deletion empties
    the user's tracking table, which resets the whole sync token to datetime.min
    and turns every book into a NewEntitlement. Doing it here would answer a
    question nobody asked.
    """
    statement = "UPDATE books SET last_modified = datetime('now') WHERE id = ?"
    if dry_run:
        print(f"    [dry run] would run against {metadata_db}: "
              f"{statement} with id={book_id}")
        return
    import sqlite3

    conn = sqlite3.connect(metadata_db)
    try:
        # Bound, not interpolated. It is only safe today because argparse coerces
        # --book-id with type=int, and that safety net is one edit away from
        # failing silently.
        conn.execute(statement, (book_id,))
        conn.commit()
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="10.0.20.250",
                        help="the Kobo's current address (DHCP moves it; sweep for dropbear on :22)")
    parser.add_argument("--book-id", type=int, required=True,
                        help="a DISPOSABLE book already synced to the device")
    parser.add_argument("--device-path", required=True,
                        help="the book's path on the device, e.g. /mnt/onboard/Author/Title.kepub.epub")
    parser.add_argument("--app-db", required=True, help="CWNG app.db (annotation store)")
    parser.add_argument("--metadata-db", required=True, help="Calibre metadata.db")
    parser.add_argument("--go", action="store_true",
                        help="actually do it; without this nothing is read or written")
    args = parser.parse_args(argv)

    dry_run = not args.go
    print("Kobo re-delivery measurement" + ("  [DRY RUN]" if dry_run else ""))
    print(f"  book {args.book_id}, device {args.host}:{args.device_path}\n")

    count = annotations_for_book(args.app_db, args.book_id)
    print(f"1. annotations on this book: {count}")
    if count:
        raise SystemExit(
            f"REFUSING: book {args.book_id} has {count} annotation(s). This measurement "
            "must use a disposable, annotation-free book — if the answer turns out to be "
            "'yes it re-delivers', a re-spined package could strand exactly these."
        )

    print("2. reading the file on the device BEFORE")
    before = read_device_file(args.host, args.device_path, dry_run=dry_run)
    if before is not None:
        print("   " + before.replace("\n", "\n   "))

    print("3. bumping ONLY Books.last_modified (no kobo_synced_books deletion)")
    bump_last_modified(args.metadata_db, args.book_id, dry_run=dry_run)

    print("\n4. SYNC THE DEVICE NOW, then press Enter.")
    if not dry_run:
        input()

    print("5. reading the file on the device AFTER")
    after = read_device_file(args.host, args.device_path, dry_run=dry_run)
    if after is not None:
        print("   " + after.replace("\n", "\n   "))

    if dry_run:
        print("\n[DRY RUN] nothing was read or written. Re-run with --go.")
        return 0

    print("\nRESULT")
    if before == after:
        print("  UNCHANGED -> a ChangedEntitlement does NOT re-deliver the file.")
        print("  F-3e383a's original claim holds: the KEPUB repair never reaches a")
        print("  device that already has the book, and needs another delivery route.")
        print("  notes/3649's rejection of the always-NewEntitlement patch keeps its")
        print("  main argument.")
    else:
        print("  CHANGED -> a ChangedEntitlement DOES re-deliver the file.")
        print("  F-3e383a's worst case is refuted, and notes/3649 loses the argument")
        print("  it used to reject the always-NewEntitlement patch — metadata edits")
        print("  would be causing redundant downloads today.")
    print("\nRun the metadata-only arm from notes/3649's verify plan in the same")
    print("sitting: it is the same sync, read twice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
