/**
 * One click in a multi-select grid. A plain click toggles the clicked item; a
 * Shift+click applies the clicked item's new state to every item between it and
 * the anchor (the previous click), as the classic book table and mail lists do.
 * Without a usable anchor — first click, or the anchor left the list — a
 * Shift+click is a plain click. The clicked item always becomes the new anchor.
 */
export function applySelectionClick<Id>(
  selected: ReadonlySet<Id>,
  orderedIds: readonly Id[],
  id: Id,
  anchor: Id | null,
  extend: boolean,
): { selected: Set<Id>; anchor: Id } {
  const next = new Set(selected);
  const select = !selected.has(id);
  const from = extend && anchor !== null ? orderedIds.indexOf(anchor) : -1;
  const to = from === -1 ? -1 : orderedIds.indexOf(id);
  const range = to === -1 ? [id] : orderedIds.slice(Math.min(from, to), Math.max(from, to) + 1);
  for (const item of range) {
    if (select) next.add(item);
    else next.delete(item);
  }
  return { selected: next, anchor: id };
}
