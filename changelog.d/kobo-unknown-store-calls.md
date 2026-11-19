### Fixed

- **Libby and other Kobo Store pages no longer fail with "Network Error" on a store call CWNG doesn't know.** With the Kobo Store proxy on, a store call CWNG has no route for, such as the calls Libby makes since Kobo moved library cards to the account, is now passed to the Kobo Store instead of being answered with a 404. With the proxy off it gets the same empty answer as other store calls (#2962).
