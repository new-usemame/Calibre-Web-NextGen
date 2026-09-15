### Fixed

- **KOReader update instructions now call out the frozen legacy plugin repository.** Users whose Updates Manager or appstore.koplugin still points at `new-usemame/cwasync.koplugin` are told to repoint it manually because that repository receives no further releases and does not redirect to the new one, avoiding a permanent “no new release” result that looks like an up-to-date plugin (#2062).
