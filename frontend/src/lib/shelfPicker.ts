/** Pure helpers for the shelf page's bulk "Add books" picker. */

export interface PickerBook {
  id: number;
  title: string;
  authors: string[];
  cover_url: string | null;
  in_shelf: boolean;
}

/** Toggle a book in the selection. Books already on the shelf are never selectable. */
export function togglePick(selected: ReadonlySet<number>, book: Pick<PickerBook, 'id' | 'in_shelf'>): Set<number> {
  if (book.in_shelf) return new Set(selected);
  const next = new Set(selected);
  if (next.has(book.id)) next.delete(book.id);
  else next.add(book.id);
  return next;
}

/** Append a freshly loaded page, dropping rows the picker already holds. */
export function mergePickerPages(pages: readonly (readonly PickerBook[])[]): PickerBook[] {
  const seen = new Set<number>();
  const merged: PickerBook[] = [];
  for (const page of pages) {
    for (const book of page) {
      if (seen.has(book.id)) continue;
      seen.add(book.id);
      merged.push(book);
    }
  }
  return merged;
}

/** After an add, keep only the books that failed so the user can retry them. */
export function retainFailed(selected: ReadonlySet<number>, failedIds: readonly number[]): Set<number> {
  const failed = new Set(failedIds);
  return new Set([...selected].filter((id) => failed.has(id)));
}
