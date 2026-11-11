import { useState } from 'react';
import { Link } from 'wouter';
import { Files, Merge, RefreshCw, X } from 'lucide-react';
import {
  useDuplicates, useDismissDuplicate, useMergeBooks, useTriggerDuplicateScan,
  type DuplicateGroup,
} from '../lib/queries';
import { resourceUrl } from '../lib/api';
import { mergeRequestIds } from '../lib/duplicateMerge';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import styles from './Duplicates.module.css';

/** Native duplicate-books view: groups of likely-duplicate titles with each
 *  member's formats, merge and dismiss per group. Replaces the legacy
 *  /duplicates page. */
export function Duplicates() {
  const t = useT();
  const { data, isLoading, error } = useDuplicates();
  const dismiss = useDismissDuplicate();
  const scan = useTriggerDuplicateScan();
  // Page-level, because a successful merge usually removes the group (and the
  // card that ran it) on the next fetch.
  const [mergeMessage, setMergeMessage] = useState('');

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
          <span className={scan.isPending ? styles.spinning : undefined}>
            <RefreshCw size={15} aria-hidden="true" focusable={false} />
          </span>
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
      <p className={styles.status} role="status" aria-live="polite">{mergeMessage}</p>

      {data.needs_scan ? (
        <EmptyState message={t('A one-time full duplicate scan is needed. Use “Scan for duplicates” above to run it.')} />
      ) : data.items.length === 0 ? (
        <EmptyState message={t('No duplicate groups found.')} />
      ) : (
        <div className={styles.groups}>
          {data.items.map((g) => (
            <DuplicateGroupCard key={g.group_hash} group={g}
              onDismiss={() => dismiss.mutate(g.group_hash)}
              dismissing={dismiss.isPending}
              onMergeResult={setMergeMessage} />
          ))}
        </div>
      )}
    </main>
  );
}

/** One duplicate group. #2747: the legacy page could merge selected copies and
 *  the new one could only dismiss. The reader picks the copy to keep (its
 *  metadata survives) and which other copies to fold into it; /ajax/mergebooks
 *  copies over the formats the kept copy lacks and then deletes the others. */
function DuplicateGroupCard({ group: g, onDismiss, dismissing, onMergeResult }: {
  group: DuplicateGroup;
  onDismiss: () => void;
  dismissing: boolean;
  onMergeResult: (message: string) => void;
}) {
  const t = useT();
  const merge = useMergeBooks();
  const groupIds = g.books.map((b) => b.id);
  const [chosenKeepId, setKeepId] = useState(groupIds[0]);
  // Unticked copies are remembered, so a copy that joins the group on a refetch
  // starts ticked like the rest; a kept copy that left falls back to the first.
  const [unticked, setUnticked] = useState<number[]>([]);
  const keepId = groupIds.includes(chosenKeepId) ? chosenKeepId : groupIds[0];
  const ticked = groupIds.filter((id) => !unticked.includes(id));
  const keepTitle = g.books.find((b) => b.id === keepId)?.title ?? g.title;
  const ids = mergeRequestIds(groupIds, keepId, ticked);

  const toggle = (id: number) =>
    setUnticked((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  const onMerge = () => {
    if (!ids) return;
    const count = ids.length - 1;
    if (!window.confirm(count === 1
      ? t('Merge one copy into “{title}”? Formats it lacks are copied over, then that copy is deleted.', { title: keepTitle })
      : t('Merge {n} copies into “{title}”? Formats it lacks are copied over, then those copies are deleted.',
        { n: count, title: keepTitle }))) return;
    onMergeResult('');
    merge.mutate(ids, {
      // The route answers an empty 200 when it found nothing to merge, so only
      // an explicit success counts.
      onSuccess: (res) => onMergeResult((res as { success?: boolean } | undefined)?.success
        ? t('Merged into “{title}”.', { title: keepTitle })
        : t('Could not merge these books.')),
      onError: () => onMergeResult(t('Could not merge these books.')),
    });
  };

  return (
    <section className={styles.group} aria-label={`${g.title} — ${g.author}`}>
      <div className={styles.groupHead}>
        <div className={styles.groupName}>
          <span className={styles.groupTitle}>{g.title}</span>
          <span className={styles.groupAuthor}>{g.author}</span>
        </div>
        <span className={styles.groupCount}>{g.count} {t('copies')}</span>
        <button type="button" className={styles.mergeBtn}
          onClick={onMerge}
          disabled={!ids || merge.isPending}>
          <Merge size={15} aria-hidden="true" focusable={false} />
          <span>{t('Merge into the kept copy')}</span>
        </button>
        <button className={styles.dismissBtn}
          onClick={onDismiss}
          disabled={dismissing}
          title={t('Dismiss this group')} aria-label={t('Dismiss this group')}>
          <X size={16} />
        </button>
      </div>
      <ul className={styles.books}>
        {g.books.map((b) => {
          const kept = b.id === keepId;
          return (
            <li key={b.id} className={kept ? `${styles.book} ${styles.bookKept}` : styles.book}>
              {b.cover_url
                ? <img src={resourceUrl(b.cover_url)} alt="" className={styles.cover} loading="lazy" />
                : <div className={styles.coverEmpty} />}
              <div className={styles.bookInfo}>
                <Link href={`/book/${b.id}`} className={styles.bookTitle}>{b.title}</Link>
                <span className={styles.bookAuthors}>{b.authors}</span>
                <span className={styles.bookFormats}>{b.formats.join(', ')}</span>
                <div className={styles.bookChoices}>
                  <label className={styles.choice}>
                    <input type="radio" name={`keep-${g.group_hash}`} checked={kept}
                      onChange={() => setKeepId(b.id)} />
                    {t('Keep this copy')}
                  </label>
                  {!kept && (
                    <label className={styles.choice}>
                      <input type="checkbox" checked={ticked.includes(b.id)}
                        onChange={() => toggle(b.id)} />
                      {t('Merge this copy')}
                    </label>
                  )}
                </div>
              </div>
              <Link href={`/book/${b.id}/edit`} className={styles.bookEdit}>{t('Edit')}</Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
