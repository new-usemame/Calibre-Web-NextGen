# OPDS 2 localized metadata and publication details

A legal OPDS 2 publication can have language-map titles or contributor names, or advertise a standalone publication detail document. These wire shapes previously failed the bounded parser or could not be browsed. Ordinary Atom and OPDS 2 feeds remain supported.

The parser now accepts nonempty language maps at metadata titles and contributor object names. All tags and values must pass the portable Readium schema grammar and existing budgets before one is selected. Case-colliding tags, invalid or oversized unselected values are refused. Link titles remain strings. The saved account locale is captured before blocking I/O, normalized for display selection, matched exactly then by regional variant/language parents, with English then deterministic alphabetical fallback. Edition languages and identifiers remain separate.

Only the explicit `application/opds-publication+json` media type is accepted for a standalone publication; it must have an acquisition link and cannot also contain feed collections. Safe non-templated `self`/`alternate` publication links become owner- and connection-revision-bound opaque navigation. There is no eager traversal. Purchase, loan, sample, preview, subscription and acquisition actions cannot become metadata reads based on their media type. Own self-links and current-resource loops are suppressed. Existing transport origin/credential/private-network checks still apply, and only supported direct EPUB/PDF offers can create acquisition jobs.

The browser keeps summaries with details navigable without falsely declaring no available EPUB/PDF. Explicit navigation moves focus to the named results region after it settles; background refresh does not take focus. Requests still use normal approvals, private staging, Calibre ingest and account-owned receipts.

Reproduce the parser/service/API boundary with the existing development environment:

```sh
python -m pytest -q tests/unit/test_acquisition_opds_publications.py tests/unit/test_acquisition_opds.py tests/unit/test_acquisition_catalog.py tests/unit/test_acquisition_api.py
```

`tests/integration/acquisition_opds_publication_runtime_probe.py` is invoked by the existing full-runtime integration wrapper. It creates an original legal EPUB and loopback catalog/detail/download fixture, explicitly opens the detail, refuses another account’s selection, uses production child HTTP/worker/staging and the normal processor subprocess, then checks the receipt, source bytes and original English Calibre title. Display metadata is French and edition language remains English. The wrapper retains the existing receipt-recovery, edition, EPUB patch, KEPUB/PDF, bundle, torrent and reading-state assertions. The Docker test copies probe inputs only; product code must be baked into the candidate image.

`frontend/e2e/acquisition-opds-details.spec.ts` is the presentation regression. Its API fixture distinguishes summary metadata from a chosen detail and an explicit job POST. Separate native browser evidence exercises actual session/CSRF/API selections, source GETs, worker, processor, receipt and book opening at root and `/books`, desktop and phone widths. DOM focus/axe checks do not claim spoken assistive-technology output or real-provider transactions.

Primary format sources: [OPDS 2.0](https://specs.opds.io/opds-2.0), [Readium default context](https://readium.org/webpub-manifest/contexts/default/), [language-map schema](https://readium.org/webpub-manifest/schema/language-map.schema.json), and [link schema](https://readium.org/webpub-manifest/schema/link.schema.json). These are protocol references, not newly configured runtime service endpoints.

No schema, dependency, provider account, new adapter, automatic loan/purchase, Anna integration or per-user private connection is introduced. Current Linux CI and immutable selected-image proof are delivery gates; native evidence alone does not establish ARM runtime behavior.
