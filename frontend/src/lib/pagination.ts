/** Return the greatest valid offset for a bounded offset-based collection. */
export function lastValidOffset(total: number, pageSize: number): number {
  if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(pageSize) || pageSize <= 0) {
    return 0;
  }
  return Math.floor((total - 1) / pageSize) * pageSize;
}

/** Clamp an offset after a mutation or filter makes the current page disappear. */
export function clampOffset(offset: number, total: number, pageSize: number): number {
  if (!Number.isFinite(offset) || offset <= 0) return 0;
  return Math.min(Math.floor(offset / pageSize) * pageSize, lastValidOffset(total, pageSize));
}

/** Clamp a one-based page after its true page count shrinks. */
export function clampPage(page: number, totalPages: number): number {
  const lastPage = Math.max(1, Math.floor(totalPages));
  if (!Number.isFinite(page) || page <= 1) return 1;
  return Math.min(Math.floor(page), lastPage);
}
