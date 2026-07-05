import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Link } from 'wouter';
import {
  ChevronLeft, Lock, Unlock, Upload as UploadIcon, Link2, RefreshCw, Check, X,
  Image as ImageIcon, AlertTriangle, KeyRound, Smartphone, Loader2, Sparkles,
} from 'lucide-react';
import { useBook } from '../lib/queries';
import {
  useCoverState, useCandidates, useProviderKeys, coverApi,
  EREADER_ASPECTS, EREADER_FILL_MODES,
  type CoverCandidate, type ProviderStatus, type UrlValidation,
  type EreaderOptions, type ProviderKey,
} from '../lib/coverPicker';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '../components/Button';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { ApiError, resourceUrl } from '../lib/api';
import { useT } from '../lib/i18n';
import styles from './CoverPicker.module.css';

type Banner = { ok: boolean; text: string } | null;
const candKey = (c: CoverCandidate) => c.candidate_id ?? `${c.source_id}:${c.cover_url}`;
const isEmbedded = (c: CoverCandidate) => c.source_id === 'embedded' || c.candidate_id === 'embedded';

export function CoverPicker({ id }: { id: string }) {
  const t = useT();
  const qc = useQueryClient();
  const { data: book } = useBook(id);
  const { data: state } = useCoverState(id);
  const candidatesQ = useCandidates(id);

  const [locked, setLocked] = useState(false);
  const [coverBust, setCoverBust] = useState<string | null>(null);
  const [banner, setBanner] = useState<Banner>(null);
  const [confirm, setConfirm] = useState<CoverCandidate | null>(null);
  const [ereaderState, setEreaderState] = useState<EreaderState>({
    enabled: false, aspect: 'kobo_libra_color', fill_mode: 'edge_mirror', color: '',
  });

  useEffect(() => { if (state) setLocked(state.locked); }, [state]);
  useEffect(() => {
    if (state?.ereader_defaults) {
      setEreaderState((s) => ({ ...s, ...state.ereader_defaults }));
    }
  }, [state]);

  const back = useBackTarget(id);

  // Current cover, cache-busted after an apply. resourceUrl() applies the
  // reverse-proxy mount prefix (both branches are server /cover/… resources).
  const currentCover = useMemo(() => {
    const raw = coverBust ?? book?.cover_url ?? null;
    return raw ? resourceUrl(raw) : null;
  }, [book?.cover_url, coverBust]);

  const onApplied = useCallback((coverUrl?: string) => {
    setCoverBust(coverUrl ?? `/cover/${id}/og?ts=${Date.now()}`);
    setBanner({ ok: true, text: t('Cover updated.') });
    qc.invalidateQueries({ queryKey: ['book', id] });
    qc.invalidateQueries({ queryKey: ['book-meta', id] });
  }, [id, qc, t]);

  const onError = useCallback((err: unknown) => {
    setBanner({ ok: false, text: err instanceof ApiError ? err.message : t('Something went wrong. Try again.') });
  }, [t]);

  const toggleLock = async () => {
    const next = !locked;
    setLocked(next); // optimistic
    try {
      const r = await coverApi.setLock(id, next);
      setLocked(r.locked);
    } catch (e) { setLocked(!next); onError(e); }
  };

  if (!book) return <main className={styles.container}><SpinnerCentered /></main>;

  return (
    <main className={styles.container}>
      <header className={styles.header}>
        <Link href={back.href} className={styles.back}>
          <ChevronLeft size={16} /> {back.label}
        </Link>
        <h1 className={styles.title}>{t('Change cover')}</h1>
        <p className={styles.subtitle}>
          {t('Pick a cover from any source we support, paste a URL, upload a file, or use the cover embedded in the book itself.')}
        </p>
      </header>

      {banner && (
        <div className={banner.ok ? styles.bannerOk : styles.bannerErr} role="status">
          {banner.ok ? <Check size={15} /> : <AlertTriangle size={15} />}
          <span>{banner.text}</span>
          <button className={styles.bannerClose} onClick={() => setBanner(null)} aria-label={t('Dismiss')}><X size={14} /></button>
        </div>
      )}

      <div className={styles.layout}>
        <aside className={styles.rail}>
          <div className={styles.currentCard}>
            <div className={styles.cardLabel}>{t('Current cover')}</div>
            <div className={styles.currentFrame}>
              {currentCover
                ? <img src={currentCover} alt={book.title} className={styles.currentImg}
                       onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }} />
                : <div className={styles.currentFallback}><ImageIcon size={30} /></div>}
            </div>
            <div className={styles.currentMeta}>
              <strong>{book.title}</strong>
              {book.authors?.length ? <span>{book.authors.map((a) => a.name).join(', ')}</span> : null}
            </div>

            <button className={`${styles.lockToggle} ${locked ? styles.lockOn : ''}`} onClick={toggleLock}
                    type="button" aria-pressed={locked}>
              <span className={styles.lockKnob}>{locked ? <Lock size={13} /> : <Unlock size={13} />}</span>
              <span>{locked ? t('Cover locked') : t('Lock cover')}</span>
            </button>
            <p className={styles.lockHelp}>
              {t('When locked, fetching metadata will not overwrite this cover.')}
            </p>
          </div>

          <AddOwnPanel id={id} locked={locked} onApplied={onApplied} onError={onError} />
        </aside>

        <section className={styles.main}>
          {state?.ereader_enabled && (
            <EreaderPanel onChange={setEreaderState} value={ereaderState} />
          )}
          <ApiKeysPanel />

          <div className={styles.gridToolbar}>
            <h2 className={styles.gridTitle}>{t('Choose a cover')}</h2>
            <ProviderSummary providers={candidatesQ.data?.providers} loading={candidatesQ.isFetching} />
            <Button variant="ghost" size="sm" onClick={() => candidatesQ.refetch()} disabled={candidatesQ.isFetching}>
              <RefreshCw size={14} className={candidatesQ.isFetching ? styles.spin : ''} /> {t('Refresh')}
            </Button>
          </div>

          {candidatesQ.isLoading ? (
            <div className={styles.gridLoading}><Loader2 size={22} className={styles.spin} /> {t('Searching every source…')}</div>
          ) : candidatesQ.isError ? (
            <EmptyState message={candidatesQ.error instanceof Error ? candidatesQ.error.message : t('Could not load candidates.')} />
          ) : (
            <CandidateGrid
              id={id}
              candidates={candidatesQ.data?.candidates ?? []}
              locked={locked}
              ereader={ereaderState}
              onPick={setConfirm}
            />
          )}

          <ProviderDetail providers={candidatesQ.data?.providers} />
        </section>
      </div>

      {confirm && (
        <ConfirmModal
          id={id}
          candidate={confirm}
          currentCover={currentCover}
          onClose={() => setConfirm(null)}
          onApplied={(url) => { onApplied(url); setConfirm(null); }}
          onError={(e) => { onError(e); setConfirm(null); }}
        />
      )}
    </main>
  );
}

// ============================================================================
// E-reader settings + live preview wiring
// ============================================================================

interface EreaderState extends EreaderOptions { enabled: boolean }

function EreaderPanel({ value, onChange }: {
  value: EreaderState;
  onChange: (s: EreaderState) => void;
}) {
  const t = useT();
  const set = (patch: Partial<EreaderState>) => onChange({ ...value, ...patch });
  return (
    <details className={styles.panel}>
      <summary className={styles.panelSummary}>
        <Smartphone size={15} /> {t('E-reader preview')}
        <span className={styles.panelHint}>{t('See how each cover looks padded for your device')}</span>
      </summary>
      <div className={styles.panelBody}>
        <label className={styles.switchRow}>
          <input type="checkbox" checked={value.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
          <span>{t('Show e-reader previews on each candidate')}</span>
        </label>
        <div className={styles.ereaderGrid}>
          <label className={styles.field}>
            <span>{t('Target aspect ratio')}</span>
            <select value={value.aspect} onChange={(e) => set({ aspect: e.target.value })}>
              {EREADER_ASPECTS.map((o) => <option key={o.value} value={o.value}>{t(o.label)}</option>)}
            </select>
          </label>
          <label className={styles.field}>
            <span>{t('Border fill style')}</span>
            <select value={value.fill_mode} onChange={(e) => set({ fill_mode: e.target.value })}>
              {EREADER_FILL_MODES.map((o) => <option key={o.value} value={o.value}>{t(o.label)}</option>)}
            </select>
          </label>
          {value.fill_mode === 'manual' && (
            <label className={styles.field}>
              <span>{t('Custom border colour')}</span>
              <input type="text" maxLength={9} placeholder="#1a1a1a" value={value.color}
                     onChange={(e) => set({ color: e.target.value })} />
            </label>
          )}
        </div>
        <p className={styles.panelNote}>{t('Defaults live in Admin → Configuration → Kobo sync.')}</p>
      </div>
    </details>
  );
}

/** Per-candidate e-reader render with a generation guard, concurrency cap and a
 *  settings-keyed cache — mirrors the legacy picker's behaviour. */
function useEreaderPreviews(id: string, candidates: CoverCandidate[], s: EreaderState) {
  const [previews, setPreviews] = useState<Record<string, string | 'loading'>>({});
  const cache = useRef<Map<string, string>>(new Map());
  const gen = useRef(0);

  const settingsKey = `${s.aspect}|${s.fill_mode}|${s.fill_mode === 'manual' ? s.color : ''}`;

  useEffect(() => {
    if (!s.enabled) { setPreviews({}); return; }
    const myGen = ++gen.current;
    let cancelled = false;
    const queue = [...candidates];
    const MAX = 6;

    const renderOne = async (c: CoverCandidate) => {
      const key = `${candKey(c)}|${c.cover_url}|${settingsKey}`;
      const cached = cache.current.get(key);
      if (cached) { if (!cancelled) setPreviews((p) => ({ ...p, [candKey(c)]: cached })); return; }
      setPreviews((p) => ({ ...p, [candKey(c)]: 'loading' }));
      try {
        const opts: EreaderOptions & { candidate_url?: string; embedded?: boolean } = {
          aspect: s.aspect, fill_mode: s.fill_mode, color: s.color,
        };
        if (isEmbedded(c)) opts.embedded = true;
        else if (c.cover_url) opts.candidate_url = c.cover_url;
        const r = await coverApi.ereaderPreview(id, opts);
        if (myGen !== gen.current) return; // settings changed mid-flight
        if (r.ok && r.data_url) {
          cache.current.set(key, r.data_url);
          if (!cancelled) setPreviews((p) => ({ ...p, [candKey(c)]: r.data_url }));
        } else if (!cancelled) {
          setPreviews((p) => { const n = { ...p }; delete n[candKey(c)]; return n; });
        }
      } catch {
        if (!cancelled) setPreviews((p) => { const n = { ...p }; delete n[candKey(c)]; return n; });
      }
    };

    const workers = Array.from({ length: Math.min(MAX, queue.length) }, async () => {
      while (queue.length && myGen === gen.current) { const c = queue.shift(); if (c) await renderOne(c); }
    });
    Promise.all(workers).catch(() => {});
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, s.enabled, settingsKey, candidates]);

  return s.enabled ? previews : {};
}

// ============================================================================
// Candidate grid + cards
// ============================================================================

function CandidateGrid({ id, candidates, locked, ereader, onPick }: {
  id: string; candidates: CoverCandidate[]; locked: boolean;
  ereader: EreaderState; onPick: (c: CoverCandidate) => void;
}) {
  const t = useT();
  const previews = useEreaderPreviews(id, candidates, ereader);
  if (!candidates.length) {
    return <EmptyState message={t('No candidates found yet. Try a different search or refresh.')} />;
  }
  return (
    <div className={styles.grid}>
      {candidates.map((c) => (
        <CandidateCard key={candKey(c)} candidate={c} locked={locked}
          preview={previews[candKey(c)]} onPick={() => onPick(c)} />
      ))}
    </div>
  );
}

function CandidateCard({ candidate: c, locked, preview, onPick }: {
  candidate: CoverCandidate; locked: boolean; preview?: string | 'loading'; onPick: () => void;
}) {
  const t = useT();
  const [failed, setFailed] = useState(false);
  const showPreview = preview && preview !== 'loading';
  const src = showPreview ? (preview as string) : c.cover_url;
  // A refresh can reuse this component instance (same key) with a new image URL;
  // clear a prior load error so the new image mounts instead of staying hidden.
  useEffect(() => { setFailed(false); }, [src]);
  const dims = c.width && c.height ? `${c.width}×${c.height}` : null;
  const lowRes = c.flags?.includes('low_res');
  return (
    <button type="button" className={`${styles.card} ${locked ? styles.cardLocked : ''}`}
            onClick={onPick} disabled={locked} title={locked ? t('Unlock the cover to change it') : undefined}>
      <div className={styles.cardImgWrap}>
        {failed
          ? <div className={styles.cardFailed}><ImageIcon size={22} /><span>{t('Cover not reachable')}</span></div>
          : <img src={src} alt={c.title || c.source_name} loading="lazy" className={styles.cardImg}
                 onError={() => setFailed(true)} />}
        {preview === 'loading' && <div className={styles.cardShimmer}><Loader2 size={18} className={styles.spin} /></div>}
        {isEmbedded(c) && <span className={styles.badgeEmbedded}>{t('In your book')}</span>}
        {showPreview && <span className={styles.badgeEreader}><Smartphone size={11} /> {t('e-reader')}</span>}
        {lowRes && <span className={styles.badgeWarn}><AlertTriangle size={11} /> {t('Low-res')}</span>}
      </div>
      <div className={styles.cardInfo}>
        <span className={styles.cardSource}>{c.source_name}</span>
        <span className={styles.cardDims}>{dims || (c.year ? c.year : ' ')}</span>
      </div>
    </button>
  );
}

// ============================================================================
// Provider status
// ============================================================================

function ProviderSummary({ providers, loading }: { providers?: ProviderStatus[]; loading: boolean }) {
  const t = useT();
  if (loading && !providers?.length) return <span className={styles.provSummary}>{t('Searching…')}</span>;
  if (!providers?.length) return <span className={styles.provSummary} />;
  const ok = providers.filter((p) => p.status === 'ok').length;
  const total = providers.length;
  return <span className={styles.provSummary}>{t('{ok} of {total} sources answered', { ok, total })}</span>;
}

function ProviderDetail({ providers }: { providers?: ProviderStatus[] }) {
  const t = useT();
  if (!providers?.length) return null;
  return (
    <details className={styles.provPanel}>
      <summary className={styles.provPanelSummary}>{t('Source details')}</summary>
      <ul className={styles.provList}>
        {providers.map((p) => (
          <li key={p.id} className={styles.provRow}>
            <span className={styles.provPill} data-status={p.status}>{p.status}</span>
            <span className={styles.provName}>{p.name}</span>
            <span className={styles.provCount}>{p.count ? t('{n} found', { n: p.count }) : (p.message || '—')}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

// ============================================================================
// "Add your own" — URL + upload tabs
// ============================================================================

function AddOwnPanel({ id, locked, onApplied, onError }: {
  id: string; locked: boolean; onApplied: (url?: string) => void; onError: (e: unknown) => void;
}) {
  const t = useT();
  const [tab, setTab] = useState<'url' | 'upload'>('url');
  // Tab keyboard contract (APG): Left/Right/Home/End move + activate, and focus
  // follows the selection. Only the selected tab is in the tab order (roving).
  const onTabKey = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    const next = tab === 'url' ? 'upload' : 'url';
    setTab(next);
    const el = e.currentTarget.parentElement?.querySelector<HTMLElement>(`#cp-tab-${next}`);
    el?.focus();
  };
  return (
    <div className={styles.addOwn}>
      <div className={styles.cardLabel}>{t('Add your own')}</div>
      <div className={styles.tabs} role="tablist" aria-label={t('Add your own')}>
        <button role="tab" id="cp-tab-url" aria-controls="cp-panel-url" aria-selected={tab === 'url'}
                tabIndex={tab === 'url' ? 0 : -1} onKeyDown={onTabKey}
                className={tab === 'url' ? styles.tabOn : styles.tab}
                onClick={() => setTab('url')}><Link2 size={14} aria-hidden="true" focusable={false} /> {t('Paste URL')}</button>
        <button role="tab" id="cp-tab-upload" aria-controls="cp-panel-upload" aria-selected={tab === 'upload'}
                tabIndex={tab === 'upload' ? 0 : -1} onKeyDown={onTabKey}
                className={tab === 'upload' ? styles.tabOn : styles.tab}
                onClick={() => setTab('upload')}><UploadIcon size={14} aria-hidden="true" focusable={false} /> {t('Upload')}</button>
      </div>
      <div role="tabpanel"
           id={tab === 'url' ? 'cp-panel-url' : 'cp-panel-upload'}
           aria-labelledby={tab === 'url' ? 'cp-tab-url' : 'cp-tab-upload'}>
        {tab === 'url'
          ? <UrlTab id={id} locked={locked} onApplied={onApplied} onError={onError} />
          : <UploadTab id={id} locked={locked} onApplied={onApplied} onError={onError} />}
      </div>
    </div>
  );
}

function UrlTab({ id, locked, onApplied, onError }: {
  id: string; locked: boolean; onApplied: (url?: string) => void; onError: (e: unknown) => void;
}) {
  const t = useT();
  const [url, setUrl] = useState('');
  const [valid, setValid] = useState<UrlValidation | null>(null);
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState(false);
  const seq = useRef(0); // ignore stale validation responses that resolve out of order

  useEffect(() => {
    const v = url.trim();
    if (!v) { setValid(null); setChecking(false); return; }
    setChecking(true);
    const mySeq = ++seq.current;
    const h = setTimeout(async () => {
      try { const r = await coverApi.validate(id, v); if (mySeq === seq.current) setValid(r); }
      catch { if (mySeq === seq.current) setValid(null); }
      finally { if (mySeq === seq.current) setChecking(false); }
    }, 400);
    return () => clearTimeout(h);
  }, [url, id]);

  const apply = async () => {
    // Guard against applying a URL that's no longer the one shown/validated.
    if (!valid?.valid || checking || valid?.url !== url.trim() || locked) return;
    setApplying(true);
    try { const r = await coverApi.applyUrl(id, valid.url); onApplied(r.cover_url); setUrl(''); setValid(null); }
    catch (e) { onError(e); }
    finally { setApplying(false); }
  };

  return (
    <div className={styles.tabBody}>
      <input className={styles.input} value={url} onChange={(e) => setUrl(e.target.value)}
             placeholder="https://…" inputMode="url" aria-label={t('Cover image URL')} />
      {checking && <div className={styles.feedbackMuted}>{t('Checking…')}</div>}
      {!checking && valid && !valid.valid && (
        <div className={styles.feedbackErr}>{valid.error_message || t('That URL is not a usable image.')}</div>
      )}
      {!checking && valid?.valid && (
        <div className={styles.urlOk}>
          <img src={valid.url} alt="" className={styles.urlThumb} />
          <div className={styles.urlMeta}>
            <span className={styles.feedbackOk}><Check size={13} /> {t('Looks good')}</span>
            {valid.width && valid.height ? <span>{valid.width}×{valid.height}</span> : null}
          </div>
        </div>
      )}
      <Button onClick={apply} disabled={!valid?.valid || checking || valid?.url !== url.trim() || locked || applying} className={styles.fullBtn}>
        {applying ? <Loader2 size={14} className={styles.spin} /> : <Check size={14} />} {t('Use this cover')}
      </Button>
      {locked && <p className={styles.lockedHint}>{t('Unlock the cover above to apply a new one.')}</p>}
    </div>
  );
}

function UploadTab({ id, locked, onApplied, onError }: {
  id: string; locked: boolean; onApplied: (url?: string) => void; onError: (e: unknown) => void;
}) {
  const t = useT();
  const [file, setFile] = useState<File | null>(null);
  const [applying, setApplying] = useState(false);
  const apply = async () => {
    if (!file || locked) return;
    setApplying(true);
    try { const r = await coverApi.applyFile(id, file); onApplied(r.cover_url); setFile(null); }
    catch (e) { onError(e); }
    finally { setApplying(false); }
  };
  return (
    <div className={styles.tabBody}>
      <label className={styles.dropZone}>
        <UploadIcon size={18} aria-hidden="true" focusable={false} />
        <span>{file ? file.name : t('Choose an image…')}</span>
        {/* C3: sr-only keeps the input focusable + in tab order (not hidden). */}
        <input type="file" accept=".jpg,.jpeg,.png,.webp,.bmp,.gif" className={styles.fileInput}
               aria-label={t('Choose a cover image to upload')}
               onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </label>
      <Button onClick={apply} disabled={!file || locked || applying} className={styles.fullBtn}>
        {applying ? <Loader2 size={14} className={styles.spin} /> : <UploadIcon size={14} />} {t('Upload as cover')}
      </Button>
      {locked && <p className={styles.lockedHint}>{t('Unlock the cover above to apply a new one.')}</p>}
    </div>
  );
}

// ============================================================================
// API keys (admin)
// ============================================================================

function ApiKeysPanel() {
  const t = useT();
  const [open, setOpen] = useState(false);
  const keysQ = useProviderKeys(open);
  return (
    <details className={styles.panel} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className={styles.panelSummary}>
        <KeyRound size={15} /> {t('API keys')}
        <span className={styles.panelHint}>{t('Some sources need a key for better covers')}</span>
      </summary>
      <div className={styles.panelBody}>
        {keysQ.isLoading ? <div className={styles.feedbackMuted}>{t('Loading…')}</div>
          : !keysQ.data?.length ? <div className={styles.feedbackMuted}>{t('No sources need a key.')}</div>
          : <ul className={styles.keysList}>{keysQ.data.map((k) => <KeyRow key={k.id} k={k} />)}</ul>}
      </div>
    </details>
  );
}

function KeyRow({ k }: { k: ProviderKey }) {
  const t = useT();
  const [val, setVal] = useState('');
  const [configured, setConfigured] = useState(k.configured);
  const [saving, setSaving] = useState(false);
  const save = async () => {
    setSaving(true);
    try { const r = await coverApi.saveKey(k.id, val); setConfigured(r.configured); setVal(''); }
    catch { /* surfaced inline below via title */ }
    finally { setSaving(false); }
  };
  return (
    <li className={styles.keyRow}>
      <span className={styles.keyName}>{k.name}</span>
      <span className={configured ? styles.keyOn : styles.keyOff}>
        {configured ? t('Configured') : t('Not configured')}
      </span>
      {k.can_edit && (
        <>
          <input className={styles.keyInput} type="password" value={val} placeholder="••••••"
                 aria-label={t('{name} API key', { name: k.name })}
                 onChange={(e) => setVal(e.target.value)} />
          <Button size="sm" variant="ghost" onClick={save} disabled={saving || !val}>{t('Save')}</Button>
        </>
      )}
    </li>
  );
}

// ============================================================================
// Confirm modal
// ============================================================================

function ConfirmModal({ id, candidate: c, currentCover, onClose, onApplied, onError }: {
  id: string; candidate: CoverCandidate; currentCover: string | null;
  onClose: () => void; onApplied: (url?: string) => void; onError: (e: unknown) => void;
}) {
  const t = useT();
  const [applying, setApplying] = useState(false);
  const modalRef = useRef<HTMLDivElement>(null);

  // Accessibility: focus the dialog on open, trap Tab within it, and restore
  // focus to the trigger on close. Escape closes.
  useEffect(() => {
    const prevFocus = document.activeElement as HTMLElement | null;
    const node = modalRef.current;
    const focusables = () => node
      ? Array.from(node.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
        .filter((el) => !el.hasAttribute('disabled'))
      : [];
    // Focus the confirm (last) action so Enter applies; falls back to the dialog.
    const f = focusables();
    (f[f.length - 1] ?? node)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return; }
      if (e.key !== 'Tab') return;
      const els = focusables();
      if (!els.length) return;
      const first = els[0], last = els[els.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); prevFocus?.focus?.(); };
  }, [onClose]);

  const apply = async () => {
    setApplying(true);
    try {
      const r = isEmbedded(c) ? await coverApi.applyEmbedded(id) : await coverApi.applyUrl(id, c.cover_url);
      if (r.ok) onApplied(r.cover_url); else onError(new ApiError(400, r.error_message || t('Cover save failed.')));
    } catch (e) { onError(e); }
    finally { setApplying(false); }
  };

  return (
    <div className={styles.overlay} onClick={onClose} role="presentation">
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}
           ref={modalRef} role="dialog" aria-modal="true" aria-label={t('Replace cover?')} tabIndex={-1}>
        <div className={styles.modalHead}>
          <h3>{t('Replace cover with this one?')}</h3>
          <button className={styles.modalClose} onClick={onClose} aria-label={t('Close')}><X size={18} /></button>
        </div>
        <div className={styles.compare}>
          <figure>
            <figcaption>{t('Current')}</figcaption>
            <div className={styles.compareFrame}>
              {currentCover ? <img src={currentCover} alt="" /> : <div className={styles.currentFallback}><ImageIcon size={26} /></div>}
            </div>
          </figure>
          <div className={styles.compareArrow}><Sparkles size={18} /></div>
          <figure>
            <figcaption>{t('New')}</figcaption>
            <div className={styles.compareFrame}>
              <img src={c.cover_url} alt={c.title || c.source_name} />
            </div>
            <div className={styles.compareMeta}>
              <strong>{c.source_name}</strong>
              {c.title ? <span>{c.title}{c.year ? ` (${c.year})` : ''}</span> : null}
              {c.width && c.height ? <span>{c.width}×{c.height}</span> : null}
            </div>
          </figure>
        </div>
        <div className={styles.modalFoot}>
          <Button variant="ghost" onClick={onClose}>{t('Cancel')}</Button>
          <Button onClick={apply} disabled={applying}>
            {applying ? <Loader2 size={14} className={styles.spin} /> : <Check size={14} />} {t('Use this cover')}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// helpers
// ============================================================================

/** The picker returns the user to where they came from: the edit page on
 *  ?origin=edit, otherwise the book detail page (fork #26). */
function useBackTarget(id: string) {
  const t = useT();
  const origin = new URLSearchParams(window.location.search).get('origin');
  return origin === 'edit'
    ? { href: `/book/${id}/edit`, label: t('Back to edit metadata') }
    : { href: `/book/${id}`, label: t('Back to book') };
}
