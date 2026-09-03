import type { TFunction } from './i18n';
import type { BulkFailureDetail } from './bulkResults';

/** Exact backend policy signal from user_library.remove_book. Match both code
 * and message so a future policy sharing the code is never mislabelled. */
export const LAST_BOOK_REMOVAL_SERVER_MESSAGE =
  'The last book cannot be removed unless this user can browse the global library.';

export function bulkRemovalFailureReason(
  detail: BulkFailureDetail,
  t: TFunction,
): string {
  if (detail.code === 'library_membership_rejected'
      && detail.message === LAST_BOOK_REMOVAL_SERVER_MESSAGE) {
    return t('The last book cannot be removed unless you can browse the global library.');
  }
  // Unknown server policies must stay visible and verbatim: translating or
  // paraphrasing an unrecognised reason would invent meaning.
  return detail.message;
}
