# Usenet book-source verification

This change extends the existing optional book-sources feature with Newznab/Torznab search and SABnzbd downloading. It remains disabled by default. Torrent clients and public-provider integration are outside this slice.

## Real isolated workflow

Verification used real Prowlarr and SABnzbd 5.1.3 containers, CWNG built from this branch, and the original offline EPUB/NNTP fixture in `tests/fixtures/virtual_library_fixture.py`. All services ran on a dedicated local Docker network. There was no public indexer, Usenet provider, or household service.

The observed flow was CWNG search through Prowlarr, an NZB upload to SAB, one local NNTP article, successful SAB postprocessing, normal CWNG ingest, an import receipt with book IDs, and the original chapter rendered in the EPUB reader. The source and imported EPUB SHA-256 matched. A paused download survived a CWNG restart with the same SAB job identity and imported after resuming. A second account reused the download and received its own private receipt and My Library membership.

## Behavioral boundaries

Tests exercise lost submit responses without blind retransmission; definite rejection and explicit retry; missing/failed SAB jobs; empty or ambiguous completed folders; bad credentials and unavailable services; admission concurrency; stale configuration, search responses, and forms; opaque pagination; torrent-result unavailability; redirect credential scope; removed LAN destinations; completed-file symlinks and special files; and repeated additive migration on an existing OPDS database.

Independent backend and browser refuters drove several fix/retest rounds. Desktop Chromium, phone Chromium, and phone WebKit covered light/dark states, setup/edit/delete, rejection, cancellation, gated deep links, and the imported reader. Twenty-four scoped axe scans reported no violations. VoiceOver and a real mobile device were not exercised.

## Automated evidence and limits

The final post-rebase acquisition, migration, HTTP, translation, subpath, and changelog gate passed 431 tests with one skip; the independent API/Usenet/HTTP/storage run passed 130 tests. Frontend unit tests passed 192 tests. Browser read-failure and connection cases passed 41 tests, and the final settings rerun passed all nine desktop/phone cases. Twenty-four live requests at parallelism eight resolved to one imported request with no additional descriptor or NNTP article fetch.

The full backend run passed 10,173 tests, skipped 103, and initially failed three. Its changelog-format failure was corrected and checked separately. Two metadata-database disk-I/O tests also failed on the untouched prerequisite baseline (10,147 passed, 103 skipped, the same two failures), while the isolated six-test metadata module passed. The full suite is therefore not claimed green. These failures concern the existing metadata replacement test environment, not acquisition; the PR records their exact names.

Release-image publication and household deployment were not exercised: this milestone is a needs-review PR, with no merge or release authorized.

## Independent review fixes

The review’s slow-child upload defect reproduced with real subprocesses and a local HTTP server: 10 KB passed but 60/200/500 KB timed out after a half-second startup delay. The fixed transport supplies complete temporary-file stdin and EOF; all four sizes pass while existing hard-deadline and cancellation tests remain green.

Other regressions confirm per-release handling of foreign download origins; a seven-day ceiling on unfinished SAB work; fresh durable names after a definite failed download; whole-attempt invalidation even when another subscriber is cancelled; rejection resolving existing subscribers; nullable-key legacy recovery; a lost submit response after a fresh retry; direct completed-file selection without scanning sibling downloads; and explicit credential reentry when the server origin changes.

The final independent backend refuter passed 187 tests and found no remaining concrete blocker. The owner’s broad focused gate passed 413 tests with one skip. The latest desktop/phone settings run passed 11 cases, including credential reentry and retained form state. Frontend unit tests still passed 192, and typecheck/build passed.

After rebasing onto main including #2388, the requested full backend run passed 10,195 tests, skipped 103, and failed only the same two metadata disk-I/O cases reproduced on the untouched baseline. Final Linux CI is recorded in the PR; this local full-suite result is not described as green.

## Reproduce the offline boundary

Run the fixture on an isolated network; configure Prowlarr Generic Newznab to its `/api` endpoint and disposable `fixture-key`; configure SAB NNTP to the fixture on port 8119. Add a SAB books category and read-only completed-folder mapping to CWNG. Bind the Prowlarr protocol endpoint to that client, explicitly authorize only the needed local origins, probe and enable both connections, and request the fixture release with a granted test account. Compare `/counts`, the SAB identity, and the CWNG receipt before and after restart or a second account request. See [book-source setup](../book-sources.md) for the user-facing configuration.
