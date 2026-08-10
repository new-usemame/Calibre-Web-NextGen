import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'wouter';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, Download, Highlighter, Upload as UploadIcon } from 'lucide-react';
import { apiGet, apiPatch, apiPost, apiUrl } from '../lib/api';
import { useBook } from '../lib/queries';
import { useAnnouncer } from '../lib/a11y/announcer';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { BulkSelectionBar } from '../components/BulkBar';
import { VirtualizedList } from '../components/VirtualizedList';
import { useT } from '../lib/i18n';
import styles from './Annotations.module.css';

interface Annotation {
  annotation_id: string; highlighted_text: string; highlight_color: string | null;
  note_text: string | null; chapter_progress: number | null; source: string | null;
  origin_device_id: string | null; assigned_device_id: string | null;
}
interface DeviceSummary { label: string; model: string | null; type: string }
interface ActiveDevice extends DeviceSummary { public_id: string; active: boolean }
interface Payload { annotations: Annotation[]; annotation_count: number; devices: Record<string, DeviceSummary> }
type Entry = { kind: 'annotation'; annotation: Annotation } | { kind: 'group'; id: string | null; label: string; count: number };

const COLOR_HEX: Record<string, string> = { yellow: '#e6c34a', red: '#d9534f', green: '#5cb85c', blue: '#5b9bd5' };
const CHUNK_SIZE = 500;

function chunks<T>(items: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let index = 0; index < items.length; index += size) out.push(items.slice(index, index + size));
  return out;
}

export function Annotations({ id }: { id: string }) {
  const t = useT();
  const announce = useAnnouncer();
  const book = useBook(id).data;
  const { data, isLoading, error } = useQuery<Payload>({
    queryKey: ['annotations', id], queryFn: () => apiGet(`/annotations/${id}/data.json`),
  });
  const { data: registry } = useQuery<{ devices: ActiveDevice[] }>({
    queryKey: ['annotation-devices'], queryFn: () => apiGet('/api/annotations/devices?active=true'),
  });
  const [filter, setFilter] = useState<string>('all');
  const [group, setGroup] = useState<'book' | 'device'>('book');
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [assignments, setAssignments] = useState<Record<string, string | null>>({});
  const [failed, setFailed] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [toast, setToast] = useState<{ text: string; failed?: number; undo?: Record<string, string | null>; target?: string | null } | null>(null);
  const filterFirstRef = useRef<HTMLButtonElement>(null);
  const selectAllRef = useRef<HTMLButtonElement>(null);
  const longPressRef = useRef<{ timer: ReturnType<typeof setTimeout>; annotationId: string } | null>(null);
  const suppressClickRef = useRef<string | null>(null);

  // Wouter reuses this component for /book/A/annotations → /book/B/annotations.
  // Reset all book-local state explicitly so selection and undo cannot leak.
  useEffect(() => {
    setFilter('all'); setGroup('book'); setSelecting(false); setSelected(new Set());
    setAssignments({}); setFailed(new Set()); setToast(null); setProgress(null);
  }, [id]);
  useEffect(() => {
    if (!data) return;
    setAssignments(Object.fromEntries(data.annotations.map((row) => [row.annotation_id, row.assigned_device_id])));
  }, [data]);
  useEffect(() => {
    if (selecting) selectAllRef.current?.focus();
  }, [selecting]);

  const annotations = data?.annotations ?? [];
  const devices = data?.devices ?? {};
  const activeDevices = registry?.devices ?? [];
  const assignmentOf = (row: Annotation) => assignments[row.annotation_id] ?? null;
  const deviceLabel = (deviceId: string | null) => deviceId ? devices[deviceId]?.label || t('Deleted device') : t('Unknown device');
  const counts = useMemo(() => {
    const result = new Map<string, number>();
    annotations.forEach((row) => {
      const key = assignments[row.annotation_id] || 'unknown';
      result.set(key, (result.get(key) || 0) + 1);
    });
    return result;
  }, [annotations, assignments]);
  const filters = useMemo(() => [
    { id: 'all', label: t('All'), count: annotations.length },
    { id: 'unknown', label: t('Unknown device'), count: counts.get('unknown') || 0 },
    ...Object.entries(devices).map(([deviceId, device]) => ({ id: deviceId, label: device.label, count: counts.get(deviceId) || 0 })),
  ].filter((item) => item.id === 'all' || item.count > 0), [annotations.length, counts, devices, t]);
  const filtered = annotations.filter((row) => filter === 'all' || (filter === 'unknown' ? !assignmentOf(row) : assignmentOf(row) === filter));
  const entries = useMemo<Entry[]>(() => {
    if (group === 'book') return filtered.map((annotation) => ({ kind: 'annotation', annotation }));
    const grouped = new Map<string, Annotation[]>();
    filtered.forEach((row) => {
      const key = assignmentOf(row) || 'unknown';
      grouped.set(key, [...(grouped.get(key) || []), row]);
    });
    const order = [...grouped.keys()].sort((a, b) => a === 'unknown' ? -1 : b === 'unknown' ? 1 : deviceLabel(a).localeCompare(deviceLabel(b)));
    return order.flatMap((key) => [
      { kind: 'group' as const, id: key === 'unknown' ? null : key, label: deviceLabel(key === 'unknown' ? null : key), count: grouped.get(key)!.length },
      ...grouped.get(key)!.map((annotation) => ({ kind: 'annotation' as const, annotation })),
    ]);
  }, [filtered, group, assignments, devices]);

  const setOneAssignment = async (row: Annotation, next: string | null) => {
    const previous = assignmentOf(row);
    setAssignments((current) => ({ ...current, [row.annotation_id]: next }));
    try {
      await apiPatch(`/annotations/${id}/${encodeURIComponent(row.annotation_id)}`, { assigned_device_id: next });
      const label = deviceLabel(next);
      setToast({ text: t('Assigned to {name}.', { name: label }), undo: { [row.annotation_id]: previous } });
      announce(t('Assigned to {name}.', { name: label }));
    } catch {
      setAssignments((current) => ({ ...current, [row.annotation_id]: previous }));
      announce(t('Could not assign.'));
    }
  };

  const applyBulk = async (next: string | null, ids = [...selected]) => {
    if (!ids.length) return;
    const previous = Object.fromEntries(ids.map((annotationId) => [annotationId, assignments[annotationId] ?? null]));
    setBusy(true); setProgress({ done: 0, total: ids.length }); setFailed(new Set());
    setAssignments((current) => ({ ...current, ...Object.fromEntries(ids.map((annotationId) => [annotationId, next])) }));
    const failures = new Set<string>();
    let done = 0;
    for (const batch of chunks(ids, CHUNK_SIZE)) {
      try {
        const response = await apiPost<{ results: { annotation_id: string; ok: boolean; error_code?: string }[] }>(
          '/api/annotations/assignments/bulk',
          { assigned_device_id: next, items: batch.map((annotation_id) => ({ book_id: Number(id), annotation_id })) },
        );
        response.results.forEach((result) => { if (!result.ok) failures.add(result.annotation_id); });
      } catch { batch.forEach((annotationId) => failures.add(annotationId)); }
      done += batch.length; setProgress({ done, total: ids.length });
    }
    setAssignments((current) => ({ ...current, ...Object.fromEntries([...failures].map((annotationId) => [annotationId, previous[annotationId]])) }));
    setSelected(new Set(failures)); setFailed(failures); setBusy(false); setProgress(null);
    const succeeded = ids.length - failures.size;
    const label = deviceLabel(next);
    setToast({
      text: failures.size ? t('{ok} of {total} assigned to {name}.', { ok: succeeded, total: ids.length, name: label }) : t('{n} assigned to {name}.', { n: succeeded, name: label }),
      failed: failures.size,
      target: next,
      undo: Object.fromEntries(Object.entries(previous).filter(([annotationId]) => !failures.has(annotationId))),
    });
    announce(failures.size ? t('{n} failed.', { n: failures.size }) : t('{n} assigned to {name}.', { n: succeeded, name: label }));
  };

  const undo = async () => {
    const previous = toast?.undo;
    if (!previous) return;
    setToast(null);
    const byTarget = new Map<string, string[]>();
    Object.entries(previous).forEach(([annotationId, target]) => {
      const key = target || 'unknown'; byTarget.set(key, [...(byTarget.get(key) || []), annotationId]);
    });
    for (const [target, idsToRestore] of byTarget) await applyBulk(target === 'unknown' ? null : target, idsToRestore);
    setToast(null); setSelected(new Set()); setFailed(new Set());
  };

  if (isLoading) return <SpinnerCentered size={40} />;
  const colorName = (color: string | null) => ({ yellow: t('Yellow'), red: t('Red'), green: t('Green'), blue: t('Blue') })[color || 'yellow'] || t('Yellow');
  const sourceLabel = (source: string | null) => source === 'kobo' ? t('Kobo') : source === 'koreader' ? t('KOReader') : source === 'webreader' ? t('Web reader') : t('Unknown source');
  const toggle = (annotationId: string) => setSelected((current) => {
    const next = new Set(current); if (next.has(annotationId)) next.delete(annotationId); else next.add(annotationId); return next;
  });
  const startLongPress = (annotationId: string, pointerType: string) => {
    if (selecting || !pointerType) return;
    const timer = setTimeout(() => {
      suppressClickRef.current = annotationId;
      setSelecting(true);
      setSelected(new Set([annotationId]));
      announce(t('1 selected.'));
      longPressRef.current = null;
    }, 550);
    longPressRef.current = { timer, annotationId };
  };
  const cancelLongPress = () => {
    if (longPressRef.current) clearTimeout(longPressRef.current.timer);
    longPressRef.current = null;
  };

  return (
    <main className={styles.container}>
      <Link href={`/book/${id}`} className={styles.back}><ChevronLeft size={16} aria-hidden="true" /> {t('Back to book')}</Link>
      <div className={styles.header}><Highlighter size={22} aria-hidden="true" /><h1>{t('Highlights')}{book ? ` — ${book.title}` : ''}</h1><span>{annotations.length}</span></div>
      <div className={`${styles.filters} ${filters.length > 7 ? styles.filtersCollapsed : ''}`} role="radiogroup" aria-label={t('Filter by device')}
        onKeyDown={(event) => {
          if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
          event.preventDefault(); const index = filters.findIndex((item) => item.id === filter);
          const nextIndex = (index + (event.key === 'ArrowRight' ? 1 : -1) + filters.length) % filters.length;
          setFilter(filters[nextIndex].id);
          event.currentTarget.querySelectorAll<HTMLButtonElement>('button')[nextIndex]?.focus();
        }}>
        {filters.map((item, index) => <button key={item.id} ref={index === 0 ? filterFirstRef : undefined} type="button" role="radio"
          aria-checked={filter === item.id} tabIndex={filter === item.id ? 0 : -1}
          aria-label={t('{name}, {n} highlights', { name: item.label, n: item.count })}
          className={filter === item.id ? styles.filterActive : styles.filter}
          onClick={() => { setFilter(item.id); setSelected(new Set()); }}>{item.label} <span>{item.count}</span></button>)}
      </div>
      <label className={`${styles.mobileFilter} ${filters.length > 7 ? styles.desktopFilterSelect : ''}`}>{t('Device')}
        <select value={filter} onChange={(event) => { setFilter(event.target.value); setSelected(new Set()); }}>
          {filters.map((item) => <option key={item.id} value={item.id}>{item.label} ({item.count})</option>)}
        </select>
      </label>
      <div className={styles.toolbar}>
        <div className={styles.viewActions}>
          <label>{t('Group by')}<select value={group} onChange={(event) => setGroup(event.target.value as 'book' | 'device')}><option value="book">{t('Book order')}</option><option value="device">{t('Device')}</option></select></label>
          <button type="button" onClick={() => { setSelecting(!selecting); setSelected(new Set()); setFailed(new Set()); }}>{selecting ? t('Done') : t('Select')}</button>
        </div>
        <div className={styles.dataActions}>
          <details><summary><Download size={14} aria-hidden="true" /> {t('Export')}</summary><div className={styles.exportMenu}>
            {['md', 'csv', 'json'].map((format) => <a key={format} href={apiUrl(`/annotations/${id}/export.${format}`)} download target="_blank" rel="noopener">{format === 'md' ? 'Markdown' : format.toUpperCase()}</a>)}
          </div></details>
          <a href={apiUrl('/annotations/import')}><UploadIcon size={14} aria-hidden="true" /> {t('Import')}</a>
        </div>
      </div>
      {selecting && <BulkSelectionBar count={selected.size} sticky onClear={() => { setSelecting(false); setSelected(new Set()); }}>
        <button ref={selectAllRef} type="button" onClick={() => setSelected(new Set(filtered.map((row) => row.annotation_id)))}>{t('Select all {n}', { n: filtered.length })}</button>
        <select aria-label={t('Assign selected to device')} disabled={!selected.size || busy} defaultValue=""
          onChange={(event) => { const value = event.target.value; if (value !== '') void applyBulk(value === 'unknown' ? null : value); event.currentTarget.value = ''; }}>
          <option value="" disabled>{t('Assign to device')}</option><option value="unknown">{t('Unknown device')}</option>
          {activeDevices.map((device) => <option key={device.public_id} value={device.public_id}>{device.label}</option>)}
        </select>
        {progress && <span>{t('{done} of {total}', { done: progress.done, total: progress.total })}</span>}
      </BulkSelectionBar>}
      {error ? <EmptyState message={error instanceof Error ? error.message : t('Could not load highlights.')} /> : !annotations.length ?
        <EmptyState message={t('No highlights yet. Highlight while reading, or import from a Kobo device.')} /> :
        <VirtualizedList items={entries} itemKey={(entry) => entry.kind === 'group' ? `group-${entry.id}` : entry.annotation.annotation_id}
          rowHeight={78} ariaLabel={t('Highlights')} renderItem={(entry) => entry.kind === 'group' ? (
            <div className={styles.groupHeader}><strong>{entry.label}</strong><span>{entry.count}</span>
              {selecting && <button type="button" onClick={() => setSelected((current) => new Set([...current, ...filtered.filter((row) => assignmentOf(row) === entry.id).map((row) => row.annotation_id)]))}>{t('Select all in group')}</button>}
            </div>
          ) : (() => {
            const row = entry.annotation; const current = assignmentOf(row); const quoteName = row.highlighted_text.slice(0, 60);
            return <div className={`${styles.item} ${selecting ? styles.selecting : ''} ${failed.has(row.annotation_id) ? styles.failed : ''}`}
              onPointerDown={(event) => startLongPress(row.annotation_id, event.pointerType)}
              onPointerUp={cancelLongPress} onPointerCancel={cancelLongPress} onPointerMove={cancelLongPress}
              onClick={() => {
                if (suppressClickRef.current === row.annotation_id) { suppressClickRef.current = null; return; }
                if (selecting) toggle(row.annotation_id);
              }}>
              {selecting && <label className={styles.rowSelect} onClick={(event) => event.stopPropagation()}><input type="checkbox" checked={selected.has(row.annotation_id)} onChange={() => toggle(row.annotation_id)} aria-label={t('Select highlight: {text}', { text: quoteName })} /><span className={styles.srOnly}>{t('Select highlight: {text}', { text: quoteName })}</span></label>}
              <span className={styles.bar} role="img" aria-label={colorName(row.highlight_color)} style={{ background: COLOR_HEX[row.highlight_color || 'yellow'] || COLOR_HEX.yellow }} />
              <div className={styles.body}>
                <blockquote className={styles.quote}>{row.highlighted_text}</blockquote>{row.note_text && <p className={styles.note}>{row.note_text}</p>}
                <div className={styles.meta}><span>{sourceLabel(row.source)}</span><span aria-hidden="true">·</span>
                  {group === 'device' ? <span>{deviceLabel(current)}</span> : <select className={!current ? styles.unknown : ''} value={current || 'unknown'}
                    aria-label={t('Device: {name}', { name: current ? deviceLabel(current) : t('unknown') })}
                    onClick={(event) => event.stopPropagation()} onChange={(event) => void setOneAssignment(row, event.target.value === 'unknown' ? null : event.target.value)}>
                    <option value="unknown">{t('Unknown device')}</option>{activeDevices.map((device) => <option key={device.public_id} value={device.public_id}>{device.label} — {device.model}</option>)}</select>}
                  {row.chapter_progress != null && <><span aria-hidden="true">·</span><span>{Math.round(row.chapter_progress * 100)}%</span></>}
                  {failed.has(row.annotation_id) && <span className={styles.failure}>{t('Not assigned')}</span>}
                </div>
              </div>
            </div>;
          })()} />}
      {toast && <div className={styles.toast} role="status"><span>{toast.text}</span>{toast.failed ? <span>{t('{n} failed.', { n: toast.failed })}</span> : null}
        {toast.undo && Object.keys(toast.undo).length > 0 && <button type="button" onClick={() => void undo()}>{t('Undo')}</button>}
        {toast.failed ? <button type="button" onClick={() => void applyBulk(toast.target ?? null)}>{t('Retry')}</button> : null}
      </div>}
    </main>
  );
}
