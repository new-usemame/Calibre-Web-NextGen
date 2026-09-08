import { sha256Fallback } from './sha256.ts';

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

// Optional resume work must not hold first display indefinitely. Half a second
// leaves room for local archive work on a busy device without a long blank page.
const RESUME_TIMEOUT_MS = 500;

export async function withResumeTimeout<T>(work: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error('Resume timed out')), RESUME_TIMEOUT_MS);
      }),
      work(controller.signal),
    ]);
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
}

/** Exact resume requires the same archive and a CFI that resolves in the reader. */
export async function resumeForArchive(resume: ReaderBookmark['resume'], archive: ArrayBuffer,
  resolveRange?: (cfi: string) => Promise<Range | null | undefined>): Promise<ReaderBookmark['resume']> {
  if (!resume?.cfi) return resume;
  try {
    const cfi = resume.cfi;
    const exact = await withResumeTimeout(async signal => {
      let digest: ArrayBuffer;
      try {
        digest = await globalThis.crypto.subtle.digest('SHA-256', archive);
      } catch {
        digest = await sha256Fallback(archive, signal);
      }
      signal.throwIfAborted();
      const fingerprint = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
      return fingerprint === resume.epub_sha256
        && (!resolveRange || !!await resolveRange(cfi));
    });
    if (exact) return resume;
  } catch { /* Failed or stalled validation must retain percentage resume. */ }
  return { ...resume, cfi: undefined };
}
