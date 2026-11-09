### Fixed

- **OPDS search finds "C++".** The catalog told reader apps to search at `/opds/search/<term>`, where an app leaves a `+` as it is, but the server read that `+` as a space, so a search for "C++" ran as "C". The catalog and its search description now point apps at `/opds/search?query=<term>`, where a plus is sent as `%2B` and comes through intact. `/opds/search/<term>` still works for apps that build it themselves, and no longer fails on servers other than the bundled one. (#2846)
