import { Link } from 'wouter';
import { Files, RefreshCw, X } from 'lucide-react';
import { useDuplicates, useDismissDuplicate, useTriggerDuplicateScan } from '../lib/queries';
import { resourceUrl } from '../lib/api';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import styles from './Duplicates.module.css';

/** Native duplicate-books view: groups of likely-duplicate titles with each
 *  member's formats, dismiss-per-group. Replaces the legacy /duplicates page. */
export function Duplicates() {
  const t = useT();
  const { data, isLoading, error } = useDuplicates();
  const dismiss = useDismissDuplicate();
  const scan = useTriggerDuplicateScan();

  if (isLoading) return <SpinnerCentered size={40} />;
  if (error || !data) {
    return (
      <main className={styles.container}>
        <EmptyState message={error instanceof Error ? error.message : t('Could not load duplicates.')} />
      </main>
    );
  }

  return (
    <main className={styles.container}>
      <div className={styles.header}>
        <Files size={22} className={styles.headerIcon} />
        <h1 className={styles.title}>{t('Duplicate books')}</h1>
        <span className={styles.count}>{data.items.length}</span>
        {/* #1048 — the SPA had no way to run a scan; the classic button lives on
            the legacy /duplicates page this route shadows. */}
        <button type="button" className={styles.scanBtn}
          onClick={() => scan.mutate()}
          disabled={scan.isPending}>
          <RefreshCw size={15} className={scan.isPending ? styles.spinning : undefined}
            aria-hidden="true" focusable={false} />
          <span>{scan.isPending ? t('Starting scan…') : t('Scan for duplicates')}</span>
        </button>
      </div>

      <p className={styles.status} role="status" aria-live="polite">
        {scan.isError
          ? t('Could not start the duplicate scan.')
          : scan.isSuccess
            ? scan.data?.already_running
              ? t('A duplicate scan is already running. This list updates when it finishes.')
              : t('Duplicate scan started. It runs in the background — this list updates when it finishes.')
            : ''}
      </p>

      {data.needs_scan ? (
        <EmptyState message={t('A one-time full duplicate scan is needed. Use “Scan for duplicates” above to run it.')} />
      ) : data.items.length === 0 ? (
        <EmptyState message={t('No duplicate groups found.')} />
      ) : (
        <div className={styles.groups}>
          {data.items.map((g) => (
            <section key={g.group_hash} className={styles.group} aria-label={`${g.title} — ${g.author}`}>
              <div className={styles.groupHead}>
                <div>
                  <span className={styles.groupTitle}>{g.title}</span>
                  <span className={styles.groupAuthor}>{g.author}</span>
                </div>
                <span className={styles.groupCount}>{g.count} {t('copies')}</span>
                <button className={styles.dismissBtn}
                  onClick={() => dismiss.mutate(g.group_hash)}
                  disabled={dismiss.isPending}
                  title={t('Dismiss this group')} aria-label={t('Dismiss this group')}>
                  <X size={16} />
                </button>
              </div>
              <ul className={styles.books}>
                {g.books.map((b) => (
                  <li key={b.id} className={styles.book}>
                    {b.cover_url
                      ? <img src={resourceUrl(b.cover_url)} alt="" className={styles.cover} loading="lazy" />
                      : <div className={styles.coverEmpty} />}
                    <div className={styles.bookInfo}>
                      <Link href={`/book/${b.id}`} className={styles.bookTitle}>{b.title}</Link>
                      <span className={styles.bookAuthors}>{b.authors}</span>
                      <span className={styles.bookFormats}>{b.formats.join(', ')}</span>
                    </div>
                    <Link href={`/book/${b.id}/edit`} className={styles.bookEdit}>{t('Edit')}</Link>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </main>
  );
}
