export interface BulkActionResult {
  succeededIds: number[];
  failedIds: number[];
}

/** Settle every per-book request while retaining which id produced each result. */
export async function settleById(
  ids: number[],
  run: (id: number) => Promise<unknown>,
): Promise<BulkActionResult> {
  const results = await Promise.allSettled(ids.map(run));
  return results.reduce<BulkActionResult>((accounting, result, index) => {
    const key = result.status === 'fulfilled' ? 'succeededIds' : 'failedIds';
    accounting[key].push(ids[index]);
    return accounting;
  }, { succeededIds: [], failedIds: [] });
}
