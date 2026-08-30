Fixed reverse-proxy SSO opening the Classic login instead of the New UI. The
existing app-wide request hook already identifies configured proxy-header users
on `/app/` and `/api/v1/auth/me`; these deployments now use that working SPA
path by default. (#1931, reported by @justemu)
