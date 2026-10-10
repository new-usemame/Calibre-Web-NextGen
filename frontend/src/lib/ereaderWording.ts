/* Which e-readers a shelf mark reaches, and how to name them.
 *
 * A shelf marked for e-reader sync (the `kobo_sync` flag) and the account's
 * "only selected shelves" choice decide what Kobo sync delivers and what the
 * KOReader library lists: one rule, cps/services/ereader_scope.py. The mark
 * matters once either sync is on (`shelf_marks_enabled` on the server).
 *
 * The wording stays "Kobo" while only Kobo sync is on, which keeps the
 * long-standing labels and their translations for that case, and becomes
 * "e-reader" once the KOReader library can receive shelves too.
 */

export interface SyncFeatures {
  kobo_sync?: boolean;
  koreader_sync?: boolean;
}

/** A shelf mark can reach a device on this server. */
export function shelfMarksReachDevices(features?: SyncFeatures | null): boolean {
  return Boolean(features?.kobo_sync || features?.koreader_sync);
}

/** Whom a shelf mark reaches: only Kobos, or e-readers in general. */
export function shelfMarkAudience(features?: SyncFeatures | null): 'kobo' | 'ereader' {
  return features?.koreader_sync ? 'ereader' : 'kobo';
}
