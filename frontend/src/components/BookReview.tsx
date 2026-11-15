import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiDelete, apiGet, apiPut } from '../lib/api';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { Button } from './Button';
import styles from './BookReview.module.css';

type ReviewResponse = { review: { text: string; updated_at: string } | null };

function DeleteReview({ busy, error, onClose, onDelete }: {
  busy: boolean; error: string | null; onClose: () => void; onDelete: () => void;
}) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, { onClose });
  return createPortal(<div className={styles.backdrop}>
    <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="delete-review-title" tabIndex={-1} className={styles.dialog}>
      <h3 id="delete-review-title">{t('Delete your review?')}</h3>
      <p>{t('This removes your private review or note for this book.')}</p>
      {error && <p className={styles.error}>{error}</p>}
      <div className={styles.actions}>
        <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>{t('Cancel')}</Button>
        <Button type="button" variant="danger" disabled={busy} onClick={onDelete}>{busy ? t('Deleting…') : t('Delete review')}</Button>
      </div>
    </div>
  </div>, document.body);
}

/** One private text per account/book; shared Calibre Description stays separate. */
export function BookReview({ bookId, accountId }: { bookId: number; accountId: number }) {
  const t = useT();
  const announce = useAnnouncer();
  const qc = useQueryClient();
  const key = ['book-review', accountId, bookId] as const;
  const url = `/api/v1/books/${bookId}/review`;
  const query = useQuery({ queryKey: key, queryFn: () => apiGet<ReviewResponse>(url), staleTime: 0 });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const editor = useRef<HTMLTextAreaElement>(null);
  const editButton = useRef<HTMLDivElement>(null);
  const returnToEdit = useRef(false);
  useEffect(() => {
    if (editing) editor.current?.focus();
    else if (returnToEdit.current) { editButton.current?.querySelector<HTMLButtonElement>('button')?.focus(); returnToEdit.current = false; }
  }, [editing]);
  const finishEditing = () => { returnToEdit.current = true; setEditing(false); setError(null); };
  const save = useMutation({
    mutationFn: (text: string) => apiPut<ReviewResponse>(url, { text }),
    onSuccess: data => {
      qc.setQueryData(key, data); finishEditing(); announce(t('Review saved.'));
    },
    onError: (err: Error) => { const text = err.message || t('Could not save review.'); setError(text); announce(text, { assertive: true }); },
  });
  const remove = useMutation({
    mutationFn: () => apiDelete(url),
    onSuccess: () => {
      qc.setQueryData(key, { review: null }); setDeleting(false); setDeleteError(null);
      announce(t('Review deleted.')); queueMicrotask(() => heading.current?.focus());
    },
    onError: (err: Error) => { const text = err.message || t('Could not delete review.'); setDeleteError(text); announce(text, { assertive: true }); },
  });
  const pending = useRef(false); pending.current = remove.isPending;
  const closeDelete = useCallback(() => { if (!pending.current) setDeleting(false); }, []);
  const review = query.data?.review;
  const characterCount = Array.from(draft).length;
  return <section className={styles.section} aria-labelledby="book-review-heading" data-testid="book-review">
    <h2 id="book-review-heading" ref={heading} tabIndex={-1}>{t('Your review')}</h2>
    <p id="book-review-privacy" className={styles.hint}>{t('Private to your account. This does not change the shared Description.')}</p>
    {query.isLoading ? <p role="status">{t('Loading…')}</p> : query.error ? <>
      <p role="alert" className={styles.error}>{t('Could not load your review.')}</p>
      <Button variant="ghost" onClick={() => void query.refetch()}>{t('Retry')}</Button>
    </> : editing ? <form onSubmit={event => { event.preventDefault(); if (draft.trim() && characterCount <= 10000) save.mutate(draft); }} aria-busy={save.isPending}>
      <label htmlFor="book-review-text">{t('Review or note')}</label>
      <textarea id="book-review-text" ref={editor} value={draft} rows={6} required dir="auto" disabled={save.isPending}
        aria-invalid={!!error || characterCount > 10000}
        aria-describedby={`book-review-privacy book-review-count${error ? ' book-review-error' : ''}`}
        onChange={event => { setDraft(event.target.value); setError(null); }} />
      <p id="book-review-count" className={styles.hint}>{t('{n} of {max} characters', { n: characterCount, max: 10000 })}</p>
      {error && <p id="book-review-error" className={styles.error}>{error}</p>}
      <div className={styles.actions}>
        <Button type="button" variant="ghost" onClick={finishEditing} disabled={save.isPending}>{t('Cancel')}</Button>
        <Button type="submit" disabled={save.isPending || !draft.trim() || characterCount > 10000}>{save.isPending ? t('Saving…') : t('Save review')}</Button>
      </div>
    </form> : <>
      {review ? <p className={styles.text} dir="auto">{review.text}</p> : <p className={styles.hint}>{t('Keep your thoughts about this book here.')}</p>}
      <div className={styles.actions}>
        <div ref={editButton}><Button type="button" variant="ghost" onClick={() => { setDraft(review?.text ?? ''); setError(null); setEditing(true); }}>{review ? t('Edit review') : t('Add a review')}</Button></div>
        {review && <Button type="button" variant="ghost" onClick={() => { setDeleteError(null); setDeleting(true); }}>{t('Delete review')}</Button>}
      </div>
    </>}
    {deleting && <DeleteReview busy={remove.isPending} error={deleteError} onClose={closeDelete} onDelete={() => remove.mutate()} />}
  </section>;
}
