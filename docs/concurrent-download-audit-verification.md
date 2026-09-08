# Concurrent metadata-embedded downloads

Baseline: release audit candidate `802004edef`, 2026-09-08.

The concurrent desktop/mobile shared-book browser check found one successful EPUB download and one failed response. Runtime logs showed Calibre rejecting a second process because the library was locked, followed by a nonexistent UUID export path and failed temporary-file cleanup.

## Root causes and correction

1. The two-slot metadata-export worker pool launched concurrent Calibre processes without the existing process-shared library-operation lock. Calibre uses an exclusive library lock even for export. Exports now acquire `metadata_db_write_lock` on the existing OS worker before spawning and waiting for Calibre. The request still waits cooperatively through the gevent thread pool; HTTP requests/tests are not serialized to mask the defect.
2. A failed export returned a plausible directory/UUID pair even when no artifact existed. Nonzero process status and missing output now return `(None, None)`, selecting the caller's existing original-file fallback.
3. The download helper renamed unique exports onto one shared temporary basename. Concurrent responses could overwrite each other's output or unlink the other's file. Unique export names are now retained through streaming and cleanup. Checksum registration receives the actual client filename from Content-Disposition separately, using the existing `filename_for_matching` API.

No content permissions, personal-library membership or device-entitlement policy is changed.

## Behavioral evidence

`test_concurrent_metadata_downloads.py` runs real OS threads and Flask requests. Its fake Calibre process rejects overlapping process lifetimes, exercising the real file lock; the HTTP test deliberately synchronizes two distinct exports just before file streaming, forcing the former shared-basename collision. It verifies independent response bytes, unique checksum paths, the actual decoded client filename, successful temporary cleanup, and an untouched original library file. Separate cases reject both nonzero and nominal-success exports that produced no file, and prove that embed failure serves the original bytes.

- Baseline: **4 failed, 1 passed**. Failures cover overlapping Calibre processes, both missing-artifact cases, and the staged-file collision.
- Fixed new suite: **5 passed**.
- Adjacent timeout/fallback, email/checksum, personal covers, shared-book continuation, Kobo download, gevent lock responsiveness and process-shared metadata-lock suites: **183 passed, 1 xfailed** in 21.43 seconds. The existing pre-release Kobo-upgrade xfail was neither introduced nor weakened by this change.

The integrating audit parent owns the real concurrent desktop/mobile rerun against the rebuilt candidate. This report does not claim that a simulated process proves the Calibre executable or final container was exercised; it records the independently reproducible code-level causes and regression tests.
