### Fixed

- **Kobo Store calls CWNG has no route for now reach the Kobo Store instead of getting a 404.** With the Kobo Store proxy on, a `/v1/` or `/v2/` store call CWNG doesn't recognise, such as one Kobo adds in a firmware update, is passed to the store and the store's answer comes back. With the proxy off it gets the same empty answer as other store calls. This is one part of the Libby "Network Error" in #2962; the OverDrive calls Libby makes are tracked there separately.
