import { useState, useEffect, useRef, useId } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'wouter';
import { ChevronLeft, Save, Trash2, RefreshCw, Image as ImageIcon, Upload as UploadIcon, ExternalLink, Sparkles, Search, Plus, X, MoreHorizontal } from 'lucide-react';
import {
  useBookMetadata, useUpdateMetadata, useBook, useMe, useDeleteFormat, useConvertFormat,
  useSetCover, useMetadataSearch, useAddFormat,
} from '../lib/queries';
import { Button } from '../components/Button';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import type { MetadataUpdate, MetaResult } from '../lib/api';
import { ApiError, resourceUrl } from '../lib/api';
import { useT } from '../lib/i18n';
import styles from './EditBook.module.css';

interface Ident { type: string; val: string }

interface FormState {
  title: string;
  authors: string;
  series: string;
  series_index: string;
  tags: string;
  publishers: string;
  languages: string;
  rating: string;
  comments: string;
  identifiers: Ident[];
}

const RATINGS = ['', '1', '2', '3', '4', '5'];

/** Which fields a fetched result can contribute, in display order. `has` decides
 *  whether the result actually offers the field (so we only show applicable rows),
 *  and `preview` renders the incoming value in the per-field apply checklist. */
type ApplyKey = 'title' | 'authors' | 'series' | 'tags' | 'publisher' | 'rating' | 'description' | 'identifiers' | 'cover';
const APPLY_FIELDS: { key: ApplyKey; label: string; has: (r: MetaResult) => boolean; preview: (r: MetaResult) => string }[] = [
  { key: 'title', label: 'Title', has: (r) => !!r.title, preview: (r) => r.title },
  { key: 'authors', label: 'Authors', has: (r) => !!r.authors?.length, preview: (r) => (r.authors || []).join(', ') },
  { key: 'series', label: 'Series', has: (r) => !!r.series, preview: (r) => `${r.series}${r.series_index ? ` #${r.series_index}` : ''}` },
  { key: 'tags', label: 'Tags', has: (r) => !!r.tags?.length, preview: (r) => (r.tags || []).join(', ') },
  { key: 'publisher', label: 'Publisher', has: (r) => !!r.publisher, preview: (r) => r.publisher || '' },
  { key: 'rating', label: 'Rating', has: (r) => !!r.rating, preview: (r) => `${Math.round(r.rating || 0)} ★` },
  { key: 'description', label: 'Description', has: (r) => !!r.description, preview: (r) => stripTags(r.description || '').slice(0, 140) },
  { key: 'identifiers', label: 'Identifiers', has: (r) => !!r.identifiers && Object.keys(r.identifiers).length > 0, preview: (r) => Object.entries(r.identifiers || {}).map(([k, v]) => `${k}:${v}`).join(', ') },
  { key: 'cover', label: 'Cover', has: (r) => !!r.cover, preview: () => '' },
];

function stripTags(s: string) { return s.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim(); }

export function EditBook({ id }: { id: string }) {
  const t = useT();
  const { data: meta, isLoading, error } = useBookMetadata(id);
  const update = useUpdateMetadata(id);
  const setCover = useSetCover(id);
  const [, navigate] = useLocation();

  const [form, setForm] = useState<FormState | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (!meta) return;
    setForm({
      title: meta.title,
      authors: meta.authors,
      series: meta.series,
      series_index: meta.series_index != null ? String(meta.series_index) : '',
      tags: meta.tags,
      publishers: meta.publishers,
      languages: meta.languages,
      rating: meta.rating ? String(meta.rating) : '',
      comments: meta.comments,
      identifiers: (meta.identifiers || []).map((i) => ({ type: i.type, val: i.val })),
    });
  }, [meta]);

  if (isLoading || !form) {
    if (error) {
      return (
        <main className={styles.container}>
          <EmptyState message={error instanceof Error ? error.message : 'Could not load metadata.'} />
        </main>
      );
    }
    return <SpinnerCentered size={40} />;
  }

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f));

  // Apply only the user-selected fields of an online result into the form. Cover
  // is applied as a side effect (it isn't a form field). Identifiers merge by
  // type (result overrides same-type rows, keeps the rest).
  const applySelected = (r: MetaResult, sel: Set<ApplyKey>) => {
    setForm((f) => {
      if (!f) return f;
      const next = { ...f };
      if (sel.has('title') && r.title) next.title = r.title;
      if (sel.has('authors') && r.authors?.length) next.authors = r.authors.join(' & ');
      if (sel.has('tags') && r.tags?.length) next.tags = r.tags.join(', ');
      if (sel.has('publisher') && r.publisher) next.publishers = r.publisher;
      if (sel.has('series') && r.series) {
        next.series = r.series;
        if (r.series_index) next.series_index = String(r.series_index);
      }
      if (sel.has('rating') && r.rating) next.rating = String(Math.round(r.rating));
      if (sel.has('description') && r.description) next.comments = r.description;
      if (sel.has('identifiers') && r.identifiers) {
        const byType = new Map(next.identifiers.map((i) => [i.type.toLowerCase(), i]));
        for (const [type, val] of Object.entries(r.identifiers)) {
          const ty = String(type || '').trim().toLowerCase();
          const vv = String(val ?? '').trim();
          if (ty && vv) byType.set(ty, { type: ty, val: vv });
        }
        next.identifiers = [...byType.values()];
      }
      return next;
    });
    if (sel.has('cover') && r.cover) {
      setCover.mutate({ url: r.cover }, {
        onSuccess: () => setBanner({ ok: true, text: t('Cover updated from the selected result.') }),
        onError: (err) => setBanner({ ok: false, text: err instanceof ApiError ? err.message : 'Cover update failed.' }),
      });
    }
  };

  const setIdent = (i: number, patch: Partial<Ident>) =>
    setForm((f) => (f ? { ...f, identifiers: f.identifiers.map((row, j) => (j === i ? { ...row, ...patch } : row)) } : f));
  const addIdent = () => setForm((f) => (f ? { ...f, identifiers: [...f.identifiers, { type: '', val: '' }] } : f));
  const removeIdent = (i: number) => setForm((f) => (f ? { ...f, identifiers: f.identifiers.filter((_, j) => j !== i) } : f));

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setBanner(null);
    setFieldErrors({});
    const payload: MetadataUpdate = {
      title: form.title,
      authors: form.authors,
      series: form.series,
      series_index: form.series_index,
      tags: form.tags,
      publishers: form.publishers,
      languages: form.languages,
      rating: form.rating ? Number(form.rating) : 0,
      comments: form.comments,
      // Drop blank rows; the backend reconciles the rest against existing rows.
      identifiers: form.identifiers
        .map((i) => ({ type: i.type.trim().toLowerCase(), val: i.val.trim() }))
        .filter((i) => i.type && i.val),
    };
    update.mutate(payload, {
      onSuccess: (data) => {
        if (data.errors && Object.keys(data.errors).length > 0) {
          setFieldErrors(data.errors);
          setBanner({ ok: false, text: 'Some fields could not be saved.' });
        } else {
          setBanner({ ok: true, text: 'Saved.' });
          navigate(`/book/${id}`);
        }
      },
      onError: (err) =>
        setBanner({ ok: false, text: err instanceof ApiError ? err.message : 'Save failed.' }),
    });
  };

  return (
    <main className={styles.container}>
      <Link href={`/book/${id}`} className={styles.back}>
        <ChevronLeft size={16} /> {t('Back to book')}
      </Link>
      <h1 className={styles.title}>{t('Edit metadata')}</h1>

      <CoverManager id={id} />

      <MetadataFetch defaultQuery={form.title} onApply={applySelected} />

      <form className={styles.form} onSubmit={onSubmit}>
        <Field label={t('Title')} error={fieldErrors.title}>
          <input className={styles.input} value={form.title} onChange={(e) => set('title', e.target.value)} />
        </Field>
        <Field label={t('Authors (separate with &)')} error={fieldErrors.authors}>
          <input className={styles.input} value={form.authors} onChange={(e) => set('authors', e.target.value)} />
        </Field>

        <div className={styles.row}>
          <Field label={t('Series')} error={fieldErrors.series}>
            <input className={styles.input} value={form.series} onChange={(e) => set('series', e.target.value)} />
          </Field>
          <Field label={t('Series index')} error={fieldErrors.series_index} grow={false}>
            <input className={styles.inputNarrow} type="number" step="0.01" value={form.series_index}
              onChange={(e) => set('series_index', e.target.value)} />
          </Field>
        </div>

        <Field label={t('Tags (comma separated)')} error={fieldErrors.tags}>
          <input className={styles.input} value={form.tags} onChange={(e) => set('tags', e.target.value)} />
        </Field>
        <Field label={t('Publishers (comma separated)')} error={fieldErrors.publishers}>
          <input className={styles.input} value={form.publishers} onChange={(e) => set('publishers', e.target.value)} />
        </Field>

        <div className={styles.row}>
          <Field label={t('Languages (comma separated)')} error={fieldErrors.languages}>
            <input className={styles.input} value={form.languages} onChange={(e) => set('languages', e.target.value)} />
          </Field>
          <Field label={t('Rating')} error={fieldErrors.rating} grow={false}>
            <select className={styles.inputNarrow} value={form.rating} onChange={(e) => set('rating', e.target.value)}>
              {RATINGS.map((r) => <option key={r} value={r}>{r ? `${r} ★` : '—'}</option>)}
            </select>
          </Field>
        </div>

        <Field label={t('Description')} error={fieldErrors.comments}>
          <textarea className={styles.textarea} rows={8} value={form.comments}
            onChange={(e) => set('comments', e.target.value)} />
          <span className={styles.hint}>{t('HTML is allowed and sanitized on display.')}</span>
        </Field>

        {/* Identifiers table (ISBN/ASIN/…) — fork #580. */}
        <div className={styles.identSection}>
          <span className={styles.label}>{t('Identifiers')}</span>
          {fieldErrors.identifiers && <span className={styles.fieldError}>{fieldErrors.identifiers}</span>}
          {form.identifiers.length > 0 && (
            <div className={styles.identTable} role="group" aria-label={t('Identifiers')}>
              {form.identifiers.map((idn, i) => (
                <div key={i} className={styles.identRow}>
                  <input className={styles.identType} value={idn.type} aria-label={t('Identifier type')}
                    placeholder={t('type (isbn, amazon, doi…)')} onChange={(e) => setIdent(i, { type: e.target.value })} />
                  <input className={styles.identVal} value={idn.val} aria-label={t('Identifier value')}
                    placeholder={t('value')} onChange={(e) => setIdent(i, { val: e.target.value })} />
                  <button type="button" className={styles.identRemove} onClick={() => removeIdent(i)}
                    aria-label={t('Remove identifier')}><X size={15} /></button>
                </div>
              ))}
            </div>
          )}
          <button type="button" className={styles.identAdd} onClick={addIdent}>
            <Plus size={14} /> {t('Add identifier')}
          </button>
          <span className={styles.hint}>{t('Each type (isbn, amazon, google, doi…) may appear once.')}</span>
        </div>

        <div className={styles.actions}>
          <Button type="submit" disabled={update.isPending}>
            <Save size={16} /> {t('Save changes')}
          </Button>
          <Link href={`/book/${id}`} className={styles.cancel}>{t('Cancel')}</Link>
          {banner && <span className={banner.ok ? styles.msgOk : styles.msgErr}>{banner.text}</span>}
        </div>
      </form>

      <FormatsManager id={id} />
    </main>
  );
}

/** Fetch metadata from online providers (Google Books, OpenLibrary, Amazon,
 *  ComicVine, …). Each result expands a per-field checklist so you apply only the
 *  values you want (fork #580) instead of overwriting the whole form. Reuses the
 *  legacy /metadata/search endpoint (per-user provider toggles live there). */
function MetadataFetch({ defaultQuery, onApply }:
  { defaultQuery: string; onApply: (r: MetaResult, sel: Set<ApplyKey>) => void }) {
  const t = useT();
  const search = useMetadataSearch();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(defaultQuery);
  const [results, setResults] = useState<MetaResult[]>([]);
  const [err, setErr] = useState<string | null>(null);
  // Editions drill-down (Hardcover): when set we're showing the editions of one
  // book, and `prev` holds the title-level view to return to via "Back".
  const [editions, setEditions] = useState<{ prevQuery: string; prevResults: MetaResult[] } | null>(null);
  // Monotonic search id. Every search (and closing the panel) bumps it; a response
  // only applies if it's still the latest — so a slow editions request that the
  // user has since abandoned (Close) or superseded (a second Editions click)
  // can't force the panel into a stale editions view or show the wrong book.
  const seq = useRef(0);

  // Single search path for the normal form, the editions drill-down, and Back.
  // `ed` is the title-level snapshot captured before drilling in; when present we
  // keep only Hardcover edition rows (a `hardcover-id:<id>` query still fans out
  // to every enabled provider, so other providers return noise for that string).
  const doSearch = (q: string, ed?: { prevQuery: string; prevResults: MetaResult[] }) => {
    const term = q.trim();
    if (!term) return;
    const mine = ++seq.current;
    setErr(null);
    setQuery(term);
    search.mutate(term, {
      onSuccess: (r) => {
        if (mine !== seq.current) return; // superseded/abandoned — ignore
        if (ed) {
          setResults(r.results.filter((x) => x.identifiers && 'hardcover-edition' in x.identifiers));
          setEditions(ed);
        } else {
          setResults(r.results);
          setEditions(null);
        }
      },
      onError: (e2) => { if (mine === seq.current) setErr(e2 instanceof ApiError ? e2.message : 'Search failed.'); },
    });
  };

  const run = (e: React.FormEvent) => { e.preventDefault(); doSearch(query); };
  const openEditions = (hardcoverId: string) =>
    doSearch(`hardcover-id:${hardcoverId}`, { prevQuery: query, prevResults: results });
  const backToResults = () => {
    if (!editions) return;
    setQuery(editions.prevQuery);
    setResults(editions.prevResults);
    setEditions(null);
    setErr(null);
  };
  // Open/close the panel from a clean state: never reopen stranded in an editions
  // drill-down, and invalidate any in-flight request on close.
  const openPanel = () => { seq.current++; setOpen(true); setQuery(defaultQuery); setEditions(null); setErr(null); };
  const closePanel = () => { seq.current++; setOpen(false); setEditions(null); };

  return (
    <section className={styles.metaFetch}>
      {!open ? (
        <Button type="button" variant="ghost" onClick={openPanel}>
          <Sparkles size={15} /> {t('Fetch metadata from web')}
        </Button>
      ) : (
        <div className={styles.metaPanel}>
          {editions ? (
            <div className={styles.editionsHead}>
              <button type="button" className={styles.cancel} onClick={backToResults}>
                <ChevronLeft size={14} /> {t('Back to results')}
              </button>
              <span className={styles.editionsTitle}>{t('Editions')}</span>
              {search.isPending && <Spinner size={14} />}
              <button type="button" className={styles.cancel} style={{ marginLeft: 'auto' }} onClick={closePanel}>{t('Close')}</button>
            </div>
          ) : (
            <form className={styles.metaSearchRow} onSubmit={run}>
              <input className={styles.input} value={query} onChange={(e) => setQuery(e.target.value)}
                placeholder={t('Title, author, or ISBN')} autoFocus />
              <Button type="submit" disabled={search.isPending || !query.trim()}>
                {search.isPending ? <Spinner size={15} /> : <Search size={15} />} {t('Search')}
              </Button>
              <button type="button" className={styles.cancel} onClick={closePanel}>{t('Close')}</button>
            </form>
          )}
          {err && <span className={styles.msgErr}>{err}</span>}
          {editions && !search.isPending && results.length === 0 && (
            <span className={styles.metaEmpty}>{t('No editions found for this book.')}</span>
          )}
          {results.length > 0 && (
            <ul className={styles.metaResults}>
              {results.map((r, i) => (
                <ResultRow key={i} r={r} onApply={onApply} onEditions={editions ? undefined : openEditions} />
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

/** One search result: shows the book, and (on "Choose fields") a checklist of the
 *  values it offers so the user applies exactly what they want. Hardcover title
 *  results also expose an "Editions" drill-down (via `onEditions`) so the user can
 *  pick a specific edition — each carries its own `hardcover-edition`/ISBN
 *  identifiers, which Hardcover progress-sync needs. */
function ResultRow({ r, onApply, onEditions }:
  { r: MetaResult; onApply: (r: MetaResult, sel: Set<ApplyKey>) => void; onEditions?: (hardcoverId: string) => void }) {
  const t = useT();
  const fields = APPLY_FIELDS.filter((f) => f.has(r));
  const [showDetails, setShowDetails] = useState(false);
  // Offer "Editions" only on a title-level Hardcover result (has hardcover-id but
  // is not itself an edition row). onEditions is absent while already viewing editions.
  const hcId = r.identifiers?.['hardcover-id'];
  const canEditions = !!onEditions && hcId != null && hcId !== ''
    && !(r.identifiers && 'hardcover-edition' in r.identifiers);
  const editionMeta = [r.format, r.publisher, r.publishedDate].filter(Boolean).join(' · ');
  const [expanded, setExpanded] = useState(false);
  const [sel, setSel] = useState<Set<ApplyKey>>(() => new Set(fields.map((f) => f.key)));

  // Results are keyed by index, so a new search reuses this instance rather than
  // remounting — reset the checklist (and collapse) whenever the result changes,
  // or a prior result's selection would leak onto a different book.
  useEffect(() => {
    setSel(new Set(APPLY_FIELDS.filter((f) => f.has(r)).map((f) => f.key)));
    setExpanded(false);
    setShowDetails(false);
  }, [r]);

  const toggle = (k: ApplyKey) => setSel((s) => {
    const n = new Set(s);
    if (n.has(k)) n.delete(k); else n.add(k);
    return n;
  });

  return (
    <li className={styles.metaResult}>
      <div className={styles.metaResultHead}>
        {r.cover && <img src={r.cover} alt="" className={styles.metaCover} loading="lazy" />}
        <div className={styles.metaInfo}>
          <span className={styles.metaTitle}>{r.title}</span>
          <span className={styles.metaAuthors}>{(r.authors || []).join(', ')}</span>
          {editionMeta && <span className={styles.metaEdition}>{editionMeta}</span>}
          {r.source?.id && <span className={styles.metaSource}>{r.source.id}</span>}
        </div>
        <div className={styles.metaResultActions}>
          {canEditions && (
            <Button type="button" variant="ghost" onClick={() => onEditions!(String(hcId))}>
              <Search size={14} /> {t('Editions')}
            </Button>
          )}
          <Button type="button" variant="ghost" onClick={() => setShowDetails(true)}
            aria-label={t('View all details')} title={t('View all details')}>
            <MoreHorizontal size={16} />
          </Button>
          <Button type="button" variant="ghost" onClick={() => setExpanded((v) => !v)}>
            {expanded ? t('Hide fields') : t('Choose fields')}
          </Button>
        </div>
      </div>
      {showDetails && <ResultDetails r={r} onClose={() => setShowDetails(false)} />}
      {expanded && (
        <div className={styles.applyPanel}>
          {fields.map((f) => (
            <label key={f.key} className={styles.applyRow}>
              <input type="checkbox" checked={sel.has(f.key)} onChange={() => toggle(f.key)} />
              <span className={styles.applyLabel}>{t(f.label)}</span>
              <span className={styles.applyPreview}>{f.key === 'cover' ? t('(replace cover)') : f.preview(r)}</span>
            </label>
          ))}
          <div className={styles.applyActions}>
            <Button type="button" disabled={sel.size === 0}
              onClick={() => { onApply(r, sel); setExpanded(false); }}>
              {t('Apply selected')}
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

/** Full-record overlay for one search result. The compact result row + per-field
 *  checklist truncate long values (identifiers, tags, description) to one line; a
 *  Hardcover edition can carry a long ISBN/edition id that must be readable in full
 *  to pick the right one. This shows every field at full length — identifiers one
 *  per line. Centered dialog on desktop, bottom sheet on mobile. */
function ResultDetails({ r, onClose }: { r: MetaResult; onClose: () => void }) {
  const t = useT();
  const modalRef = useRef<HTMLDivElement>(null);
  // Keep the latest onClose in a ref so the focus/scroll effect can run once on
  // open ([] deps) — `onClose` is a fresh closure each parent render, and using
  // it as a dep would re-run the effect on any background re-render (e.g. a search
  // resolving), re-grabbing focus mid-read and flickering the trap.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const titleId = `md-details-title-${useId()}`;

  // Accessibility: focus the dialog on open, trap Tab within it, restore focus to
  // the trigger on close, Escape closes, and lock background scroll while open
  // (mirrors CoverPicker's confirm modal, plus scroll-lock for the mobile sheet).
  useEffect(() => {
    const prevFocus = document.activeElement as HTMLElement | null;
    const node = modalRef.current;
    const focusables = () => node
      ? Array.from(node.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
        .filter((el) => !el.hasAttribute('disabled'))
      : [];
    (focusables()[0] ?? node)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onCloseRef.current(); return; }
      if (e.key !== 'Tab') return;
      const els = focusables();
      if (!els.length) return;
      const first = els[0], last = els[els.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
      prevFocus?.focus?.();
    };
  }, []);

  const idents = Object.entries(r.identifiers || {}).filter(([, v]) => v !== '' && v != null);
  const rows = [
    { label: t('Authors'), value: (r.authors || []).join(', ') },
    { label: t('Series'), value: r.series ? `${r.series}${r.series_index ? ` #${r.series_index}` : ''}` : '' },
    { label: t('Format'), value: r.format || '' },
    { label: t('Publisher'), value: r.publisher || '' },
    { label: t('Published'), value: r.publishedDate || '' },
    { label: t('Rating'), value: r.rating ? `${Math.round(r.rating)} ★` : '' },
    { label: t('Tags'), value: (r.tags || []).join(', ') },
    { label: t('Source'), value: r.source?.id || '' },
  ].filter((row) => row.value);

  return createPortal(
    <div className={styles.detailsOverlay} onClick={onClose} role="presentation">
      <div className={styles.detailsModal} onClick={(e) => e.stopPropagation()} ref={modalRef}
        role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <div className={styles.detailsHead}>
          <span className={styles.detailsTitle} id={titleId}>{r.title}</span>
          <button type="button" className={styles.detailsClose} onClick={onClose} aria-label={t('Close')}>
            <X size={18} />
          </button>
        </div>
        <div className={styles.detailsBody}>
          {r.cover && <img src={r.cover} alt="" className={styles.detailsCover} loading="lazy" />}
          <dl className={styles.detailsFields}>
            {rows.map((row) => (
              <div key={row.label} className={styles.detailsRow}>
                <dt className={styles.detailsLabel}>{row.label}</dt>
                <dd className={styles.detailsValue}>{row.value}</dd>
              </div>
            ))}
            {r.description && (
              <div className={styles.detailsRow}>
                <dt className={styles.detailsLabel}>{t('Description')}</dt>
                <dd className={styles.detailsValue}>{stripTags(r.description)}</dd>
              </div>
            )}
            {idents.length > 0 && (
              <div className={styles.detailsRow}>
                <dt className={styles.detailsLabel}>{t('Identifiers')}</dt>
                <dd className={styles.detailsValue}>
                  <ul className={styles.detailsIdents}>
                    {idents.map(([k, v]) => (
                      <li key={k}><code>{k}</code>: {String(v)}</li>
                    ))}
                  </ul>
                </dd>
              </div>
            )}
          </dl>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Replace the book cover: upload a file or paste a URL. The full provider
 *  candidate grid + e-reader padding preview lives at the legacy /book/:id/cover. */
function CoverManager({ id }: { id: string }) {
  const t = useT();
  const { data: book } = useBook(id);
  const setCover = useSetCover(id);
  const [url, setUrl] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setMsg(null);
    setCover.mutate({ file }, {
      onSuccess: () => setMsg({ ok: true, text: 'Cover updated.' }),
      onError: (err) => setMsg({ ok: false, text: err instanceof ApiError ? err.message : 'Upload failed.' }),
    });
  };

  const onUrl = () => {
    if (!url.trim()) return;
    setMsg(null);
    setCover.mutate({ url: url.trim() }, {
      onSuccess: () => { setMsg({ ok: true, text: 'Cover updated.' }); setUrl(''); },
      onError: (err) => setMsg({ ok: false, text: err instanceof ApiError ? err.message : 'Could not fetch cover.' }),
    });
  };

  return (
    <section className={styles.coverSection}>
      <div className={styles.coverPreview}>
        {book?.cover_url
          ? <img src={resourceUrl(book.cover_url)} alt={t('Current cover')} className={styles.coverImg} />
          : <div className={styles.coverPlaceholder}><ImageIcon size={28} /></div>}
      </div>
      <div className={styles.coverControls}>
        <label className={styles.coverUploadBtn}>
          <UploadIcon size={15} /> {t('Upload image')}
          <input type="file" accept="image/*" hidden onChange={onFile} disabled={setCover.isPending} />
        </label>
        <div className={styles.coverUrlRow}>
          <input className={styles.input} value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder={t('…or paste an image URL')} />
          <Button type="button" variant="ghost" onClick={onUrl} disabled={setCover.isPending || !url.trim()}>
            {t('Fetch')}
          </Button>
        </div>
        <Link className={styles.coverAdvanced} href={`/book/${id}/cover?origin=edit`}>
          <ExternalLink size={13} /> {t('More cover options (search providers, e-reader preview)')}
        </Link>
        {msg && <span className={msg.ok ? styles.msgOk : styles.msgErr}>{msg.text}</span>}
      </div>
    </section>
  );
}

/** Manage a book's files: delete a format, or queue a conversion. */
function FormatsManager({ id }: { id: string }) {
  const t = useT();
  const { data: book } = useBook(id);
  const me = useMe().data;
  const deleteFormat = useDeleteFormat(id);
  const convertFormat = useConvertFormat(id);
  const addFormat = useAddFormat(id);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const formats = book?.formats.map((f) => f.format) ?? [];
  if (!book) return null;
  const canDelete = !!me?.role?.delete_books;
  const canUpload = !!me?.role?.upload;

  const onAddFormat = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setMsg(null);
    addFormat.mutate(file, {
      onSuccess: () => setMsg({ ok: true, text: t('Format queued — it will appear once processed.') }),
      onError: (err) => setMsg({ ok: false, text: err instanceof ApiError ? err.message : 'Upload failed.' }),
    });
    e.target.value = '';
  };

  const onConvert = (e: React.FormEvent) => {
    e.preventDefault();
    setMsg(null);
    convertFormat.mutate(
      { from: from || formats[0], to: to.trim().toUpperCase() },
      {
        onSuccess: (r) => { setMsg({ ok: true, text: r.message }); setTo(''); },
        onError: (err) => setMsg({ ok: false, text: err instanceof ApiError ? err.message : 'Convert failed.' }),
      },
    );
  };

  return (
    <section className={styles.formatsSection}>
      <h2 className={styles.subTitle}>{t('Files')}</h2>
      <ul className={styles.formatList}>
        {book!.formats.map((f) => (
          <li key={f.format} className={styles.formatItem}>
            <span className={styles.formatName}>{f.format}</span>
            <a className={styles.formatDownload} href={resourceUrl(f.download_url)} download>{t('Download')}</a>
            {canDelete && (
              <button className={styles.formatDelete}
                onClick={() => {
                  if (window.confirm(`Delete the ${f.format} file? The book stays; only this format is removed.`)) {
                    deleteFormat.mutate(f.format);
                  }
                }}
                disabled={deleteFormat.isPending}
                aria-label={`Delete ${f.format}`}>
                <Trash2 size={14} />
              </button>
            )}
          </li>
        ))}
      </ul>

      {formats.length > 0 && (
        <form className={styles.convertForm} onSubmit={onConvert}>
          <label className={styles.fieldNarrow}>
            <span className={styles.label}>{t('Convert from')}</span>
            <select className={styles.inputNarrow} value={from || formats[0]} onChange={(e) => setFrom(e.target.value)}>
              {formats.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          <label className={styles.fieldNarrow}>
            <span className={styles.label}>{t('to')}</span>
            <input className={styles.inputNarrow} value={to} onChange={(e) => setTo(e.target.value)}
              placeholder="e.g. MOBI" />
          </label>
          <Button type="submit" variant="ghost" disabled={convertFormat.isPending || !to.trim()}>
            <RefreshCw size={15} /> {t('Convert')}
          </Button>
        </form>
      )}

      {canUpload && (
        <label className={styles.coverUploadBtn} style={{ marginTop: 'var(--sp-3)' }}>
          <UploadIcon size={15} /> {addFormat.isPending ? t('Uploading…') : t('Add a format')}
          <input type="file" hidden onChange={onAddFormat} disabled={addFormat.isPending} />
        </label>
      )}
      {msg && <span className={msg.ok ? styles.msgOk : styles.msgErr}>{msg.text}</span>}
    </section>
  );
}

function Field({ label, error, grow = true, children }:
  { label: string; error?: string; grow?: boolean; children: React.ReactNode }) {
  return (
    <label className={grow ? styles.field : styles.fieldNarrow}>
      <span className={styles.label}>{label}</span>
      {children}
      {error && <span className={styles.fieldError}>{error}</span>}
    </label>
  );
}
