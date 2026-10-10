import { useCallback, useEffect, useRef } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { applySelectionClick } from './rangeSelection';

/**
 * A stable click handler for a multi-select grid: `toggle(book, extend)` toggles
 * one book, or with `extend` (Shift held) the whole run from the last click.
 * `orderedIds` is the list in display order, including rows scrolled out of view;
 * leaving selection mode (`active` false) forgets the anchor.
 */
export function useRangeSelection(
  setSelected: Dispatch<SetStateAction<Set<number>>>,
  orderedIds: readonly number[],
  active: boolean,
) {
  const anchor = useRef<number | null>(null);
  useEffect(() => {
    if (!active) anchor.current = null;
  }, [active]);
  const ids = useRef(orderedIds);
  ids.current = orderedIds;
  return useCallback(({ id }: { id: number }, extend: boolean) => {
    // Read and move the anchor outside the updater so it stays pure (StrictMode
    // runs updaters twice).
    const from = anchor.current;
    anchor.current = id;
    setSelected((previous) => applySelectionClick(previous, ids.current, id, from, extend).selected);
  }, [setSelected]);
}
