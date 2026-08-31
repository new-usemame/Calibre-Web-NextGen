/*
 * Shared permission predicates.
 *
 * A permission that is only ever spelled out at each call site drifts: #1288
 * shipped with three upload controls (Library toolbar, account menu, Add a
 * format) that each had to agree on the same two-part rule, and the server-side
 * half of that rule was missing entirely. One predicate, one place to fix.
 */
import type { Me } from './api';

/**
 * May this user add book files right now?
 *
 * Two-part gate, mirroring how classic gates its navbar upload button
 * (`cps/templates/layout.html`: `current_user.role_upload() and g.allow_upload`):
 * the per-user role AND the admin's instance-wide "Enable Uploads" switch.
 *
 * `features.uploading` is absent on servers older than the fix, and absent
 * means ON — it matches the column default (`config_uploading` defaults to 1),
 * so an older server keeps working instead of hiding upload everywhere. That is
 * the opposite of the opt-in flags in `ServerFeatures`, which default off.
 *
 * UI-gating only. Enforcement is server-side on each endpoint
 * (`cps/api/upload.py`, `cps/editbooks.py::upload_required`).
 */
export function canUploadBooks(me: Me | undefined | null): boolean {
  return !!me?.role?.upload && me?.features?.uploading !== false;
}

/** May this user open book content in a browser reader? Mirrors viewer_required. */
export function canReadBooks(me: Me | undefined | null): boolean {
  return !!me?.role?.viewer;
}

/** May this user receive book files as downloads? Mirrors download_required. */
export function canDownloadBooks(me: Me | undefined | null): boolean {
  return !!me?.role?.download;
}
