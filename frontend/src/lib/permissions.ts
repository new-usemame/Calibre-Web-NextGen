/*
 * Shared permission predicates.
 *
 * A permission that is only ever spelled out at each call site drifts: #1288
 * shipped with three upload controls (Library toolbar, account menu, Add a
 * format) that each had to agree on the same two-part rule, and the server-side
 * half of that rule was missing entirely. One predicate, one place to fix.
 */
import type { Me, Shelf } from './api';

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

/**
 * May this user permanently delete a whole book or one of its formats?
 *
 * Classic's detail control checks both roles directly. Its format and bulk
 * controls sit behind edit-only routes and then call the delete-role core, so
 * their effective policy is the same conjunction.
 */
export function canDeleteBooks(me: Me | undefined | null): boolean {
  return !!me?.role?.delete_books && !!me?.role?.edit;
}

/**
 * May this user open the cover editor for a book?
 *
 * Editors change the library cover of any book they can see
 * (`cps/cover_picker.py::_load_book`). Everyone else edits a private cover,
 * which the server keeps for a book in their library or, with Global Library
 * access, any book they may browse (`cps/api/actions.py::_personal_cover_book`).
 * A book reached only through a public shelf is neither, so the editor could
 * only fail there. Guests have no private cover.
 */
export function canEditBookCover(me: Me | undefined | null, inLibrary: boolean): boolean {
  if (!me || me.role?.anonymous) return false;
  return inLibrary || !!(me.role?.edit || me.role?.admin || me.role?.browse_global);
}

/**
 * May this user add books to this shelf, or take them off it?
 *
 * The server's one rule (`cps/shelf.py::check_shelf_edit_permissions`, used by
 * the classic routes and `/api/v1/shelves/*` alike): a private shelf by its
 * owner only, a public shelf only with the "Edit public shelves" role. Owning a
 * public shelf is not enough: an admin can take the role away after the shelf
 * was made public, and the server then refuses its owner too.
 */
export function canEditShelf(
  me: Me | undefined | null,
  shelf: Pick<Shelf, 'is_public' | 'is_owner'>,
): boolean {
  return shelf.is_public ? !!me?.role?.edit_shelfs : shelf.is_owner;
}
