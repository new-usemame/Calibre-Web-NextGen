# SPDX-License-Identifier: GPL-3.0-or-later
"""Run Calibre's CLI with its password read from the actual redirected pipe.

Calibre's <stdin> option calls getpass, which can prefer a controlling terminal
and uses the Windows console rather than redirected stdin. Keep its supported
argument boundary, but provide the password reader explicitly for this child.
"""
import sys


def read_password(*_args, **_kwargs):
    value = sys.stdin.readline()
    if not value:
        raise EOFError("No Calibre content-server password was supplied")
    return value.removesuffix("\n").removesuffix("\r")


def main(arguments=None):
    import getpass
    from calibre.db.cli.main import main as calibre_main
    getpass.getpass = read_password
    return calibre_main(["calibredb", *(sys.argv[1:] if arguments is None else arguments)])


if __name__ == "__main__":
    raise SystemExit(main())
