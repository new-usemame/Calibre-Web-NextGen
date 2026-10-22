import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError, apiPost } from '../lib/api';
import { selectAllState, toggleAll, toggleOne } from '../lib/coverFlagSelection';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useT } from '../lib/i18n';
import { Button } from './Button';
import styles from './CoverFlagRepairAdmin.module.css';

interface ScanResponse { count: number; books: { id: number; title: string }[]; token: string | null }
interface RestoreResponse { fixed: number; skipped: number }

/** Restore covers whose cover.jpg is on disk but whose has_cover flag is 0 (#2678).
 *  Scan is read-only; nothing changes until the admin selects books and restores. */
export function CoverFlagRepairAdmin() {
  const t = useT();
  const announce = useAnnouncer();
  const qc = useQueryClient();
  const selectAllBox = useRef<HTMLInputElement>(null);
  const [scan, setScan] = useState<ScanResponse | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const report = (text: string, error: boolean) => {
    setMessage({ text, error });
    announce(text, { assertive: error });
  };

  const failure = (err: Error, fallback: string): string => {
    const code = err instanceof ApiError ? err.detail?.code : undefined;
    switch (code) {
      case 'not_available': return t('This is not available for Google Drive libraries.');
      case 'invalid_token': return t('This preview is invalid or expired. Scan the library again.');
      case 'empty_selection': return t('Select at least one book to restore.');
      case 'invalid_selection': return t('The selected books do not match this preview. Scan the library again.');
      case 'write_failed': return t('Could not update the library database. No covers were changed.');
      default: return fallback;
    }
  };

  const scanLibrary = useMutation({
    mutationFn: () => apiPost<ScanResponse>('/api/v1/admin/cover-flags/scan'),
    onSuccess: (result) => {
      setScan(result);
      setSelected(new Set());
      report(result.count
        ? t('{count} book(s) have a cover file but are marked as having no cover.', { count: result.count })
        : t('No books need their cover restored.'), false);
    },
    onError: (err: Error) => {
      setScan(null);
      report(failure(err, t('Could not scan the library.')), true);
    },
  });

  const restore = useMutation({
    mutationFn: (body: { token: string; book_ids: number[] }) =>
      apiPost<RestoreResponse>('/api/v1/admin/cover-flags/restore', body),
    onSuccess: async (result) => {
      setScan(null);
      setSelected(new Set());
      const text = result.skipped
        ? `${t('{count} cover(s) restored.', { count: result.fixed })} ${t('{count} book(s) were skipped because they changed since the scan.', { count: result.skipped })}`
        : t('{count} cover(s) restored.', { count: result.fixed });
      report(text, false);
      await qc.invalidateQueries({ queryKey: ['books'] });
    },
    onError: (err: Error) => report(failure(err, t('Could not restore covers.')), true),
  });

  const ids = scan?.books.map(book => book.id) ?? [];
  const state = selectAllState(selected, ids);
  useEffect(() => {
    if (selectAllBox.current) selectAllBox.current.indeterminate = state === 'some';
  }, [state]);
  const busy = scanLibrary.isPending || restore.isPending;

  return <section className={styles.section} aria-labelledby="cover-flag-repair-heading" id="cover-flag-repair">
    <h2 id="cover-flag-repair-heading" tabIndex={-1} className={styles.heading}>{t('Restore missing covers')}</h2>
    <p className={styles.hint}>{t('Find books that have a cover.jpg in their folder but are marked as having no cover, so the cover is never shown.')}</p>
    <p className={styles.hint}>{t('A cover that was removed on purpose in the book editor also looks like this. Select only the books whose cover you want back.')}</p>

    <div className={styles.actions}>
      <Button type="button" variant="ghost" disabled={busy} onClick={() => { setMessage(null); scanLibrary.mutate(); }}>
        {scanLibrary.isPending ? t('Scanning…') : t('Scan library')}
      </Button>
    </div>

    {scan && scan.token && scan.books.length > 0 && <div>
      <div className={styles.toolbar}>
        <label className={styles.selectAll}>
          <input ref={selectAllBox} type="checkbox" checked={state === 'all'} disabled={busy}
            onChange={() => setSelected(toggleAll(selected, ids))} />
          {t('Select all books')}
        </label>
        <span className={styles.count} aria-live="polite">
          {t('{selected} of {count} selected', { selected: selected.size, count: ids.length })}
        </span>
      </div>
      <ul className={styles.list} aria-label={t('Books with a cover file but no cover')}>
        {scan.books.map(book => <li key={book.id}>
          <label className={styles.row}>
            <input type="checkbox" checked={selected.has(book.id)} disabled={busy}
              aria-label={t('Restore the cover of {title}', { title: book.title })}
              onChange={() => setSelected(toggleOne(selected, book.id))} />
            <span className={styles.title}>{book.title}</span>
          </label>
        </li>)}
      </ul>
      <div className={styles.actions}>
        <Button type="button" disabled={busy || selected.size === 0}
          onClick={() => { setMessage(null); restore.mutate({ token: scan.token as string, book_ids: [...selected] }); }}>
          {restore.isPending ? t('Restoring…') : t('Restore selected covers')}
        </Button>
      </div>
    </div>}

    {message && <p role={message.error ? 'alert' : 'status'}
      className={`${styles.message} ${message.error ? styles.error : ''}`}>{message.text}</p>}
  </section>;
}
