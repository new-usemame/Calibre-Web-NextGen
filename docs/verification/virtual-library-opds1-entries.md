# OPDS 1 complete-entry details

OPDS 1 partial catalog entries can advertise an alternate complete-entry document with `application/atom+xml;type=entry;profile=opds-catalog`. CWNG already offered this explicit navigation, but the bounded XML parser required a feed root and rejected the standalone Atom entry. That details path now works.

A namespaced Atom entry root normalizes to one publication with its title, authors, declared languages, identifiers, description and supported direct offers. It must contain one nonempty Atom identity and update field, a nonempty title and a recognized acquisition link. Existing XML byte, depth, node, publication, link and text budgets, DTD/entity refusal, scoped `xml:base`, final-response URL resolution and description sanitization apply unchanged. An ordinary Atom feed retains its existing interpretation. This is a bounded supported-format parser, not a general Atom schema validator; unsupported extension acquisition relations are not guessed.

Choosing an advertised detail is an explicit metadata read through the existing opaque owner- and connection-revision-bound selection. It does not fetch the EPUB eagerly or create a job. Complete-entry self-links and current-resource loops are suppressed. Purchase, borrowing, subscription, sample and indirect acquisition stay nondownloadable; their catalog MIME type cannot turn them into metadata actions. Only existing supported direct EPUB/PDF offers create requests. Transport origin, credential and private-network authority, approvals, normal processing and owned import receipts remain unchanged.

Reproduce the protocol/service boundary with the existing development environment:

```sh
python -m pytest -q tests/unit/test_acquisition_opds_entries.py tests/unit/test_acquisition_opds.py tests/unit/test_acquisition_opds_publications.py tests/unit/test_acquisition_catalog.py
```

The existing full-runtime integration wrapper invokes `run_opds_publication_runtime(..., protocol="opds1")` in `tests/integration/acquisition_opds_publication_runtime_probe.py`. Its original legal EPUB goes through an owned loopback Atom feed, chosen standalone entry, production child HTTP/worker/private staging, normal Calibre processor and import receipt. Assertions cover another account's refused detail selection, no purchase/loan GET, original edition and byte identity, requesting user's membership, source/private cleanup and a distinct receipt from the OPDS 2 fixture. The wrapper retains the prior receipt-recovery, edition, EPUB processing/conversion, bundle/torrent and reading-state checks. Docker copies test inputs only; product code must be baked in the candidate image.

Native browser verification uses actual account/session/CSRF/API selections and that XML details path, then normal processing, receipt and imported-book opening at root and `/books`, desktop and phone. It reuses the existing results-focus and summary controls. DOM focus and axe evidence do not establish spoken assistive-technology timing, physical touch, real-provider transactions or ARM runtime execution. Current Linux candidate CI and anonymous immutable selected-image content/pullability remain separate delivery gates.

Primary format reference: [OPDS 1.2, entry documents and partial/complete entries](https://specs.opds.io/opds-1.2#5-opds-catalog-entry-documents). This documentation link is not a newly configured runtime endpoint. No schema, dependency, new adapter, provider account, automatic loan/purchase, Anna integration or private per-user connection is introduced.

Delivery is tracked in [PR #2467](https://github.com/new-usemame/Calibre-Web-NextGen/pull/2467). Its body records current required CI and immutable image evidence; the local proof does not claim a published release.
