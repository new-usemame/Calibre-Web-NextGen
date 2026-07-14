# #548 — Wizarr invitations

## Verdict

**Needs operator and upstream coordination; not implementable as a CWNG-only feature today.** Wizarr is an external service and the issue itself reports that its Calibre-Web integration/API contract is incomplete. Adding an unconsumed CWNG route would create a privileged public surface without delivering invitations.

## Required contract

A viable integration needs an agreed Wizarr provider contract covering: create invitation, redeem exactly once, create a least-privilege CWNG user, assign roles/library restrictions, expire/revoke an invite, and report status without exposing passwords. The external service must confirm which endpoints, authentication scheme, errors, and idempotency keys it will actually call.

## CWNG-side design if upstream commits

- Use opaque, hashed, short-lived, single-use invitation tokens; never let Wizarr choose or retain a user's password.
- Redemption is a CWNG-hosted same-origin form with CSRF protection and rate limits. The invite carries an administrator-selected role template, never arbitrary role bits from the caller.
- Separate invitation-management permission from full user administration and require an app credential scoped to that capability.
- Audit create/revoke/redeem events; do not log tokens or passwords.
- Make create/redeem idempotent and transactionally consume the token with user creation.
- Admin UI must work with mouse, keyboard, and touch and expose expiry/error states accessibly on mobile and desktop.

## Recommended next step

Ask the operator whether to pursue (A) an upstream Wizarr provider contract or (B) a native CWNG invitation flow that has no external dependency. Recommendation: native invitations provide the requested self-service/password privacy with fewer trust boundaries; Wizarr compatibility can later wrap that stable API. Either choice creates authentication/user-management routes and requires security review.

No code, dependency, or external URL is added in this wave.
