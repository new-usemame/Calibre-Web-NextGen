/** Portable positions use 0–100 on the wire, epub.js uses a 0–1 fraction. */
export interface ReaderBookmark {
  bookmark: string | null;
  resume?: { percentage: number; synced_at: string; mode: 'automatic' | 'offer'; cfi?: string; epub_sha256?: string } | null;
}

export function resumeCfi(locations: { cfiFromPercentage: (fraction: number) => string },
  resume: ReaderBookmark['resume']): string | undefined {
  if (resume?.cfi) return resume.cfi;
  const percentage = resume?.percentage;
  if (typeof percentage !== 'number' || !Number.isFinite(percentage)
    || percentage < 0 || percentage > 100) return undefined;
  return locations.cfiFromPercentage(percentage / 100) || undefined;
}

/** Exact resume requires the same archive and a CFI that resolves in the reader. */
export async function resumeForArchive(resume: ReaderBookmark['resume'], archive: ArrayBuffer,
  resolveRange?: (cfi: string) => Promise<Range | null | undefined>): Promise<ReaderBookmark['resume']> {
  if (!resume?.cfi) return resume;
  try {
    const digest = await crypto.subtle.digest('SHA-256', archive);
    const fingerprint = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
    if (fingerprint === resume.epub_sha256
      && (!resolveRange || await resolveRange(resume.cfi))) return resume;
  } catch { /* An unavailable digest or unresolvable CFI must retain percentage resume. */ }
  return { ...resume, cfi: undefined };
}
