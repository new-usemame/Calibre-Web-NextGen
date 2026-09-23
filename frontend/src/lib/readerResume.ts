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

interface SpineSection {
  index: number;
  href?: string;
}

interface ChapterProgressBook {
  spine: {
    spineItems?: SpineSection[];
    get: (target: string | number) => SpineSection | null | undefined;
  };
  locations: {
    save: () => string;
    cfiFromLocation: (location: number) => string | number;
  };
}

/**
 * Translate a same-archive Readium chapter progression into an epub.js CFI.
 *
 * Readium can report navigator-private fragment names that do not exist in the
 * EPUB. Its chapter href + chapter-local progression is still a stronger
 * approximation than applying total progression to another renderer's global
 * location index. The result remains approximate in the UI.
 */
export function chapterProgressCfi(
  book: ChapterProgressBook, href: string, progression: number,
): string | undefined {
  if (!Number.isFinite(progression) || progression < 0 || progression > 1) return undefined;
  const target = decodeURI(href).replace(/^\/+/, '').split('#', 1)[0];
  const normalize = (value: string) => decodeURI(value).replace(/^\/+/, '');
  const matches = (book.spine.spineItems ?? []).filter(section => {
    const candidate = normalize(section.href ?? '');
    return candidate === target
      || target.endsWith(`/${candidate}`)
      || candidate.endsWith(`/${target}`);
  });
  const section = matches.length === 1 ? matches[0] : book.spine.get(target);
  if (!section || typeof section.index !== 'number') return undefined;

  let locations: string[];
  try {
    const saved = JSON.parse(book.locations.save());
    if (!Array.isArray(saved) || !saved.every(row => typeof row === 'string')) return undefined;
    locations = saved;
  } catch {
    return undefined;
  }
  const inSection: number[] = [];
  locations.forEach((cfi, index) => {
    if (book.spine.get(cfi)?.index === section.index) inSection.push(index);
  });
  if (inSection.length === 0) return undefined;
  const offset = Math.round((inSection.length - 1) * progression);
  const cfi = book.locations.cfiFromLocation(inSection[offset]);
  return typeof cfi === 'string' && cfi ? cfi : undefined;
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

/** Confirm that an external exact locator belongs to the EPUB being rendered. */
export async function archiveMatchesFingerprint(
  archive: ArrayBuffer, expected: string | undefined,
): Promise<boolean> {
  if (!expected) return false;
  try {
    return await withResumeTimeout(async signal => {
      let digest: ArrayBuffer;
      try {
        digest = await globalThis.crypto.subtle.digest('SHA-256', archive);
      } catch {
        digest = await sha256Fallback(archive, signal);
      }
      signal.throwIfAborted();
      const fingerprint = Array.from(
        new Uint8Array(digest), b => b.toString(16).padStart(2, '0'),
      ).join('');
      return fingerprint === expected;
    });
  } catch {
    return false;
  }
}

/** Exact resume requires the same archive and a CFI that resolves in the reader. */
export async function resumeForArchive(resume: ReaderBookmark['resume'], archive: ArrayBuffer,
  resolveRange?: (cfi: string) => Promise<Range | null | undefined>): Promise<ReaderBookmark['resume']> {
  if (!resume?.cfi) return resume;
  try {
    const cfi = resume.cfi;
    const exact = await archiveMatchesFingerprint(archive, resume.epub_sha256)
      && await withResumeTimeout(async () => !resolveRange || !!await resolveRange(cfi));
    if (exact) return resume;
  } catch { /* Failed or stalled validation must retain percentage resume. */ }
  return { ...resume, cfi: undefined };
}
