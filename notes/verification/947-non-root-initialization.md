# Arbitrary-UID first-time initialization (#947)

Starting the current image with an explicit UID 1000 used to fail cwa-auto-library: discovery redundantly rewrote the already-correct default library path in image-owned dirs.json, raising EACCES and blocking the web service. The new equality guard avoids that write; changed paths still persist normally, and CWA_CALIBRE_LIBRARY_DIR remains authoritative.

Runtime-directory setup preserves root/abc ownership for normal LinuxServer startup and uses the running UID for explicit non-root startup. Required directory failures propagate immediately. ImageMagick's intended policy is installed during image build, so skipping privileged policy replacement retains the same policy. Non-root Qt processing uses a marker in writable config and avoids image mutations.

Product decision: support the documented explicit-UID/default-mount deployment without adding privileges, service extraction or host ownership repair. Non-default library discovery still requires the authoritative environment setting or writable external dirs.json. Existing mount permissions remain the deployer's responsibility. No claim is made about a physical rootless Podman host.

Independent root review found and corrected policy parity and hidden early-directory failure; no unresolved source/security blocker remains. A real whole image (38dd067006e03dcfa2c20ba2a78a38dcf8843e8bff2c3b145e0cb2a9d7480839) booted fresh explicit UID 1000 and normal root/PUID 1000 controls: both healthy, zero restarts and HTTP /login 200, with intended policy and runtime ownership. Owned containers were removed after preserving evidence.

Focused initialization/ownership regressions: 134 passed. Full local smoke/unit run: 10,256 passed, 124 skipped, four failures. Two are the established baseline metadata-replacement SQLite I/O failures. The malformed new changelog fragment was corrected; an obsolete assertion on literal install commands was removed. The real helper test now creates and writes the runtime and separate opt-in-plugin directories, retaining plugin separation coverage. These tests, remaining Calibre environment checks and actual changelog assembly now pass: 25 passed. No second full-suite run is claimed after these test/fragment-only changes.

The no-op discovery test was seen red before the guard, including an explicit denied-write reproduction. Runtime failure tests detect a failed early directory hidden by a later success. Shell syntax and diff checks pass. This patch may conflict with PR #2403 and the separate #995 cache change in cwa-init; rebase after the first merge rather than bundling their scopes.


## Serial merge integration (2026-10-02)

Rebased onto current main4369e2c56, including complete-view selection and its translation follow-up. Only CHANGES add/link conflicts were resolved; all preceding rows and SPA strings are preserved. Dockerfile, cwa-init, AutoLibrary, both new shell helpers and feature tests are byte-identical to the independently reviewed original. Original actual explicit UID1000/root-PUID1000 whole-image startup evidence remains applicable to unchanged initialization code. Other-owner #2403 remains outside this work.

Current focused startup/environment/fragment/classifier packet26 passed; adjacent AutoLibrary discovery and root/non-root service-helper packet51 passed, total77. Shell syntax and diff checks pass. Independently measured classifier closure214 of272 matches the README. Fresh exact-head CI is required after rebase.
