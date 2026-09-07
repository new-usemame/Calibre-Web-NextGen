### Fixed

- **Impact-map staleness is visible without blocking contributor changes.** CI regenerates the Python impact map and recall report, publishes fresh downloads and a currency summary, and checks call accounting on freshly generated graphs. Generation and evidence errors block the required test summary while staleness stays advisory. Refresh rejects summary paths that would corrupt inputs or outputs and removes obsolete currency metadata before a repeated attempt. Improved recall no longer fails the historical-report test.
