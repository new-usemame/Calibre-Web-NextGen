import { useSyncExternalStore } from 'react';

// A selection change changes every catalog page, including pages kept only in
// the scroll snapshot. Give those views one shared revision so old responses,
// restored pages and mounted accumulators cannot cross the selection boundary.
let revision = 0;
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};
const getSnapshot = () => revision;

export function useLibraryRevision(): number {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export function advanceLibraryRevision(): void {
  revision += 1;
  listeners.forEach((listener) => listener());
}
