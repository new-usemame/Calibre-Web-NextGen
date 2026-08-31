export interface NamedPreferenceMe {
  role?: { anonymous?: boolean };
  preferences?: Record<string, boolean | null>;
}

type AdoptionMutation = (
  preferences: Record<string, boolean>,
  options: { onError: () => void },
) => void;

interface AdoptionBatch {
  preferences: Record<string, boolean>;
  mutate: AdoptionMutation;
  errorHandlers: Array<() => void>;
}

const pendingAdoptions = new WeakMap<object, AdoptionBatch>();

/** Coalesce sibling hooks' one-time adoptions into one transaction.
 *
 * React runs all passive effects from a commit before this microtask. Using the
 * shared /me object as the key lets every named preference on that account join
 * one map, avoiding both dropped scoped mutations and lost JSON updates.
 */
export function queueNamedPreferenceAdoption(
  account: object,
  name: string,
  value: boolean,
  mutate: AdoptionMutation,
  onError?: () => void,
) {
  const existing = pendingAdoptions.get(account);
  if (existing) {
    existing.preferences[name] = value;
    if (onError) existing.errorHandlers.push(onError);
    return;
  }

  const batch: AdoptionBatch = {
    preferences: { [name]: value },
    mutate,
    errorHandlers: onError ? [onError] : [],
  };
  pendingAdoptions.set(account, batch);
  queueMicrotask(() => {
    pendingAdoptions.delete(account);
    batch.mutate(batch.preferences, {
      onError: () => {
        for (const handler of batch.errorHandlers) handler();
      },
    });
  });
}

/** Pure decision core for useNamedPreference, kept separate so the first-render,
 * adoption, and guest/offline contracts have dependency-free unit coverage. */
export function resolveNamedPreferenceState(
  me: NamedPreferenceMe | null | undefined,
  name: string,
  localValue: boolean,
  storedValue: boolean | null,
) {
  const isGuest = !!me?.role?.anonymous;
  const hasServerSlot = !!me?.preferences
    && Object.prototype.hasOwnProperty.call(me.preferences, name);
  const serverValue = hasServerSlot ? me?.preferences?.[name] : undefined;
  const canPersist = !!me && !isGuest && hasServerSlot;

  return {
    isGuest,
    hasServerSlot,
    serverValue,
    value: (!isGuest && typeof serverValue === 'boolean')
      ? serverValue
      : localValue,
    canPersist,
    shouldAdopt: canPersist && serverValue === null && storedValue !== null,
  };
}
