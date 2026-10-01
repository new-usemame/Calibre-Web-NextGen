# Environment-managed Generic OIDC

Generic OpenID Connect can be configured at application startup without storing
the provider client secret or endpoints in `app.db`. The environment owns the
whole Generic provider as soon as any variable beginning with
`GENERIC_OAUTH_` is set. In that mode the database's Generic-provider row is
ignored, and the Generic provider fields on the classic admin page are
read-only. GitHub, Google, and the existing global security settings remain
under their current configuration paths.

The provider is active only when `GENERIC_OAUTH_ENABLED=true` is explicit and
the declaration is complete. An incomplete, invalid, or unknown
`GENERIC_OAUTH_*` variable disables Generic OIDC and shows a safe error on the
admin page; old database values never fill gaps. `GENERIC_OAUTH_ENABLED=false`
disables Generic OIDC while leaving other configured OAuth providers alone.
Without any `GENERIC_OAUTH_*` variable, the existing database-managed behavior
is unchanged.

## Manual endpoint example

```yaml
environment:
  GENERIC_OAUTH_ENABLED: "true"
  GENERIC_OAUTH_CLIENT_ID: "calibre-web"
  GENERIC_OAUTH_CLIENT_SECRET_FILE: "/run/secrets/oidc-client-secret"
  GENERIC_OAUTH_SERVER_URL: "https://id.example.org/oidc"
  GENERIC_OAUTH_AUTH_URL: "https://id.example.org/oidc/authorize"
  GENERIC_OAUTH_TOKEN_URL: "https://id.example.org/oidc/token"
  GENERIC_OAUTH_USERINFO_URL: "https://id.example.org/oidc/userinfo"
  GENERIC_OAUTH_SCOPE: "openid profile email"
  GENERIC_OAUTH_USERNAME_MAPPER: "preferred_username"
  GENERIC_OAUTH_EMAIL_MAPPER: "email"
  GENERIC_OAUTH_LOGIN_BUTTON: "Sign in with Example ID"
  GENERIC_OAUTH_GROUP_CLAIM: "groups"
  GENERIC_OAUTH_REQUIRE_GROUP: "true"
  GENERIC_OAUTH_ALLOWED_GROUPS: "calibre-readers"
  GENERIC_OAUTH_ADMIN_GROUP: "calibre-admins"
  GENERIC_OAUTH_DEFAULT_ROLE: "download,viewer"
```

Set `GENERIC_OAUTH_CLIENT_SECRET` instead of the `_FILE` variable when the
secret is supplied directly by the process environment. If both are present,
a non-empty direct secret takes precedence. A missing or unreadable secret file
fails closed. The file is read once when the application starts; restart every
application process after rotating it. No restart-time database write is
performed.

For provider discovery, set `GENERIC_OAUTH_METADATA_URL` to an absolute HTTP(S)
OIDC discovery document URL and omit the four manual endpoint variables. The
document must supply `issuer`, `authorization_endpoint`, `token_endpoint`, and
`userinfo_endpoint`. Discovery runs during startup and never writes endpoint
values to the database. Use HTTPS endpoints with a valid certificate in
production; the application's existing OAuth TLS verification policy applies.

All endpoint values must be absolute HTTP(S) URLs without embedded credentials.
`GENERIC_OAUTH_ENABLED` accepts `true`, `false`, `1`, `0`, `yes`, `no`, `on`,
and `off` (case-insensitive). To activate the provider, it must be true and
the required fields below must resolve:

| Variable | Requirement |
| --- | --- |
| `GENERIC_OAUTH_ENABLED` | Must be explicitly `true` to activate the provider |
| `GENERIC_OAUTH_CLIENT_ID` | Required when enabled |
| `GENERIC_OAUTH_CLIENT_SECRET` or `GENERIC_OAUTH_CLIENT_SECRET_FILE` | One must resolve to a non-empty secret; a non-empty direct secret wins |
| `GENERIC_OAUTH_METADATA_URL` | Optional discovery document; when set, it supplies all provider endpoints |
| `GENERIC_OAUTH_SERVER_URL`, `GENERIC_OAUTH_AUTH_URL`, `GENERIC_OAUTH_TOKEN_URL`, `GENERIC_OAUTH_USERINFO_URL` | All four are required if metadata URL is omitted |

Other variables are strings unless noted:

| Variable | Purpose | Default |
| --- | --- | --- |
| `GENERIC_OAUTH_SCOPE` | Space-separated scopes | `openid profile email` |
| `GENERIC_OAUTH_USERNAME_MAPPER` | Userinfo claim for the account name | `preferred_username` |
| `GENERIC_OAUTH_EMAIL_MAPPER` | Userinfo claim for the email address | `email` |
| `GENERIC_OAUTH_LOGIN_BUTTON` | Public login button label | `OpenID Connect` |
| `GENERIC_OAUTH_GROUP_CLAIM` | Top-level userinfo claim containing group names | `groups` |
| `GENERIC_OAUTH_REQUIRE_GROUP` | Require membership in the allow-list | `false` |
| `GENERIC_OAUTH_ALLOWED_GROUPS` | Comma-separated allowed groups | empty |
| `GENERIC_OAUTH_ADMIN_GROUP` | Group considered for OAuth admin mapping | `admin` |
| `GENERIC_OAUTH_DEFAULT_ROLE` | Comma-separated non-admin grants for newly created users | global new-user default role |

Allowed default-role tokens are `download`, `viewer`, `upload`, `edit`,
`passwd`, `delete_books`, and `edit_shelves`. `admin` is intentionally not a
valid default-role token. An explicit `GENERIC_OAUTH_ADMIN_GROUP` does not by
itself grant administrator access: the existing global **Manage admin role
from OAuth group** setting remains the authority and must be enabled to use
group-based admin mapping.

Environment configuration replaces the Generic provider's complete database
configuration. Database-only Generic settings for client credentials,
endpoints, group rules, and the per-provider new-user role are not inherited.
If `GENERIC_OAUTH_DEFAULT_ROLE` is omitted, the existing global new-user default
role setting still applies. Configure the provider's intended group and
new-account policy explicitly, alongside the normal global OAuth security
settings.

When a valid environment declaration is active, OAuth is selected in memory
even if `config_login_type` in `app.db` is still Standard Authentication. The
admin page reports this choice and disables the login-type control. The
database value is left untouched. When the declaration is disabled, the saved
login type remains in effect; other provider declarations and login methods
retain their existing behavior.

The client secret is never displayed on the admin page, exposed through the
unauthenticated authentication API, or included in feature-generated
configuration errors. Keep deployment environment files and mounted secret
files protected using the secret-management controls of your container or
orchestration platform.
