import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiGet, apiPut } from '../lib/api';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { refreshLibraryViews } from '../lib/queries';
import { Button } from './Button';
import { StarRating } from './StarRating';
import styles from './PersonalRating.module.css';

type Scores = { personal_rating: number | null; household_rating?: number | null };

/** Account/book-keyed by the parent. A shared score never seeds a personal one. */
export function PersonalRating({ bookId, accountId }: { bookId: number; accountId: number }) {
  const t = useT();
  const announce = useAnnouncer();
  const qc = useQueryClient();
  const key = ['book-rating', accountId, bookId] as const;
  const url = `/api/v1/books/${bookId}/rating`;
  const query = useQuery({ queryKey: key, queryFn: () => apiGet<Scores>(url), staleTime: 0 });
  const [draft, setDraft] = useState(0);
  const dirty = useRef(false);
  useEffect(() => {
    if (query.data && !dirty.current) setDraft(query.data.personal_rating ?? 0);
  }, [query.data]);
  const save = useMutation({
    mutationFn: (rating: number) => apiPut<Scores>(url, { rating }),
    onSuccess: () => {
      dirty.current = false;
      void qc.invalidateQueries({ queryKey: key });
      void refreshLibraryViews(qc);
      announce(t('Your rating saved.'));
    },
    onError: () => announce(t('Could not save your rating.'), { assertive: true }),
  });
  return <section className={styles.section} aria-labelledby="your-rating-heading">
    <h2 id="your-rating-heading">{t('Your rating')}</h2>
    <p id="your-rating-help">{t('Your score does not change the Calibre library rating. Sharing is off unless you opt in in your account.')}</p>
    {query.isPending ? <p role="status">{t('Loading…')}</p> : query.isError ? <>
      <p role="alert">{t('Could not load your rating.')}</p>
      <Button onClick={() => void query.refetch()}>{t('Retry')}</Button>
    </> : <>
      {query.data.personal_rating ? <StarRating rating={query.data.personal_rating} /> : <p>{t('Unrated')}</p>}
      <form className={styles.form} onSubmit={event => { event.preventDefault(); save.mutate(draft); }}>
        <label htmlFor="your-rating">{t('Your rating')}</label>
        <select id="your-rating" value={draft} disabled={save.isPending}
          aria-describedby={`your-rating-help${save.isError ? ' your-rating-error' : ''}`}
          onChange={event => { dirty.current = true; setDraft(Number(event.currentTarget.value)); save.reset(); }}>
          <option value={0}>{t('Unrated')}</option>
          {Array.from({ length: 10 }, (_, i) => i + 1).map(score =>
            <option key={score} value={score}>{t('{rating} out of 5', { rating: score / 2 })}</option>)}
        </select>
        <Button type="submit" disabled={save.isPending}>{save.isPending ? t('Saving…') : t('Save rating')}</Button>
      </form>
      {save.isError && <p id="your-rating-error" role="alert">{t('Could not save your rating.')}</p>}
      {query.data.household_rating != null && <div className={styles.average}>
        <span>{t('Household average')}</span><StarRating rating={query.data.household_rating} />
      </div>}
    </>}
  </section>;
}
