/** Build the id list for /ajax/mergebooks from one duplicate group (#2747).
 *
 *  The route keeps the FIRST id and deletes every other one after copying over
 *  the formats the kept book lacks, so the order here decides which book
 *  survives. Returns null when there is nothing to merge, or when the kept id
 *  is not in the group (a stale selection must never reach the server, where
 *  it would delete books the reader can no longer see on the page). */
export function mergeRequestIds(
  groupIds: readonly number[],
  keepId: number,
  tickedIds: readonly number[],
): number[] | null {
  if (!groupIds.includes(keepId)) return null;
  const ticked = new Set(tickedIds);
  const others = groupIds.filter((id) => id !== keepId && ticked.has(id));
  return others.length ? [keepId, ...others] : null;
}
