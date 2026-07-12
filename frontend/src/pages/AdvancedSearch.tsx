import { useState, useEffect, useRef, useId } from 'react';
import { Search as SearchIcon, RotateCcw } from 'lucide-react';
import { useIntersectionObserver } from '../lib/useIntersectionObserver';
import { useSearchOptions, useAdvancedSearch, useMe } from '../lib/queries';
import { MultiSelect } from '../components/MultiSelect';
import { BookCard } from '../components/BookCard';
import { Button } from '../components/Button';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import type { Book, AdvancedSearchParams } from '../lib/api';
import { useT } from '../lib/i18n';
import styles from './AdvancedSearch.module.css';

type ReadStatus = 'all' | 'read' | 'unread';

interface FormState {
  title: string;
  authors: string;
  publisher: string;
  comments: string;
  read_status: ReadStatus;
  publishstart: string;
  publishend: string;
  rating_low: string;
  rating_high: string;
  include_tag: (string | number)[];
  exclude_tag: (string | number)[];
  include_serie: (string | number)[];
  exclude_serie: (string | number)[];
  include_language: (string | number)[];
  exclude_language: (string | number)[];
  include_extension: string[];
  exclude_extension: string[];
}

const EMPTY: FormState = {
  title: '', authors: '', publisher: '', comments: '',
  read_status: 'all', publishstart: '', publishend: '', rating_low: '', rating_high: '',
  include_tag: [], exclude_tag: [], include_serie: [], exclude_serie: [],
  include_language: [], exclude_language: [], include_extension: [], exclude_extension: [],
};

const RATINGS = ['', '1', '2', '3', '4', '5'];

function dedupAppend(prev: Book[], next: Book[]): Book[] {
  const seen = new Set(prev.map((b) => b.id));
  const fresh = next.filter((b) => !seen.has(b.id));
  return fresh.length ? [...prev, ...fresh] : prev;
}

export function AdvancedSearch() {
  const t = useT();
  const canEdit = !!useMe().data?.role?.edit;  // quick-edit pencil on results (#572)
  const { data: options } = useSearchOptions();
  const [form, setForm] = useState<FormState>(EMPTY);
  const [submitted, setSubmitted] = useState<AdvancedSearchParams | null>(null);
  const [page, setPage] = useState(1);
  const [results, setResults] = useState<Book[]>([]);
  const accKeyRef = useRef<string>('');

  const { data, isFetching, isPlaceholderData, error } = useAdvancedSearch(submitted, page);

  // Skip placeholder data: on a new search react-query briefly returns the
  // PREVIOUS result (placeholderData) under the new key — acting on it would
  // seed the grid with stale cards that then survive the real-data append.
  useEffect(() => {
    if (!data || isPlaceholderData) return;
    const key = JSON.stringify(submitted);
    if (key !== accKeyRef.current) {
      setResults(data.items);
      accKeyRef.current = key;
    } else {
      setResults((prev) => dedupAppend(prev, data.items));
    }
  }, [data, isPlaceholderData, submitted]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    setResults([]);
    accKeyRef.current = '';
    setSubmitted({ ...form });
  };

  const onReset = () => {
    setForm(EMPTY);
    setSubmitted(null);
    setResults([]);
  };

  const total = data?.total ?? 0;
  const hasMore = results.length < total;
  const sentinelRef = useIntersectionObserver({
    onIntersect: () => setPage((p) => p + 1),
    enabled: hasMore && !isFetching,
  });
  const formatOptions = (options?.formats ?? []).map((f) => ({ id: f, name: f }));

  return (
    <main className={styles.container}>
      <h1 className={styles.title}>{t('Advanced search')}</h1>

      <form className={styles.form} onSubmit={onSubmit}>
        <div className={styles.grid}>
          <Field label={t('Title')}>
            <input className={styles.input} value={form.title} aria-label={t('Title')}
              onChange={(e) => set('title', e.target.value)} />
          </Field>
          <Field label={t('Author')}>
            <input className={styles.input} value={form.authors} aria-label={t('Author')}
              onChange={(e) => set('authors', e.target.value)} />
          </Field>
          <Field label={t('Publisher')}>
            <input className={styles.input} value={form.publisher} aria-label={t('Publisher')}
              onChange={(e) => set('publisher', e.target.value)} />
          </Field>
          <Field label={t('Description contains')}>
            <input className={styles.input} value={form.comments} aria-label={t('Description contains')}
              onChange={(e) => set('comments', e.target.value)} />
          </Field>

          <Field label={t('Read status')}>
            <div className={styles.segmented}>
              {(['all', 'unread', 'read'] as ReadStatus[]).map((rs) => (
                <button key={rs} type="button"
                  className={form.read_status === rs ? styles.segActive : styles.seg}
                  onClick={() => set('read_status', rs)}>
                  {rs === 'all' ? t('Any') : t(rs === 'unread' ? 'Unread' : 'Read')}
                </button>
              ))}
            </div>
          </Field>

          <Field label={t('Published')}>
            <div className={styles.rangeRow}>
              <input type="date" className={styles.input} value={form.publishstart}
                onChange={(e) => set('publishstart', e.target.value)} aria-label={t('Published after')} />
              <span className={styles.rangeSep}>→</span>
              <input type="date" className={styles.input} value={form.publishend}
                onChange={(e) => set('publishend', e.target.value)} aria-label={t('Published before')} />
            </div>
          </Field>

          <Field label={t('Rating (stars)')}>
            <div className={styles.rangeRow}>
              <select className={styles.input} value={form.rating_low}
                onChange={(e) => set('rating_low', e.target.value)} aria-label={t('Minimum rating')}>
                {RATINGS.map((r) => <option key={r} value={r}>{r ? `≥ ${r}` : t('Min')}</option>)}
              </select>
              <span className={styles.rangeSep}>→</span>
              <select className={styles.input} value={form.rating_high}
                onChange={(e) => set('rating_high', e.target.value)} aria-label={t('Maximum rating')}>
                {RATINGS.map((r) => <option key={r} value={r}>{r ? `≤ ${r}` : t('Max')}</option>)}
              </select>
            </div>
          </Field>

          <Field label={t('Tags — include')}>
            <MultiSelect options={options?.tags ?? []} value={form.include_tag}
              onChange={(v) => set('include_tag', v)} placeholder={t('Any tags')} />
          </Field>
          <Field label={t('Tags — exclude')}>
            <MultiSelect options={options?.tags ?? []} value={form.exclude_tag}
              onChange={(v) => set('exclude_tag', v)} placeholder={t('No excluded tags')} />
          </Field>

          <Field label={t('Series — include')}>
            <MultiSelect options={options?.series ?? []} value={form.include_serie}
              onChange={(v) => set('include_serie', v)} placeholder={t('Any series')} />
          </Field>
          <Field label={t('Languages — include')}>
            <MultiSelect options={options?.languages ?? []} value={form.include_language}
              onChange={(v) => set('include_language', v)} placeholder={t('Any language')} />
          </Field>

          <Field label={t('Formats — include')}>
            <MultiSelect options={formatOptions} value={form.include_extension}
              onChange={(v) => set('include_extension', v.map(String))} placeholder={t('Any format')} />
          </Field>
          <Field label={t('Formats — exclude')}>
            <MultiSelect options={formatOptions} value={form.exclude_extension}
              onChange={(v) => set('exclude_extension', v.map(String))} placeholder={t('None')} />
          </Field>
        </div>

        <div className={styles.actions}>
          <Button type="submit">
            <SearchIcon size={16} aria-hidden="true" focusable={false} /> {t('Search')}
          </Button>
          <Button type="button" variant="ghost" onClick={onReset}>
            <RotateCcw size={15} aria-hidden="true" focusable={false} /> {t('Reset')}
          </Button>
        </div>
      </form>

      {/* Results */}
      {submitted !== null && (
        <section className={styles.results} aria-label={t('Search results')}>
          {error ? (
            <EmptyState message={error instanceof Error ? error.message : t('Search failed.')} />
          ) : isFetching && results.length === 0 ? (
            <SpinnerCentered size={32} />
          ) : results.length === 0 ? (
            <EmptyState message={t('No books match those criteria.')} />
          ) : (
            <>
              <p className={styles.resultCount}>
                {total} result{total !== 1 ? 's' : ''}
                {data?.criteria ? ` · ${data.criteria}` : ''}
              </p>
              <div className={styles.resultsGrid}>
                {results.map((book, i) => (
                  <BookCard key={book.id} book={book} quickEdit={canEdit}
                    style={{ animationDelay: `${Math.min(i, 24) * 35}ms` }} />
                ))}
              </div>
              {hasMore && (
                <div ref={sentinelRef} className={styles.loadMore}>
                  {isFetching && (<><Spinner size={16} /> {t('Loading…')}</>)}
                </div>
              )}
            </>
          )}
        </section>
      )}
    </main>
  );
}

// A labelled group. role=group + aria-labelledby is valid for one OR several
// controls (a <label> may only wrap a single control — several date/rating/
// MultiSelect fields wrap two+). Individual controls carry their own names.
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <div className={styles.field} role="group" aria-labelledby={id}>
      <span className={styles.label} id={id}>{label}</span>
      {children}
    </div>
  );
}
