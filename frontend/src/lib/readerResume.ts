/** Portable positions use 0–100 on the wire, epub.js uses a 0–1 fraction. */
export interface ReaderBookmark {
  bookmark: string | null;
  resume?: { percentage: number; synced_at: string; mode: 'automatic' | 'offer' } | null;
}

export function resumeCfi(locations: { cfiFromPercentage: (fraction: number) => string },
  resume: ReaderBookmark['resume']): string | undefined {
  const percentage = resume?.percentage;
  if (typeof percentage !== 'number' || !Number.isFinite(percentage)
    || percentage < 0 || percentage > 100) return undefined;
  return locations.cfiFromPercentage(percentage / 100) || undefined;
}
