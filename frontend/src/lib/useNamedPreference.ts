import { useCallback, useEffect, useRef } from 'react';
import { useMe, useUpdateNamedPreferences } from './queries';
import { usePersistentBool } from './usePersistentBool';
import {
  queueNamedPreferenceAdoption,
  resolveNamedPreferenceState,
} from './namedPreferenceState';

function readStoredBool(key: string): boolean | null {
  try {
    const value = localStorage.getItem(key);
    return value === null ? null : value === '1';
  } catch {
    return null;
  }
}

export interface NamedPreferenceOptions {
  onError?: () => void;
}

/** A boolean preference that follows authenticated accounts while retaining a
 * localStorage fallback for guests, offline state, and one-time adoption.
 *
 * /me is loaded before Catalog mounts, so an authoritative server value drives
 * the first render directly. If the server returns null, an explicit existing
 * local value drives that first render and is adopted once. Guests never call
 * the mutation endpoint.
 */
export function useNamedPreference(
  name: string,
  localStorageKey: string,
  fallback: boolean,
  options: NamedPreferenceOptions = {},
) {
  const { data: me } = useMe();
  const update = useUpdateNamedPreferences();
  const [localValue, setLocalValue] = usePersistentBool(localStorageKey, fallback);
  const storedAtMount = useRef<boolean | null>(readStoredBool(localStorageKey));
  const adoptionAttempted = useRef(false);

  const state = resolveNamedPreferenceState(
    me, name, localValue, storedAtMount.current,
  );
  const { serverValue, value } = state;

  // Keep the fallback current for offline use without letting it override the
  // server on an authenticated render.
  useEffect(() => {
    if (typeof serverValue === 'boolean') setLocalValue(serverValue);
  }, [serverValue, setLocalValue]);

  // One-time adoption. An absent local key is not a preference and therefore
  // does not create a pointless write of the fallback default.
  useEffect(() => {
    const valueToAdopt = storedAtMount.current;
    if (!state.shouldAdopt || valueToAdopt === null
        || adoptionAttempted.current) return;
    adoptionAttempted.current = true;
    queueNamedPreferenceAdoption(
      me!, name, valueToAdopt,
      (preferences, mutationOptions) => update.mutate(preferences, {
        onError: mutationOptions.onError,
      }),
      options.onError,
    );
  }, [me, name, options, state.shouldAdopt, update]);

  const setValue = useCallback((next: boolean) => {
    const previous = value;
    setLocalValue(next);
    if (!state.canPersist) return;
    update.mutate(
      { [name]: next },
      {
        onError: () => {
          setLocalValue(previous);
          options.onError?.();
        },
      },
    );
  }, [name, options, setLocalValue, state.canPersist, update, value]);

  return [value, setValue, update.isPending] as const;
}
