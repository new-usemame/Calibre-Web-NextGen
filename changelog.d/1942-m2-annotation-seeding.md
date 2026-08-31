### Added

- **Owned Kobo annotations can become safely server-authoritative without a
  manual database edit.** CWNG captures the complete upstream annotation set
  per active Kobo, preserves its exact pages, and keeps unsafe or oversized
  sets proxied instead of serving a destructive subset.

### Fixed

- **Server-authoritative Kobo books no longer fall back to a stale cloud
  replacement set.** Seed promotion now proves captured annotation IDs, keeps
  newer server edits and tombstones, serializes reconciliation per book,
  expires abandoned captures, isolates later-device failures, and provides an
  authenticated retry for an initial quarantined seed.
- **New/reset Kobo devices now establish routing evidence before their first
  local annotation response.** Authority lookup failures remain tri-state,
  corrupt capture proof is rebuilt from the complete live set, reconciliation
  uses server-owned row revisions, and post-authority sets over 100 are flagged
  while remaining losslessly available in one complete response.
- **Authoritative annotation GET failures can no longer become destructive
  empty sets or stale Kobo replacements.** CWNG always answers a prior CWNG
  ETag locally, durably snapshots each complete response for exact replay when
  live reads fail, and blocks initial authority while same-ID reconciliation
  conflicts remain unresolved.
- **Fallback snapshots now belong to one exact authority revision.** A local
  Kobo PATCH advances and invalidates the rendered-set digest before its 204;
  stale snapshots are rejected, while a current complete live render is never
  replaced by older bytes if snapshot persistence fails.
- **Owned Kobo PATCHes now commit annotation changes and their authority
  watermark atomically.** Create, edit, delete, and mixed batches roll back as
  one request on failure, remain retryable in the recovery spool, and cannot
  leave an older snapshot eligible after partial persistence.
