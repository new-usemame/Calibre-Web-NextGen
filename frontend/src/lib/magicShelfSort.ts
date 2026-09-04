export interface MagicShelfSortAdoption {
  value: string;
  persist: boolean;
}

export function canonicalMagicShelfSortAdoption(
  requestedSort: string,
  responseSort: string | undefined,
  isPlaceholderData: boolean,
  sortPersistable: boolean | undefined,
): MagicShelfSortAdoption | null {
  if (isPlaceholderData || !responseSort || responseSort === requestedSort) return null;
  return { value: responseSort, persist: sortPersistable !== false };
}

export function customMagicShelfSortOptions<T>(options: readonly T[] | undefined): readonly T[] {
  return options ?? [];
}
