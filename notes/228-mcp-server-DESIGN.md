# #228 — optional CWNG MCP server

## Verdict

**Viable, but requires operator sign-off before code or dependencies.** This is a new authenticated automation surface, not a small REST wrapper. Keep it an optional sidecar under `contrib/` so the main web process, default image, and Python dependency set remain unchanged.

## Proposed v1

Read-only first: search books, fetch book details/formats, list shelves and shelf contents, and list recent additions. Each tool delegates to versioned `/api/v1` endpoints using a regular user's app password, so existing visibility and shelf ownership remain the source of truth. Do not scrape HTML, query the database, read the ingest directory, or accept an administrator password.

Mutations are a later capability set: shelf membership/creation and send-to-e-reader. Each must be individually allowlisted in sidecar configuration and return a preview suitable for the MCP client's confirmation flow. Deletes, metadata refresh, ingest, user administration, Kobo cursor changes, and raw downloads are out of v1.

## Security and operations

- Default bind is loopback/container-network only; remote exposure belongs behind the operator's authenticated reverse proxy.
- Credentials arrive through a secret/file environment mechanism and are never logged or returned as tool output.
- Enforce server-side authorization; `READ_ONLY` flags are defense-in-depth, never the permission boundary.
- Rate-limit requests, cap result/page sizes, redact paths, and emit structured audit events containing user/tool/outcome but no book contents or credentials.
- Tool descriptions must not claim confirmation guarantees the protocol cannot enforce; the sidecar itself blocks disabled mutations.
- Version the tool schema and test every tool against CWNG's API contract in CI.

## Proposed dependency gate

Operator choice required between an official MCP SDK (less protocol code, new pinned dependency and release cadence) and a minimal JSON-RPC implementation (no SDK dependency, substantially higher protocol/security maintenance). Recommendation: approve a pinned official SDK in the **sidecar only**, after license/supply-chain review. OpenAPI generation is optional later; current API coverage is incomplete enough that hand-authored v1 adapters are safer than pretending the generated surface is comprehensive.

## Delivery phases

1. Approve SDK/runtime, packaging, and threat model.
2. Inventory only the five read endpoints and add missing API contract tests.
3. Implement stdio transport first, then optional streamable HTTP with explicit origin/auth rules.
4. Run a security review before exposing any transport or adding mutation tools.

No code or dependency is added in this wave. The future sidecar/API work requires security review.
