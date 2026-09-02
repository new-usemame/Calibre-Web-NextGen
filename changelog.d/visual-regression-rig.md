### Changed

- **The browser regression harness now catches six high-value pixel changes on reproducible bytes.** An opt-in Chromium visual lane runs only in an isolated Docker rig, pins its browser and rendering inputs, includes a fully translated French view, and keeps a hard six-snapshot ceiling so visual failures remain actionable. The same update moves Playwright to 1.62.1 and records intermittent E2E failures in a durable flake ledger instead of retrying them away.
