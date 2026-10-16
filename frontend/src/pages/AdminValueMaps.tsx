import { Link } from 'wouter';
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, Check, ChevronLeft, Download, Plus, RotateCcw, Trash2, Upload, Wand2 } from 'lucide-react';
import { ApiError, apiGet, apiPost, apiPut } from '../lib/api';
import { useT } from '../lib/i18n';
import { EmptyState } from '../components/EmptyState';
import { SpinnerCentered } from '../components/Spinner';
import styles from './AdminValueMaps.module.css';

type Field = 'tags';

interface Rule { incoming: string; targets: string[] }
interface LibraryValue { name: string; count: number; covered: boolean }
interface Change { book_id: number; title: string; authors: string[]; before: string[]; after: string[] }
interface SetRow extends Change { id: number; status: string; error: string | null }
interface SetSummary {
  id: number; source: string; status: 'proposed' | 'applied' | 'undone';
  created_at: string | null; applied_at: string | null; counts: Record<string, number>;
}
interface SetDetail extends SetSummary { changes: SetRow[] }
interface MapState {
  field: Field; multi: boolean; rules: Rule[]; values: LibraryValue[]; changes: Change[];
  history: SetSummary[]; files_rewritten: boolean;
}
interface RunResponse {
  written: number;
  removed_values: string[];
  files_rewritten: boolean;
  set: SetDetail;
  state: MapState;
}

const FIELDS: { id: Field; label: string }[] = [
  { id: 'tags', label: 'Tags' },
];

const endpoint = (field: Field) => `/api/v1/admin/value-maps/${field}`;
const fold = (value: string) =>
  value.normalize('NFKD').replace(/\p{M}/gu, '').toLocaleLowerCase().split(/\s+/).filter(Boolean).join(' ');

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    const details = error.detail?.details;
    return Array.isArray(details) ? `${error.message}: ${details.join('; ')}` : error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

function RuleEditor({ multi, initial, onAdd }: {
  multi: boolean;
  initial: string;
  onAdd: (rule: Rule) => string | null;
}) {
  const t = useT();
  const [incoming, setIncoming] = useState(initial);
  const [targets, setTargets] = useState('');
  const [drop, setDrop] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const incomingRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    setIncoming(initial);
    if (initial) incomingRef.current?.focus();
  }, [initial]);

  const submit = () => {
    const list = drop ? [] : (multi ? targets.split(',') : [targets])
      .map((value) => value.trim()).filter(Boolean);
    if (!incoming.trim()) return setProblem(t('Enter the value to match.'));
    if (!drop && list.length === 0) return setProblem(t('Enter what it becomes, or choose Drop.'));
    const error = onAdd({ incoming: incoming.trim(), targets: list });
    setProblem(error);
    if (!error) { setIncoming(''); setTargets(''); setDrop(false); }
  };

  return (
    <form className={styles.editor} onSubmit={(event) => { event.preventDefault(); submit(); }}>
      <div className={styles.fields}>
        <label className={styles.field}>
          <span>{t('Incoming value')}</span>
          <input ref={incomingRef} value={incoming} onChange={(e) => setIncoming(e.target.value)}
            placeholder={multi ? 'Fiction / Thrillers' : 'Zebra Books'} />
        </label>
        <label className={styles.field}>
          <span>{multi ? t('Becomes (comma-separated to split)') : t('Becomes')}</span>
          <input value={targets} disabled={drop} onChange={(e) => setTargets(e.target.value)}
            placeholder={multi ? 'Thriller' : 'Zebra'} />
        </label>
      </div>
      <div className={styles.editorActions}>
        <label className={styles.inlineSwitch}>
          <input type="checkbox" checked={drop} onChange={(e) => setDrop(e.target.checked)} />
          <span>{t('Drop this value')}</span>
        </label>
        <button type="submit" className={styles.secondary}>
          <Plus size={16} aria-hidden="true" focusable={false} /> {t('Add rule')}
        </button>
      </div>
      {problem && <p className={styles.problem} role="alert">{problem}</p>}
    </form>
  );
}

function FieldPanel({ field }: { field: Field }) {
  const t = useT();
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery<MapState>({
    queryKey: ['value-map', field],
    queryFn: () => apiGet(endpoint(field)),
  });
  const [draft, setDraft] = useState<Rule[] | null>(null);
  const [prefill, setPrefill] = useState('');
  const [filter, setFilter] = useState('');
  const [uncoveredOnly, setUncoveredOnly] = useState(true);
  const [sortBy, setSortBy] = useState<'books' | 'name'>('books');
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const rules = draft ?? data?.rules ?? [];
  const dirty = draft !== null;
  const changes = data?.changes ?? [];
  const selected = changes.filter((change) => !excluded.has(change.book_id));

  const values = useMemo(() => {
    const needle = fold(filter);
    const shown = (data?.values ?? []).filter((value) =>
      (!uncoveredOnly || !value.covered) && (!needle || fold(value.name).includes(needle)));
    // By name, folded the way rules match, so near duplicates ("Montreal" and
    // "Montréal", "Zebra" and "Zebra Books") sit next to each other.
    return sortBy === 'name'
      ? [...shown].sort((a, b) => fold(a.name).localeCompare(fold(b.name)) || a.name.localeCompare(b.name))
      : shown;
  }, [data, filter, uncoveredOnly, sortBy]);

  if (isLoading) return <SpinnerCentered size={36} />;
  if (error || !data) return <EmptyState message={t('Could not load the map.')} />;

  const update = (next: MapState) => {
    queryClient.setQueryData(['value-map', field], next);
    setExcluded(new Set());
  };

  const addRule = (rule: Rule): string | null => {
    const key = fold(rule.incoming);
    if (rules.some((existing) => fold(existing.incoming) === key)) {
      return t('There is already a rule for this value.');
    }
    setDraft([...rules, rule].sort((a, b) => fold(a.incoming).localeCompare(fold(b.incoming))));
    setPrefill('');
    return null;
  };

  const save = async (next: Rule[] = rules) => {
    setBusy(true); setFailure(null); setStatus(null);
    try {
      update(await apiPut<MapState>(endpoint(field), { rules: next }));
      setDraft(null);
      setStatus(t('Rules saved. The preview below is up to date.'));
    } catch (e) {
      setFailure(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const importFile = async (file: File) => {
    try {
      const parsed = JSON.parse(await file.text());
      const raw = parsed.rules ?? parsed.map ?? parsed;
      const list: Rule[] = Array.isArray(raw)
        ? raw
        : Object.entries(raw as Record<string, string[] | string>)
          .filter(([key]) => !key.startsWith('_'))
          .map(([incoming, targets]) => ({ incoming, targets: Array.isArray(targets) ? targets : [targets] }));
      await save(list);
    } catch (e) {
      setFailure(t('Could not read that file: {error}', { error: errorText(e) }));
    }
  };

  const exportFile = () => {
    const body = JSON.stringify({ field, rules: data.rules }, null, 2);
    const url = URL.createObjectURL(new Blob([body], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url; a.download = `${field}-map.json`; a.click();
    URL.revokeObjectURL(url);
  };

  const report = (result: RunResponse, verb: 'apply' | 'undo') => {
    update(result.state);
    const counts = result.set.counts;
    const parts = [verb === 'apply'
      ? t('Changed {n} books.', { n: result.written })
      : t('Restored {n} books.', { n: result.written })];
    if (counts.skipped) parts.push(t('{n} skipped because they changed since the preview or were left out.', { n: counts.skipped }));
    if (result.removed_values.length) parts.push(t('Removed {n} values no book uses now.', { n: result.removed_values.length }));
    parts.push(result.files_rewritten
      ? t('The book files are being updated too.')
      : t('Metadata enforcement is off, so the book files keep their old metadata.'));
    setStatus(parts.join(' '));
    const failed = result.set.changes.filter((row) => row.status === 'failed');
    if (failed.length) {
      setFailure(t('{n} books could not be changed: {list}', {
        n: failed.length,
        list: failed.map((row) => `${row.title ?? row.book_id} (${row.error})`).join('; '),
      }));
    }
  };

  const run = async (work: () => Promise<RunResponse>, verb: 'apply' | 'undo') => {
    setConfirming(false); setBusy(true); setFailure(null); setStatus(null);
    try {
      report(await work(), verb);
    } catch (e) {
      setFailure(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const apply = () => run(async () => {
    // The preview becomes a stored change set, then that exact set is applied;
    // a book that changed in between is skipped and reported, not overwritten.
    const proposed = await apiPost<SetDetail>(`${endpoint(field)}/preview`);
    return apiPost<RunResponse>(`/api/v1/admin/change-sets/${proposed.id}/apply`, {
      exclude_book_ids: changes.filter((c) => excluded.has(c.book_id)).map((c) => c.book_id),
    });
  }, 'apply');

  const undo = (setId: number) => run(
    () => apiPost<RunResponse>(`/api/v1/admin/change-sets/${setId}/undo`), 'undo');

  return (
    <>
      <section className={styles.card} aria-labelledby={`${field}-rules`}>
        <header className={styles.cardHeader}>
          <h2 id={`${field}-rules`}>{t('Rules')} <span className={styles.count}>{rules.length}</span></h2>
          <div className={styles.toolbar}>
            <input ref={fileRef} type="file" accept="application/json,.json" hidden
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void importFile(f); e.target.value = ''; }} />
            <button type="button" className={styles.secondary} disabled={busy} onClick={() => fileRef.current?.click()}>
              <Upload size={16} aria-hidden="true" focusable={false} /> {t('Import')}
            </button>
            <button type="button" className={styles.secondary} disabled={busy || data.rules.length === 0} onClick={exportFile}>
              <Download size={16} aria-hidden="true" focusable={false} /> {t('Export')}
            </button>
          </div>
        </header>

        <RuleEditor multi={data.multi} initial={prefill} onAdd={addRule} />

        {rules.length === 0 ? (
          <p className={styles.muted}>{t('No rules yet. Add one above, or import a JSON map.')}</p>
        ) : (
          <ul className={styles.rules} role="list">
            {rules.map((rule) => (
              <li key={rule.incoming} className={styles.rule}>
                <span className={styles.incoming}>{rule.incoming}</span>
                <ArrowRight size={15} aria-hidden="true" focusable={false} className={styles.arrow} />
                <span className={styles.targets}>
                  {rule.targets.length === 0
                    ? <span className={styles.dropped}>{t('Dropped')}</span>
                    : rule.targets.map((target) => <span key={target} className={styles.chip}>{target}</span>)}
                </span>
                <button type="button" className={styles.iconButton} disabled={busy}
                  aria-label={t('Remove the rule for {value}', { value: rule.incoming })}
                  onClick={() => setDraft(rules.filter((r) => r !== rule))}>
                  <Trash2 size={16} aria-hidden="true" focusable={false} />
                </button>
              </li>
            ))}
          </ul>
        )}

        {dirty && (
          <div className={styles.saveBar} role="status">
            <span>{t('Unsaved changes to the rules.')}</span>
            <div className={styles.toolbar}>
              <button type="button" className={styles.secondary} disabled={busy} onClick={() => setDraft(null)}>{t('Discard')}</button>
              <button type="button" className={styles.primary} disabled={busy} onClick={() => void save()}>{t('Save rules')}</button>
            </div>
          </div>
        )}
      </section>

      <section className={styles.card} aria-labelledby={`${field}-values`}>
        <header className={styles.cardHeader}>
          <h2 id={`${field}-values`}>{t('Values in your library')} <span className={styles.count}>{data.values.length}</span></h2>
          <div className={styles.toolbar}>
            <select className={styles.search} value={sortBy} aria-label={t('Sort values')}
              onChange={(e) => setSortBy(e.target.value as 'books' | 'name')}>
              <option value="books">{t('Most books first')}</option>
              <option value="name">{t('By name')}</option>
            </select>
            <input className={styles.search} type="search" value={filter} placeholder={t('Filter')}
              aria-label={t('Filter values')} onChange={(e) => setFilter(e.target.value)} />
            <label className={styles.inlineSwitch}>
              <input type="checkbox" checked={uncoveredOnly} onChange={(e) => setUncoveredOnly(e.target.checked)} />
              <span>{t('Only values without a rule')}</span>
            </label>
          </div>
        </header>
        {values.length === 0 ? (
          <p className={styles.muted}>{uncoveredOnly ? t('Every value is covered by a rule.') : t('No values match.')}</p>
        ) : (
          <ul className={styles.values} role="list">
            {values.map((value) => (
              <li key={value.name} className={styles.value}>
                <span className={styles.valueName}>
                  {value.covered && <Check size={14} aria-label={t('Covered by a rule')} className={styles.covered} />}
                  {value.name}
                </span>
                <span className={styles.valueCount}>{t('{n} books', { n: value.count })}</span>
                {!value.covered && (
                  <button type="button" className={styles.linkButton} onClick={() => setPrefill(value.name)}>
                    {t('Add rule')}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={styles.card} aria-labelledby={`${field}-preview`}>
        <header className={styles.cardHeader}>
          <h2 id={`${field}-preview`}>{t('Preview')} <span className={styles.count}>{changes.length}</span></h2>
          {changes.length > 0 && (
            <div className={styles.toolbar}>
              {confirming ? (
                <>
                  <span className={styles.confirmText}>{t('Change {n} books now?', { n: selected.length })}</span>
                  <button type="button" className={styles.secondary} onClick={() => setConfirming(false)}>{t('Cancel')}</button>
                  <button type="button" className={styles.primary} disabled={busy} onClick={() => void apply()}>{t('Apply')}</button>
                </>
              ) : (
                <button type="button" className={styles.primary} disabled={busy || dirty || selected.length === 0}
                  onClick={() => setConfirming(true)}>
                  <Wand2 size={16} aria-hidden="true" focusable={false} /> {t('Apply to {n} books', { n: selected.length })}
                </button>
              )}
            </div>
          )}
        </header>
        {dirty && <p className={styles.muted}>{t('Save the rules to update the preview.')}</p>}
        {status && <p className={styles.ok} role="status">{status}</p>}
        {failure && <p className={styles.problem} role="alert">{failure}</p>}
        {changes.length === 0 ? (
          <p className={styles.muted}>{t('No book would change with the saved rules.')}</p>
        ) : (
          <ul className={styles.changes} role="list">
            {changes.map((change) => {
              const afterKeys = new Set(change.after.map(fold));
              const beforeKeys = new Set(change.before.map(fold));
              const included = !excluded.has(change.book_id);
              return (
                <li key={change.book_id} className={styles.change} data-included={included}>
                  <label className={styles.changeHead}>
                    <input type="checkbox" checked={included} onChange={() => {
                      const next = new Set(excluded);
                      if (included) next.add(change.book_id); else next.delete(change.book_id);
                      setExcluded(next);
                    }} />
                    <span>
                      <Link href={`/book/${change.book_id}`} className={styles.bookTitle}>{change.title}</Link>
                      {change.authors.length > 0 && <span className={styles.authors}> · {change.authors.join(', ')}</span>}
                    </span>
                  </label>
                  <div className={styles.diff}>
                    <span className={styles.chips}>
                      {change.before.map((value) => (
                        <span key={value} className={afterKeys.has(fold(value)) && change.after.includes(value)
                          ? styles.chip : styles.chipRemoved}>{value}</span>
                      ))}
                    </span>
                    <ArrowRight size={15} aria-hidden="true" focusable={false} className={styles.arrow} />
                    <span className={styles.chips}>
                      {change.after.length === 0 && <span className={styles.dropped}>{t('None')}</span>}
                      {change.after.map((value) => (
                        <span key={value} className={beforeKeys.has(fold(value)) && change.before.includes(value)
                          ? styles.chip : styles.chipAdded}>{value}</span>
                      ))}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className={styles.card} aria-labelledby={`${field}-history`}>
        <header className={styles.cardHeader}>
          <h2 id={`${field}-history`}>{t('History')}</h2>
        </header>
        {data.history.length === 0 ? (
          <p className={styles.muted}>{t('Nothing applied yet.')}</p>
        ) : (
          <ul className={styles.history} role="list">
            {data.history.map((item) => (
              <li key={item.id} className={styles.historyRow}>
                <span>
                  <span className={styles.incoming}>{item.applied_at ? new Date(item.applied_at).toLocaleString() : `#${item.id}`}</span>
                  <span className={styles.historyCounts}>
                    {Object.entries(item.counts).map(([key, n]) => `${t(key)} ${n}`).join(' · ')}
                  </span>
                </span>
                {item.status === 'applied' ? (
                  <button type="button" className={styles.secondary} disabled={busy} onClick={() => void undo(item.id)}>
                    <RotateCcw size={16} aria-hidden="true" focusable={false} /> {t('Undo')}
                  </button>
                ) : (
                  <span className={styles.stateMuted}>{t('Undone')}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

export function AdminValueMaps() {
  const t = useT();
  const [field, setField] = useState<Field>('tags');
  const onTabKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = FIELDS.findIndex((item) => item.id === field);
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % FIELDS.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + FIELDS.length) % FIELDS.length;
    else return;
    event.preventDefault();
    setField(FIELDS[next].id);
    event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  };

  return (
    <div className={styles.container}>
      <Link href="/admin" className={styles.back}>
        <ChevronLeft size={16} aria-hidden="true" focusable={false} /> {t('Admin')}
      </Link>
      <div className={styles.heading}>
        <Wand2 aria-hidden="true" focusable="false" />
        <h1>{t('Metadata clean-up')}</h1>
      </div>
      <p className={styles.intro}>
        {t('One map per field. A rule turns a value into another (rename), several (split) or none (drop), matching regardless of case and accents. Values no rule covers stay as they are. Nothing changes in the library until you apply the preview.')}
      </p>
      <div className={styles.tabs} role="tablist" aria-label={t('Field')} onKeyDown={onTabKeyDown}>
        {FIELDS.map((item) => (
          <button key={item.id} id={`value-map-tab-${item.id}`} type="button" role="tab"
            aria-selected={field === item.id} aria-controls={`value-map-panel-${item.id}`}
            tabIndex={field === item.id ? 0 : -1} onClick={() => setField(item.id)}>
            {t(item.label)}
          </button>
        ))}
      </div>
      <div id={`value-map-panel-${field}`} role="tabpanel" aria-labelledby={`value-map-tab-${field}`}
        className={styles.panel}>
        <FieldPanel key={field} field={field} />
      </div>
    </div>
  );
}
