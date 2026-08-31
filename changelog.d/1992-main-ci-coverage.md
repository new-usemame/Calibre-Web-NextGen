### Fixed

- **Every commit that lands on the main branch is verified by CI again.** A new push used to cancel the still-queued test run of the previous commit, so under a busy merge rate most main commits were never tested while development images still published from them.
