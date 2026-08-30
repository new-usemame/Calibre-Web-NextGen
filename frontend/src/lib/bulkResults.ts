export interface BulkActionResult {
  succeededIds: number[];
  failedIds: number[];
}

export interface BulkBatchResult extends BulkActionResult {
  /** Request-level failures (for example batch_too_large). Per-id policy
   *  refusals stay in failedIds because the batch endpoint reports them in a
   *  successful response. */
  errors: unknown[];
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

/** Run a bounded batch endpoint without losing per-id accounting.
 *
 * Every requested id is classified exactly once. A rejected request marks its
 * whole chunk failed while successful earlier/later chunks remain recorded;
 * an id omitted by a malformed/truncated response is failed, never silently
 * treated as successful. */
export async function settleByBatch(
  ids: number[],
  batchSize: number,
  run: (ids: number[]) => Promise<BulkActionResult>,
): Promise<BulkBatchResult> {
  if (!Number.isInteger(batchSize) || batchSize < 1) {
    throw new RangeError('batchSize must be a positive integer');
  }

  const chunks: number[][] = [];
  for (let start = 0; start < ids.length; start += batchSize) {
    chunks.push(ids.slice(start, start + batchSize));
  }
  const settled = await Promise.allSettled(chunks.map(run));

  return settled.reduce<BulkBatchResult>((accounting, result, index) => {
    const chunk = chunks[index];
    if (result.status === 'rejected') {
      accounting.failedIds.push(...chunk);
      accounting.errors.push(result.reason);
      return accounting;
    }

    const succeeded = new Set(result.value.succeededIds);
    chunk.forEach((id) => {
      (succeeded.has(id) ? accounting.succeededIds : accounting.failedIds).push(id);
    });
    return accounting;
  }, { succeededIds: [], failedIds: [], errors: [] });
}
