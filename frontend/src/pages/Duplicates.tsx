import { Link } from 'wouter';
import { Files, X } from 'lucide-react';
import { useDuplicates, useDismissDuplicate } from '../lib/queries';
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
      </div>

      {data.needs_scan ? (
        <EmptyState message={t('A one-time full duplicate scan is needed. Run it from CWA settings, then return here.')} />
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
