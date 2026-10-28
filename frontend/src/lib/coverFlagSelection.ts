/** Selection rules for the "Restore missing covers" list (#2678).
 *
 * Pure so they can be tested without a DOM. The list can hold hundreds of
 * books, so "select all" must be one action and must never leave the page's
 * state out of step with the checkboxes. */

export type SelectAllState = 'none' | 'some' | 'all';

export function selectAllState(selected: ReadonlySet<number>, ids: readonly number[]): SelectAllState {
  if (ids.length === 0) return 'none';
  let count = 0;
  for (const id of ids) if (selected.has(id)) count += 1;
  if (count === 0) return 'none';
  return count === ids.length ? 'all' : 'some';
}

export function toggleOne(selected: ReadonlySet<number>, id: number): Set<number> {
  const next = new Set(selected);
  if (next.has(id)) next.delete(id); else next.add(id);
  return next;
}

/** Everything when anything is unselected; nothing when everything already is. */
export function toggleAll(selected: ReadonlySet<number>, ids: readonly number[]): Set<number> {
  return selectAllState(selected, ids) === 'all' ? new Set() : new Set(ids);
}
