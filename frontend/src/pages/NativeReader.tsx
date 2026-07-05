import { useState, useEffect, useCallback } from 'react';
import { Link } from 'wouter';
import { X, ChevronLeft, ChevronRight } from 'lucide-react';
import { apiGet, apiUrl } from '../lib/api';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { VisuallyHidden } from '../components/VisuallyHidden';
import { useT } from '../lib/i18n';
import styles from './NativeReader.module.css';

const AUDIO = new Set(['mp3', 'm4a', 'm4b', 'flac', 'ogg', 'opus', 'wav', 'aac']);
const COMIC = new Set(['cbz', 'cbr', 'cbt']);

/** Native in-browser reader for non-EPUB formats. PDF renders in the browser's
 *  built-in viewer (iframe), audiobooks in an <audio> player, plain text inline
 *  — all dependency-free. EPUB/KEPUB use the dedicated epub.js reader; comics
 *  and DjVu fall back to the server reader (image extraction needs server help). */
export function NativeReader({ id, format }: { id: string; format: string }) {
  const t = useT();
  const fmt = format.toLowerCase();
  const src = apiUrl(`/show/${id}/${fmt}`);
  const [text, setText] = useState<string | null>(null);
  const [textErr, setTextErr] = useState(false);

  useEffect(() => {
    if (fmt !== 'txt') return;
    let alive = true;
    fetch(src, { credentials: 'include' })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((tx) => { if (alive) setText(tx); })
      .catch(() => { if (alive) setTextErr(true); });
    return () => { alive = false; };
  }, [src, fmt]);

  return (
    <div className={styles.shell}>
      <VisuallyHidden as="h1">{t('{format} reader', { format: fmt.toUpperCase() })}</VisuallyHidden>
      <div className={styles.bar}>
        <Link href={`/book/${id}`} className={styles.close} title={t('Close reader')} aria-label={t('Close reader')}>
          <X size={18} aria-hidden="true" focusable={false} /> {t('Close')}
        </Link>
        <span className={styles.fmt}>{fmt.toUpperCase()}</span>
      </div>

      <div className={styles.body}>
        {fmt === 'pdf' && (
          <iframe className={styles.pdf} src={src} title={t('PDF reader')} />
        )}

        {AUDIO.has(fmt) && (
          <div className={styles.audioWrap}>
            <audio className={styles.audio} controls preload="metadata" src={src}>
              {t('Your browser cannot play this audio format.')}
            </audio>
          </div>
        )}

        {fmt === 'txt' && (
          textErr ? <EmptyState message={t('Could not load this text file.')} />
            : text === null ? <SpinnerCentered size={36} />
              : <pre className={styles.text}>{text}</pre>
        )}

        {COMIC.has(fmt) && <ComicViewer id={id} />}

        {!['pdf', 'txt'].includes(fmt) && !AUDIO.has(fmt) && !COMIC.has(fmt) && (
          // djvu / other — server reader handles rendering
          <div className={styles.fallback}>
            <p>{t('This format opens in the full-screen reader.')}</p>
            <a className={styles.fallbackBtn} href={apiUrl(`/read/${id}/${fmt}`)}>{t('Open reader')}</a>
          </div>
        )}
      </div>
    </div>
  );
}

/** Native comic viewer: server extracts pages; we show one <img> at a time with
 *  prev/next + arrow-key nav. No client archive lib needed. */
function ComicViewer({ id }: { id: string }) {
  const t = useT();
  const [pages, setPages] = useState<number | null>(null);
  const [page, setPage] = useState(0);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let alive = true;
    apiGet<{ pages: number }>(`/api/v1/books/${id}/comic`)
      .then((d) => { if (alive) setPages(d.pages); })
      .catch(() => { if (alive) setErr(true); });
    return () => { alive = false; };
  }, [id]);

  const go = useCallback((d: number) => {
    setPage((p) => Math.min(Math.max(0, p + d), (pages ?? 1) - 1));
  }, [pages]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') go(1);
      if (e.key === 'ArrowLeft') go(-1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [go]);

  if (err) return <EmptyState message={t('Could not read this comic archive.')} />;
  if (pages === null) return <SpinnerCentered size={36} />;
  if (pages === 0) return <EmptyState message={t('No pages found in this comic.')} />;

  return (
    <div className={styles.comic}>
      <button className={styles.comicNav} onClick={() => go(-1)} disabled={page === 0}
        aria-label={t('Previous page')}><ChevronLeft size={28} /></button>
      <img className={styles.comicPage} src={apiUrl(`/api/v1/books/${id}/comic/${page}`)} alt={`Page ${page + 1}`} />
      <button className={styles.comicNav} onClick={() => go(1)} disabled={page >= pages - 1}
        aria-label={t('Next page')}><ChevronRight size={28} /></button>
      <div className={styles.comicPager}>{page + 1} / {pages}</div>
    </div>
  );
}
