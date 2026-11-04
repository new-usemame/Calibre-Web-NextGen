import { useSyncExternalStore } from 'react';
import {
  observeLibraryTotal,
  startImportWatch,
  type ImportWatch,
} from './uploadImportWatch';

// The one upload being followed into the library (#1044). It lives outside any
// page so following carries on when the reader leaves /upload for the library,
// and so the Upload page can show how far the import has got.
let watch: ImportWatch | null = null;
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};
const emit = () => listeners.forEach((listener) => listener());

export function getImportWatch(): ImportWatch | null {
  return watch;
}

export function useImportWatch(): ImportWatch | null {
  return useSyncExternalStore(subscribe, getImportWatch, getImportWatch);
}

/** Start (or extend) following `queued` files; `baseline` is the library total
 *  read before they were queued, or null if that read failed. */
export function beginImportWatch(queued: number, baseline: number | null, now = Date.now()): void {
  if (queued <= 0) return;
  watch = startImportWatch(watch, queued, baseline, now);
  emit();
}

/** Record a library total. Returns true when the library views should refetch. */
export function recordLibraryTotal(total: number, now = Date.now()): boolean {
  if (!watch || watch.status !== 'watching') return false;
  const result = observeLibraryTotal(watch, total, now);
  watch = result.watch;
  emit();
  return result.refresh;
}
