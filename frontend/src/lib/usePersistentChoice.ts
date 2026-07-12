import { useCallback, useState } from 'react';

/** Small local preference hook for view-only choices. The key is the single
 * persistence source; invalid/old values safely fall back to the supplied default. */
export function usePersistentChoice<T extends string>(key: string, allowed: readonly T[], fallback: T) {
  const [value, setValueState] = useState<T>(() => {
    try {
      const stored = localStorage.getItem(key) as T | null;
      return stored && allowed.includes(stored) ? stored : fallback;
    } catch { return fallback; }
  });
  const setValue = useCallback((next: T) => {
    setValueState(next);
    try { localStorage.setItem(key, next); } catch { /* storage can be disabled */ }
  }, [key]);
  return [value, setValue] as const;
}
