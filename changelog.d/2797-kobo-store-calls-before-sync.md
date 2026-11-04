### Fixed

- **With Proxy unknown requests to Kobo Store on, a store refusal just before a Kobo syncs gets the same empty answer as with the setting off, and the log says what the store answered.** The profile, subscriptions, deals and next-read calls now go to the store from the server instead of by redirect, so a refusal is visible as a `Kobo Store refused` log line. (#2797)
