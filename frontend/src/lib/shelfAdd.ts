import { settleById, type BulkActionResult } from './bulkResults.ts';

/** Shared accounting for sidebar drops and the existing bulk shelf action. */
export async function addShelfBooks(
  ids: number[], run: (id: number) => Promise<unknown>,
): Promise<BulkActionResult> {
  const unique = [...new Set(ids)];
  const result: BulkActionResult = {
    succeededIds: [], failedIds: [], warningIds: [], failureDetails: [], warnings: [], outcomes: [],
  };
  for (let offset = 0; offset < unique.length; offset += 4) {
    const batch = await settleById(unique.slice(offset, offset + 4), (id) => run(id).catch((error: unknown) => {
      const conflict = error as { status?: number; detail?: { code?: string } };
      if (conflict?.status === 409 && conflict.detail?.code === 'conflict') return null;
      throw error;
    }));
    result.succeededIds.push(...batch.succeededIds);
    result.failedIds.push(...batch.failedIds);
    result.failureDetails.push(...batch.failureDetails);
    result.outcomes.push(...batch.outcomes);
  }
  return result;
}

export function draggedBookIds(id: number, selected: readonly number[]): number[] {
  return selected.includes(id) ? [...new Set(selected)] : [id];
}
