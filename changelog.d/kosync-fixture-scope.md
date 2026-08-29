### Fixed

- **The fast test lane no longer starts a Docker container for tests that don't need one.** Enabling KOReader sync for the integration suite was applied to a whole module, including a class of pure helper tests, so the quick lane quietly booted a container to run three tests that never touch it. The fast lane now refuses, by name, any quick test that depends on a container.
