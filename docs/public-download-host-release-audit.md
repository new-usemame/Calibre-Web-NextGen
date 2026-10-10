# Public download hosts — release audit

Audit target: assembled release candidate `5d0015fe99`, 2026-09-08.

## Release scope and verdict

There is no integrated Anna's Archive acquisition provider or Store / Discover feature in this release candidate. The release does not expose unfinished controls, credentials, downloads, or settings for that integration. Functional completion of the separate development branch is therefore not a condition of this release.

Inspection covered the backend API registry, provider modules, configuration and role definitions, frontend router/sidebar/admin/account surfaces, release documentation and the separate Store development branch. The assembled tree contains the Anna's Archive metadata-provider request as open finding `F-e12078`; a request is not shipped functionality.

As a runtime check, the actual `api_v1` blueprint was registered on an isolated Flask application. Its 113 registered rules included no Store, Shelfmark, Anna's Archive or experimental-feature route. Requests to `/api/v1/store`, `/api/v1/store/search`, `/api/v1/store/acquire`, and `/api/v1/store/credentials` all returned 404. This establishes the API surface of this candidate, not provider availability or an external download test.

## Separate development work

The local `feature/store-discover` branch at `4044f662f7` is not an ancestor of the release candidate. It contains a separate Shelfmark adapter, Store API/page and encrypted credential storage. Its `STORE-DISCOVER.md` explicitly calls the feature experimental and default off.

Read-only inspection of that branch found:

- Store API calls require both the experimental feature flag and the user's Store-access role; off or unauthorized calls return 404.
- The frontend route and sidebar entry require both flags through `canUseStore`.
- Administrator review/revoke actions require the feature to be enabled and administrator authorization. Experimental settings themselves are administrator-only.
- Per-user provider credential transport is explicitly unsupported. Bootstrap advertises no credential providers, and the page does not offer unusable credential-entry fields.

Those are source observations of the development branch, not a declaration that its download lifecycle is verified. The branch was not merged, enabled, configured or run against a public host. Keep it outside this release. Before eventual shipment, it needs its own current provider-contract, request ownership, quota, failure handling, credential and ingest-attribution verification.

## Standalone Shelfmark pairing

The README's Shelfmark pairing instructions describe a separately deployed service sharing the ingest folder. They do not implement Store / Discover inside NextGen. This release's watch-folder imports have no authenticated uploader identity and therefore enter the global library without automatically creating personal-library membership for the external requester. Authenticated browser uploads use a separate attribution manifest.

The README now states this distinction and explains the current user flow: readers with Global Library access add the imported book to their own selection; an administrator adds it for managed accounts. Whole-library accounts continue seeing permitted imports automatically. This clarification prevents the multi-user feature from implying an external requester-to-personal-library link that does not exist.

No product-code change or new feature gate was needed in the assembled release. No external credentials were read, external services changed, large downloads attempted, or provider functionality represented as tested. The edit is documentation only; no implementation-mirroring test was added for absent feature code.
