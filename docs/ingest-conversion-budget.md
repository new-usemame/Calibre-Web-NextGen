# Conversion time for large PDFs

Ingest uses **Ingest Timeout** in CWA Settings to wait for a file to finish copying. Its normal conversion safety budget is three times that setting. PDF inputs above 500 pages now receive a longer conversion budget automatically; the file-stability wait keeps the configured value.

The selected safety budget is the existing budget multiplied by `pages / 500`, rounded up to the next whole second. Books with 500 pages or fewer retain the existing budget. Automatic extensions stop at 12 hours; an explicitly configured base budget above that is preserved. An existing zero timeout remains unlimited for conversion. Its readiness allowance performs one immediate writer check: a closed file proceeds, while an active writer stays queued until a later attempt.

For example, at the default 15-minute setting:

| Input | Safety budget | Conversion deadline before recovery |
|---|---:|---:|
| 300-page PDF | 45 minutes | 40 minutes 30 seconds |
| 2,643-page PDF | 3 hours 57 minutes 53 seconds | 3 hours 34 minutes 6 seconds |
| 10,000-page PDF | 12 hours | 10 hours 48 minutes |
| TXT, MOBI or an unreadable PDF | 45 minutes | 40 minutes 30 seconds |

For PDFs, writer readiness runs before page counting, including files moved into ingest and retry entries. The first attempt uses the configured wait; a file still being written when that wait expires stays queued, with its original bytes retained. Retries use one immediate check for every format, so an open writer cannot repeatedly consume the worker's full wait. The processor checks readiness again before conversion in case the source reopens. Write-only and read/write access modes both count as active writers. Detection runs as the service user and can only see writers that user is permitted to inspect. As before, if `lsof` is unavailable or its ten-second check times out, ingest logs a warning and proceeds; that fallback cannot establish that a writer has closed.

The conversion deadline stays inside the safety budget to leave time for backing up and importing the original if conversion fails. All conversion stages share that deadline, including EPUB-to-KEPUB follow-up. A conversion timeout still imports the original rather than requiring successful conversion. Both new ingest events and retry-queue entries select their budget through the same wrapper. The first PDF preflight uses the configured wait, without extending that wait with the page multiplier; time spent in that preflight does not consume the later conversion watchdog. A page count is an estimate of the file at that moment, not a transactional snapshot across counting and conversion.

Page counting uses the already installed `pypdf` parser without extracting text, rendering pages or running OCR. It runs as the service user in a separate child with a five-second CPU allowance, 768MiB address-space limit, and an eight-second wall-clock limit. The child rejects encrypted PDFs and nonregular inputs. Invalid, missing, unsupported or slow inputs keep the base budget; failure to install the resource limits also keeps it. Linux containers support the limits; macOS hosts that reject the address-space limit use the fallback. No new dependency or page-count service is required.

The service log prints the selected safety budget and its base. A page count is a useful estimate, not a promise that every PDF will convert: document structure, images, fonts, Calibre plugins and hardware can still change conversion time. Raising Ingest Timeout raises the base allowance for unpaged formats as well.
