import { useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  useExecuteDuplicateResolution, usePreviewDuplicateResolution,
  type DuplicateResolutionResult, type DuplicateStrategy,
} from '../lib/queries';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useT } from '../lib/i18n';
import { Button } from './Button';
import styles from './DuplicateAutoResolve.module.css';

/** Groups listed in the preview before collapsing to "and N more". */
const PREVIEW_LIMIT = 50;

/** "#12 (EPUB, PDF, Penguin Classics, 2003)" -- the publisher and year make
 *  it obvious when a group holds different editions rather than true copies. */
function describeBook(id: number, formats: string[], publisher?: string | null, year?: number | null) {
  const parts = [...formats, ...(publisher ? [publisher] : []), ...(year ? [String(year)] : [])];
  return parts.length ? `#${id} (${parts.join(', ')})` : `#${id}`;
}

function ConfirmDialog({ deleted, busy, error, onClose, onConfirm }: {
  deleted: number; busy: boolean; error: string | null; onClose: () => void; onConfirm: () => void;
}) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, { onClose });
  return createPortal(<div className={styles.backdrop}>
    <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="dup-resolve-title" className={styles.dialog} tabIndex={-1}>
      <h3 id="dup-resolve-title">{t('Delete {count} duplicate books?', { count: deleted })}</h3>
      <p>{t('This permanently removes the extra copies from your library. It cannot be undone.')}</p>
      {error && <p className={styles.error}>{error}</p>}
      <div className={styles.buttons}>
        <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>{t('Cancel')}</Button>
        <Button type="button" variant="danger" onClick={onConfirm} disabled={busy}>
          {busy ? t('Deleting…') : t('Delete duplicates')}
        </Button>
      </div>
    </div>
  </div>, document.body);
}

/** Admin-only port of the classic Duplicates page "Auto-Resolve Duplicates"
 *  panel (#1902): pick a strategy, preview what would be deleted, confirm. It
 *  reuses the classic JSON routes, which are admin-gated server side. */
export function DuplicateAutoResolve() {
  const t = useT();
  const announce = useAnnouncer();
  const preview = usePreviewDuplicateResolution();
  const execute = useExecuteDuplicateResolution();
  const [strategy, setStrategy] = useState<DuplicateStrategy>('newest');
  const [plan, setPlan] = useState<DuplicateResolutionResult | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);

  const strategies: { value: DuplicateStrategy; label: string }[] = [
    { value: 'newest', label: t('Keep the newest copy') },
    { value: 'oldest', label: t('Keep the oldest copy') },
    { value: 'merge', label: t('Merge formats into the newest copy') },
    { value: 'highest_quality_format', label: t('Keep the best format') },
    { value: 'most_metadata', label: t('Keep the most complete metadata') },
    { value: 'largest_file_size', label: t('Keep the largest files') },
  ];

  const report = (text: string, error: boolean) => {
    setMessage({ text, error });
    announce(text, { assertive: error });
  };

  const runPreview = () => {
    setPlan(null);
    setMessage(null);
    preview.mutate(strategy, {
      onSuccess: (result) => {
        if (!result.success) {
          report(result.errors?.[0] || result.error || t('Could not preview the resolution.'), true);
        } else if (!result.deleted_count) {
          report(t('Nothing to delete with this strategy.'), false);
        } else {
          setPlan(result);
        }
      },
      onError: (err) => report(err.message || t('Could not preview the resolution.'), true),
    });
  };

  const runExecute = () => {
    execute.mutate(strategy, {
      onSuccess: (result) => {
        setConfirming(false);
        setPlan(null);
        const errors = result.errors?.length ? ` ${result.errors[0]}` : '';
        if (!result.success) {
          report(`${result.error || t('Could not resolve duplicates.')}${errors}`, true);
        } else if (!result.resolved_count && result.message) {
          report(result.message, false);
        } else {
          report(t('Resolved {groups} groups and deleted {deleted} books.', {
            groups: result.resolved_count ?? 0, deleted: result.deleted_count ?? 0,
          }) + errors, false);
        }
      },
      // Keep the dialog open on failure so the error shows beside Retry.
      onError: () => undefined,
    });
  };

  const groups = plan?.preview ?? [];
  return (
    <section className={styles.panel} aria-labelledby="dup-auto-title">
      <h2 id="dup-auto-title" className={styles.heading}>{t('Resolve automatically')}</h2>
      <p className={styles.help}>
        {t('Keeps one book in every group and deletes the other copies, using the strategy you choose.')}
      </p>
      <div className={styles.controls}>
        <label htmlFor="dup-strategy">{t('Strategy')}</label>
        <select id="dup-strategy" className={styles.select} value={strategy}
          onChange={(e) => { setStrategy(e.target.value as DuplicateStrategy); setPlan(null); setMessage(null); }}>
          {strategies.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        <Button type="button" variant="ghost" onClick={runPreview} disabled={preview.isPending}>
          {preview.isPending ? t('Previewing…') : t('Preview')}
        </Button>
      </div>

      <p className={message?.error ? styles.error : styles.status} role="status" aria-live="polite">
        {message?.text ?? ''}
      </p>

      {plan && (
        <div className={styles.plan}>
          <p className={styles.summary}>
            {t('{deleted} books in {groups} groups would be deleted.', {
              deleted: plan.deleted_count ?? 0, groups: plan.resolved_count ?? 0,
            })}
          </p>
          <ul className={styles.planList}>
            {groups.slice(0, PREVIEW_LIMIT).map((g) => (
              <li key={g.group_hash}>
                <span className={styles.planTitle}>{g.title}</span>
                <span className={styles.planAuthor}>{g.author}</span>
                <span className={styles.planDetail}>
                  {t('Keep')}{' '}
                  {describeBook(g.kept_book_id, g.kept_book_formats, g.kept_book_edition?.publisher, g.kept_book_edition?.year)}
                  {' · '}{t('Delete')}{' '}
                  {g.deleted_books_info.map((b) => describeBook(b.id, b.formats, b.publisher, b.year)).join('; ')}
                </span>
              </li>
            ))}
          </ul>
          {groups.length > PREVIEW_LIMIT && (
            <p className={styles.more}>{t('…and {count} more groups', { count: groups.length - PREVIEW_LIMIT })}</p>
          )}
          <Button type="button" variant="danger" onClick={() => { execute.reset(); setConfirming(true); }}>
            {t('Delete duplicates')}
          </Button>
        </div>
      )}

      {confirming && (
        <ConfirmDialog deleted={plan?.deleted_count ?? 0} busy={execute.isPending}
          error={execute.isError ? (execute.error.message || t('Could not resolve duplicates.')) : null}
          onClose={() => setConfirming(false)} onConfirm={runExecute} />
      )}
    </section>
  );
}
