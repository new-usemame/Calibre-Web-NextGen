import { useEffect, useId, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import { getAcquisitionCatalog, type AcquisitionConnection } from '../lib/acquisition';
import { SEARCH_BATCH_SIZE, searchCatalogBatch, type CatalogSearchResult } from '../lib/acquisitionSearch';
import { useT } from '../lib/i18n';
import styles from './FindBooks.module.css';

/** Browser orchestration of the existing permission/opaque-selection API.
 * No source endpoints, search templates or credentials enter this component. */
export function AcquisitionSearch({ connections, onBrowse, renderCatalog }: {
  connections: AcquisitionConnection[];
  onBrowse: (id: string) => void;
  renderCatalog: (result: CatalogSearchResult, query: string) => React.ReactNode;
}) {
  const t = useT();
  const inputId = useId();
  const [draft, setDraft] = useState('');
  const [query, setQuery] = useState('');
  const [sources, setSources] = useState<AcquisitionConnection[]>([]);
  const [results, setResults] = useState<Record<string, CatalogSearchResult>>({});
  const [pending, setPending] = useState<string[]>([]);
  const [offset, setOffset] = useState(0);
  const active = useRef<AbortController | null>(null);
  const currentConnections = useRef(connections);
  currentConnections.current = connections;
  const withdrawn = useRef(new Set<string>());
  const sourceKey = (source: AcquisitionConnection) => `${source.id}:${source.revision}`;
  useEffect(() => () => active.current?.abort(), []);

  // A bootstrap refresh can withdraw or revise a source while reads are in
  // flight. Do not keep its old results actionable or search its stale config.
  const configured = (source: AcquisitionConnection) => currentConnections.current.some((c) =>
    c.enabled && c.id === source.id && c.revision === source.revision);
  const live = (source: AcquisitionConnection) => configured(source) && !withdrawn.current.has(sourceKey(source));
  useEffect(() => {
    // Withdrawal invalidates this query's snapshot permanently. Re-enabling
    // the same revision must require a fresh search, including for late reads.
    const removed = sources.filter((source) => !configured(source));
    if (!removed.length) return;
    for (const source of removed) withdrawn.current.add(sourceKey(source));
    setResults((old) => Object.fromEntries(Object.entries(old).filter(([, result]) => live(result.connection))));
    setPending((old) => old.filter((id) => !removed.some((source) => source.id === id)));
  }, [connections, sources]);
  const remaining = sources.slice(offset).filter(live);
  const completed = sources.filter((c) => live(c) && results[c.id]).length;

  const run = async (batch: AcquisitionConnection[], term: string, replace: boolean) => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    if (replace) setResults({});
    else setResults((old) => Object.fromEntries(Object.entries(old).filter(([id]) =>
      !batch.some((c) => c.id === id))));
    setPending(batch.map((c) => c.id));
    try {
      await searchCatalogBatch(batch, term, getAcquisitionCatalog, controller.signal, (result) => {
        if (active.current !== controller || !live(result.connection)) return;
        setResults((old) => ({ ...old, [result.connection.id]: result }));
        setPending((old) => old.filter((id) => id !== result.connection.id));
      });
    } finally {
      if (active.current === controller) setPending([]);
    }
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const term = draft.trim();
    if (!term) return;
    const snapshot = connections.filter((c) => c.enabled);
    withdrawn.current.clear();
    setQuery(term); setSources(snapshot); setOffset(SEARCH_BATCH_SIZE);
    void run(snapshot.slice(0, SEARCH_BATCH_SIZE), term, true);
  };

  return <section aria-label={t('Search all catalogs')}>
    <form className={styles.search} onSubmit={submit} role="search">
      <label className={styles.srOnly} htmlFor={inputId}>{t('Search all catalogs')}</label>
      <input id={inputId} type="search" value={draft} onChange={(e) => setDraft(e.target.value)}
        placeholder={t('Search all catalogs')} maxLength={500} />
      <button type="submit" disabled={!draft.trim()}>
        <Search size={16} aria-hidden="true" focusable={false} /><span>{t('Search')}</span>
      </button>
    </form>
    <p className={styles.muted}>{t('Results stay separate by catalog so you can choose the edition and format.')}</p>
    <p role="status" aria-atomic="true" className={`${styles.muted} ${styles.searchStatus}`}>
      {query && (pending.length
        ? t('Searching catalogs…')
        : t('Checked {count} catalogs for “{query}”.', { count: completed, query }))}
    </p>
    {sources.filter(live).filter((c) => results[c.id] || pending.includes(c.id)).map((source) => {
      const result = results[source.id];
      return <section key={source.id} aria-label={source.label} className={styles.activity}>
        <h2>{source.label}</h2>
        {pending.includes(source.id) && <p className={styles.muted}>{t('Searching this catalog…')}</p>}
        {result?.state === 'failed' && <div className={`${styles.sectionError} ${styles.searchFailure}`} role="alert">
          <p>{t('This catalog could not be searched. Results from other catalogs are still available.')}</p>
          <button type="button" className={styles.secondary} disabled={pending.length > 0}
            onClick={() => void run([source], query, false)}>{t('Try again')}</button>
        </div>}
        {result?.state === 'browse-only' && <p className={styles.muted}>{t('This catalog does not offer search.')}</p>}
        <button type="button" className={styles.secondary} onClick={() => onBrowse(source.id)}>{t('Browse this catalog')}</button>
        {result?.state === 'ready' && renderCatalog(result, query)}
      </section>;
    })}
    {query && remaining.length > 0 && <div className={styles.notice}>
      <div>
        <p>{t('{count} catalogs have not been searched yet.', { count: remaining.length })}</p>
        <button type="button" className={styles.secondary} disabled={pending.length > 0} onClick={() => {
          const batch = remaining.slice(0, SEARCH_BATCH_SIZE);
          const last = sources.findIndex((c) => c.id === batch[batch.length - 1].id);
          setOffset(last + 1); void run(batch, query, false);
        }}>{t('Search remaining catalogs')}</button>
      </div>
    </div>}
  </section>;
}
