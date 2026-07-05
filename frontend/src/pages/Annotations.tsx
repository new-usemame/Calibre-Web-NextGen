import { Link } from 'wouter';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, Download, Upload as UploadIcon, Highlighter } from 'lucide-react';
import { apiGet, apiUrl } from '../lib/api';
import { useBook } from '../lib/queries';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import styles from './Annotations.module.css';

interface Annotation {
  annotation_id: string;
  highlighted_text: string;
  highlight_color: string | null;
  note_text: string | null;
  chapter_progress: number | null;
  source: string | null;
}

const COLOR_HEX: Record<string, string> = {
  yellow: '#e6c34a', red: '#d9534f', green: '#5cb85c', blue: '#5b9bd5',
};

/** Native per-book highlights view: every annotation the user has for the book,
 *  with export (MD/CSV/JSON) and Kobo import. Consumes /annotations/<id>/data.json.
 *  (In-reader CFI highlight creation lives in the epub.js reader — phase 2.) */
export function Annotations({ id }: { id: string }) {
  const t = useT();
  const book = useBook(id).data;
  // SC 1.4.1: name the highlight color; the colored bar alone is not enough.
  const colorName = (c: string | null) =>
    ({ yellow: t('Yellow'), red: t('Red'), green: t('Green'), blue: t('Blue') })[c || 'yellow'] || t('Yellow');
  const { data, isLoading, error } = useQuery<{ annotations: Annotation[] }>({
    queryKey: ['annotations', id],
    queryFn: () => apiGet<{ annotations: Annotation[] }>(`/annotations/${id}/data.json`),
  });

  if (isLoading) return <SpinnerCentered size={40} />;

  const annotations = data?.annotations ?? [];

  return (
    <main className={styles.container}>
      <Link href={`/book/${id}`} className={styles.back}>
        <ChevronLeft size={16} aria-hidden="true" focusable={false} /> {t('Back to book')}
      </Link>

      <div className={styles.header}>
        <Highlighter size={22} className={styles.headerIcon} aria-hidden="true" focusable={false} />
        <h1 className={styles.title}>{t('Highlights')}{book ? ` — ${book.title}` : ''}</h1>
        <span className={styles.count}>{annotations.length}</span>
      </div>

      <div className={styles.toolbar}>
        <a className={styles.toolBtn} href={apiUrl(`/annotations/${id}/export.md`)}><Download size={14} aria-hidden="true" focusable={false} /> Markdown</a>
        <a className={styles.toolBtn} href={apiUrl(`/annotations/${id}/export.csv`)}><Download size={14} aria-hidden="true" focusable={false} /> CSV</a>
        <a className={styles.toolBtn} href={apiUrl(`/annotations/${id}/export.json`)}><Download size={14} aria-hidden="true" focusable={false} /> JSON</a>
        <a className={styles.toolBtn} href={apiUrl('/annotations/import')}><UploadIcon size={14} aria-hidden="true" focusable={false} /> {t('Import from Kobo')}</a>
      </div>

      {error ? (
        <EmptyState message={error instanceof Error ? error.message : t('Could not load highlights.')} />
      ) : annotations.length === 0 ? (
        <EmptyState message={t('No highlights yet. Highlight while reading, or import from a Kobo device.')} />
      ) : (
        <ul className={styles.list}>
          {annotations.map((a) => (
            <li key={a.annotation_id} className={styles.item}>
              <span className={styles.bar} role="img" aria-label={colorName(a.highlight_color)}
                style={{ background: COLOR_HEX[a.highlight_color || 'yellow'] || '#e6c34a' }} />
              <div className={styles.body}>
                <blockquote className={styles.quote}>{a.highlighted_text}</blockquote>
                {a.note_text && <p className={styles.note}>{a.note_text}</p>}
                {a.chapter_progress != null && (
                  <span className={styles.progress}>{Math.round(a.chapter_progress * 100)}%</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
