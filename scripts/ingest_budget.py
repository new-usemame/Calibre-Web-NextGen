# SPDX-License-Identifier: GPL-3.0-or-later
"""Select the ingest watchdog budget without loading the application or OCR."""
from pathlib import Path
import os
import stat
import subprocess
import sys

REFERENCE_PAGES = 500
AUTOMATIC_CEILING_SECONDS = 12 * 60 * 60
PDF_PROBE_TIMEOUT_SECONDS = 8
WORKER = Path(__file__).resolve()


def scaled_budget(base_seconds, pages):
    """Never shorten an owner's budget; zero remains explicitly unlimited."""
    if base_seconds <= 0 or pages is None or pages <= REFERENCE_PAGES:
        return base_seconds
    scaled = (base_seconds * pages + REFERENCE_PAGES - 1) // REFERENCE_PAGES
    return max(base_seconds, min(AUTOMATIC_CEILING_SECONDS, scaled))


def pdf_page_count(path):
    if Path(path).suffix.lower() != '.pdf':
        return None
    try:
        result = subprocess.run(
            [sys.executable, str(WORKER), '--pdf-pages', str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=PDF_PROBE_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        if not value.isascii() or not value.isdecimal() or len(value) > 10:
            return None
        pages = int(value)
        return pages if pages > 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _read_pdf_pages(path):
    # Install bounds before importing the document parser. A slow or hostile
    # page tree must not pin the service or exhaust its memory. The parent
    # additionally kills/reaps this child after eight wall-clock seconds.
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (5, 6))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from pypdf import PdfReader

    # Reject FIFOs/devices and avoid following a substituted ingest symlink.
    # Failure to count is safe: the normal configured budget still applies.
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('not a regular PDF')
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted:
            raise ValueError('encrypted PDF')
        return reader.get_num_pages()


def main():
    try:
        if len(sys.argv) == 3 and sys.argv[1] == '--pdf-pages':
            print(_read_pdf_pages(sys.argv[2]))
        elif len(sys.argv) == 3:
            base = int(sys.argv[1])
            if base < 0:
                raise ValueError('negative budget')
            pages = pdf_page_count(sys.argv[2]) if base else None
            print(scaled_budget(base, pages))
        else:
            raise ValueError('expected budget and path')
    except Exception:
        # The service keeps its original budget when the probe cannot run.
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
