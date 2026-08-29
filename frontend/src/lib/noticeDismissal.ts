export const NOTICE_DISMISS_BATCH_SIZE = 500;

export interface NoticeDismissResult {
  dismissed: number;
  remaining: number;
}

/**
 * Dismiss an arbitrarily large notice selection without exceeding the API's
 * deliberately bounded bulk-request contract. Batches run sequentially so a
 * failure stops at the first unconfirmed batch; the endpoint is idempotent, so
 * retrying the original selection is safe even when earlier batches succeeded.
 */
export async function dismissNoticeIdsInBatches(
  noticeIds: number[],
  dismissBatch: (noticeIds: number[]) => Promise<NoticeDismissResult>,
): Promise<NoticeDismissResult> {
  let dismissed = 0;
  let remaining = 0;

  for (let start = 0; start < noticeIds.length; start += NOTICE_DISMISS_BATCH_SIZE) {
    const result = await dismissBatch(noticeIds.slice(start, start + NOTICE_DISMISS_BATCH_SIZE));
    dismissed += result.dismissed;
    remaining = result.remaining;
  }

  return { dismissed, remaining };
}
