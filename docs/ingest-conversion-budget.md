# Conversion time for large PDFs

Ingest uses **Ingest Timeout** in CWA Settings to wait for a file to finish copying. Its normal conversion safety budget is three times that setting. PDF inputs above 500 pages now receive a longer conversion budget automatically; the file-stability wait keeps the configured value.

The selected safety budget is the existing budget multiplied by `pages / 500`, rounded up to the next whole second. Books with 500 pages or fewer retain the existing budget. Automatic extensions stop at 12 hours; an explicitly configured base budget above that is preserved. An existing zero timeout remains unlimited.

For example, at the default 15-minute setting:

| Input | Safety budget | Conversion deadline before recovery |
|---|---:|---:|
| 300-page PDF | 45 minutes | 40 minutes 30 seconds |
| 2,643-page PDF | 3 hours 57 minutes 53 seconds | 3 hours 34 minutes 6 seconds |
| 10,000-page PDF | 12 hours | 10 hours 48 minutes |
| TXT, MOBI or an unreadable PDF | 45 minutes | 40 minutes 30 seconds |

The conversion deadline stays inside the safety budget to leave time for backing up and importing the original if conversion fails. All conversion stages share that deadline, including EPUB-to-KEPUB follow-up. A timeout still imports the original rather than requiring successful conversion. Both new ingest events and retry-queue entries select their budget through the same wrapper.

Page counting uses the already installed `pypdf` parser without extracting text, rendering pages or running OCR. It runs as the service user in a separate child with a five-second CPU allowance, 768MiB address-space limit, and an eight-second wall-clock limit. The child rejects encrypted PDFs and nonregular inputs. Invalid, missing, unsupported or slow inputs keep the base budget; failure to install the resource limits also keeps it. Linux containers support the limits; macOS hosts that reject the address-space limit use the fallback. No new dependency or page-count service is required.

The service log prints the selected safety budget and its base. A page count is a useful estimate, not a promise that every PDF will convert: document structure, images, fonts, Calibre plugins and hardware can still change conversion time. Raising Ingest Timeout raises the base allowance for unpaged formats as well.
