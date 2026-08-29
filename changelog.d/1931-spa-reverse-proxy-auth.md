Fixed reverse-proxy SSO opening the Classic login instead of the New UI: an
existing local user asserted by the configured proxy header is now identified
on both `/app/` and `/api/v1/auth/me`. Disabled proxy-header login and unknown
users remain unauthenticated. (#1931, reported by @justemu)
