import { useEffect, useMemo, useState, type KeyboardEvent } from 'react';
import { Link } from 'wouter';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, Smartphone } from 'lucide-react';
import { apiGet } from '../lib/api';
import { clampOffset, clampPage } from '../lib/pagination';
import { useT } from '../lib/i18n';
import { DeviceInventory, type Device } from '../components/DeviceInventory';
import { DeviceSummary } from '../components/DeviceSummary';
import { EmptyState } from '../components/EmptyState';
import { SpinnerCentered } from '../components/Spinner';
import styles from './DeviceDetail.module.css';

type AnnotationType = 'highlight' | 'note' | 'dogear';
type Tab = AnnotationType | 'inventory';

interface DeviceAnnotation {
  annotation_id: string;
  book_id: number;
  annotation_type: string | null;
  highlighted_text: string | null;
  highlight_color: string | null;
  note_text: string | null;
  chapter_progress: number | null;
  source: string | null;
  created_at: string | null;
  origin_device_id: string | null;
  assigned_device_id: string | null;
  book: { id: number; title: string | null };
}

interface AnnotationPayload {
  device: Device;
  annotations: DeviceAnnotation[];
  devices: Record<string, { label: string; model: string | null; type: string }>;
  page: number;
  pages: number;
  page_size: number;
  total: number;
  role: 'origin' | 'assigned';
  type: AnnotationType;
}

interface SummaryPayload {
  highlights: number;
  notes: number;
  dogears: number;
  books_with_position: number;
  last_position_at: string | null;
  seeded_books: number;
  unseeded_books: number;
}

interface PositionRow {
  book_id: number;
  book: { id: number; title: string | null };
  progress_percent: number | null;
  location_type: string | null;
  location_value: string | null;
  client_modified_at: string | null;
  server_modified_at: string;
}

interface PositionsPayload {
  positions: PositionRow[];
  limit: number;
  offset: number;
  total: number;
}

const POSITION_PAGE_SIZE = 100;

const TABS: { id: Tab; label: string }[] = [
  { id: 'highlight', label: 'Highlights' },
  { id: 'note', label: 'Notes' },
  { id: 'dogear', label: 'Dog-ears' },
  { id: 'inventory', label: 'Device library' },
];

function AnnotationList({ payload, loading, error }: {
  payload?: AnnotationPayload;
  loading: boolean;
  error: unknown;
}) {
  const t = useT();
  if (loading) return <p role="status">{t('Loading device annotations…')}</p>;
  if (error) return <p role="alert">{t('Could not load device annotations.')}</p>;
  const rows = payload?.annotations ?? [];
  if (!rows.length) return <p role="status">{t('Nothing in this category yet.')}</p>;
  return (
    <>
      <p role="status">{t('Showing {shown} of {total} annotations.', {
        shown: rows.length, total: payload?.total ?? 0,
      })}</p>
      <ul className={styles.annotationList} role="list">
        {rows.map((row) => (
          <li key={`${row.book_id}:${row.annotation_id}`} className={styles.annotationRow}>
            <Link href={`/book/${row.book_id}/annotations`}>
              {row.book.title || t('Book {id}', { id: row.book_id })}
            </Link>
            {row.highlighted_text && <blockquote>{row.highlighted_text}</blockquote>}
            {row.note_text && <p>{row.note_text}</p>}
            <small>
              {[
                (row.origin_device_id && payload?.devices[row.origin_device_id]?.label)
                  || row.source,
                row.chapter_progress == null
                ? null
                : `${Math.round(row.chapter_progress * 100)}%`,
              ].filter(Boolean).join(' · ')}
            </small>
          </li>
        ))}
      </ul>
    </>
  );
}

function PositionList({ data, loading, error }: {
  data?: PositionsPayload;
  loading: boolean;
  error: unknown;
}) {
  const t = useT();
  if (loading) return <p role="status">{t('Loading reading positions…')}</p>;
  if (error) return <p role="alert">{t('Could not load reading positions.')}</p>;
  const positions = data?.positions ?? [];
  if (!positions.length) return <p>{t('No reading positions from this device yet.')}</p>;
  return (
    <>
      <p role="status">{t('Page {page} of {pages}', {
        page: Math.floor((data?.offset ?? 0) / (data?.limit ?? POSITION_PAGE_SIZE)) + 1,
        pages: Math.max(1, Math.ceil((data?.total ?? 0) / (data?.limit ?? POSITION_PAGE_SIZE))),
      })}</p>
      <ul className={styles.positionList} role="list">
        {positions.map((position) => (
          <li key={position.book_id}>
            <Link href={`/book/${position.book_id}`}>
              {position.book.title || t('Book {id}', { id: position.book_id })}
            </Link>
            <span>{position.progress_percent == null
              ? t('Position recorded')
              : t('{percent}% read', { percent: Math.round(position.progress_percent) })}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

export function DeviceDetail({ publicId }: { publicId: string }) {
  const t = useT();
  const [tab, setTab] = useState<Tab>('highlight');
  const [role, setRole] = useState<'origin' | 'assigned'>('origin');
  const [page, setPage] = useState(1);
  const [positionOffset, setPositionOffset] = useState(0);
  const [retainedDevice, setRetainedDevice] = useState<{
    publicId: string;
    device: Device;
  } | null>(null);
  const registry = useQuery<{ devices: Device[] }>({
    queryKey: ['annotation-devices'],
    queryFn: () => apiGet('/api/annotations/devices'),
  });
  const summary = useQuery<SummaryPayload>({
    queryKey: ['device-summary', publicId],
    queryFn: () => apiGet(`/api/annotations/devices/${publicId}/summary`),
  });
  const positions = useQuery<PositionsPayload>({
    queryKey: ['device-positions', publicId, positionOffset],
    queryFn: () => apiGet(
      `/api/annotations/devices/${publicId}/positions?limit=${POSITION_PAGE_SIZE}&offset=${positionOffset}`,
    ),
  });
  const annotations = useQuery<AnnotationPayload>({
    queryKey: ['device-annotations', publicId, tab, role, page],
    queryFn: () => apiGet(
      `/api/annotations/devices/${publicId}/annotations?type=${tab}&role=${role}&page=${page}`,
    ),
    enabled: tab !== 'inventory',
  });
  const correctedAnnotationPage = annotations.data
    ? clampPage(page, annotations.data.pages)
    : page;
  const staleAnnotationPage = tab !== 'inventory' && correctedAnnotationPage !== page;
  useEffect(() => {
    if (staleAnnotationPage) setPage(correctedAnnotationPage);
  }, [correctedAnnotationPage, staleAnnotationPage]);
  const correctedPositionOffset = positions.data
    ? clampOffset(positionOffset, positions.data.total, POSITION_PAGE_SIZE)
    : positionOffset;
  const stalePositionPage = correctedPositionOffset !== positionOffset;
  useEffect(() => {
    if (stalePositionPage) setPositionOffset(correctedPositionOffset);
  }, [correctedPositionOffset, stalePositionPage]);
  const resolvedDevice = useMemo(
    () => registry.data?.devices.find((candidate) => candidate.public_id === publicId)
      || (annotations.data?.device.public_id === publicId ? annotations.data.device : undefined),
    [annotations.data, publicId, registry.data],
  );
  useEffect(() => {
    if (resolvedDevice) setRetainedDevice({ publicId, device: resolvedDevice });
  }, [publicId, resolvedDevice]);
  // A query-key change temporarily clears annotations.data. Retain only the
  // device already resolved for this public id so switching tabs cannot replace
  // the whole tablist with the loading spinner and destroy keyboard focus.
  const device = resolvedDevice
    || (retainedDevice?.publicId === publicId ? retainedDevice.device : undefined);

  const selectTab = (next: Tab) => {
    setTab(next);
    setPage(1);
  };
  const onTabKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const buttons = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
    );
    const current = buttons.indexOf(event.target as HTMLButtonElement);
    if (current < 0) return;
    let next = current;
    if (event.key === 'ArrowRight') next = (current + 1) % TABS.length;
    else if (event.key === 'ArrowLeft') next = (current - 1 + TABS.length) % TABS.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = TABS.length - 1;
    else return;
    event.preventDefault();
    selectTab(TABS[next].id);
    buttons[next]?.focus();
  };

  if (registry.isLoading || (annotations.isLoading && !device)) return <SpinnerCentered size={40} />;
  if (!device) {
    return (
      <main className={styles.container}>
        <h1>{t('E-reader not found')}</h1>
        <EmptyState message={t('This e-reader is unavailable or belongs to another account.')} />
      </main>
    );
  }
  const activePanelId = `device-panel-${tab}`;
  return (
    <main className={styles.container}>
      <Link href="/account/devices" className={styles.back}>
        <ChevronLeft size={16} aria-hidden="true" focusable={false} /> {t('E-readers')}
      </Link>
      <header className={styles.header}>
        <Smartphone aria-hidden="true" focusable={false} />
        <div><h1>{device.label}</h1><p>{device.kind_label || device.type}</p></div>
      </header>
      <section className={styles.summary} aria-label={t('Device summary')}>
        <DeviceSummary device={{
          ...device,
          highlights: summary.data?.highlights ?? device.highlights,
          notes: summary.data?.notes ?? device.notes,
          dogears: summary.data?.dogears ?? device.dogears,
          seeded_books: summary.data?.seeded_books ?? device.seeded_books,
          unseeded_books: summary.data?.unseeded_books ?? device.unseeded_books,
        }} />
      </section>
      <label className={styles.assignedToggle}>
        <input
          type="checkbox"
          checked={role === 'assigned'}
          onChange={(event) => {
            setRole(event.target.checked ? 'assigned' : 'origin');
            setPage(1);
          }}
        />
        <span>{t('Show annotations assigned to this device')}</span>
      </label>
      <div className={styles.tabs} role="tablist" aria-label={t('Device data')} onKeyDown={onTabKeyDown}>
        {TABS.map((item) => (
          <button
            key={item.id}
            id={`device-tab-${item.id}`}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            aria-controls={`device-panel-${item.id}`}
            tabIndex={tab === item.id ? 0 : -1}
            onClick={() => selectTab(item.id)}
          >
            {t(item.label)}
          </button>
        ))}
      </div>
      <section
        id={activePanelId}
        className={styles.panel}
        role="tabpanel"
        aria-labelledby={`device-tab-${tab}`}
        tabIndex={0}
      >
        {tab === 'inventory'
          ? <DeviceInventory device={device} />
          : <AnnotationList
              payload={annotations.data}
              loading={annotations.isLoading || staleAnnotationPage}
              error={annotations.error}
            />}
        {tab !== 'inventory' && !staleAnnotationPage && (annotations.data?.pages ?? 0) > 1 && (
          <nav className={styles.pagination} aria-label={t('Annotation pages')}>
            <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              {t('Previous')}
            </button>
            <span>{t('Page {page} of {pages}', {
              page, pages: annotations.data?.pages ?? 1,
            })}</span>
            <button
              type="button"
              disabled={page >= (annotations.data?.pages ?? 1)}
              onClick={() => setPage(page + 1)}
            >
              {t('Next')}
            </button>
          </nav>
        )}
      </section>
      <section className={styles.positions} aria-labelledby="device-positions-heading">
        <h2 id="device-positions-heading">{t('Reading positions')}</h2>
        <PositionList data={positions.data}
          loading={positions.isLoading || stalePositionPage} error={positions.error} />
        {!stalePositionPage && (positions.data?.total ?? 0) > POSITION_PAGE_SIZE && (
          <nav className={styles.pagination} aria-label={t('Reading positions')}>
            <button type="button" disabled={positionOffset === 0}
              onClick={() => setPositionOffset(Math.max(0, positionOffset - POSITION_PAGE_SIZE))}>
              {t('Previous')}
            </button>
            <span>{t('Page {page} of {pages}', {
              page: Math.floor(positionOffset / POSITION_PAGE_SIZE) + 1,
              pages: Math.ceil((positions.data?.total ?? 0) / POSITION_PAGE_SIZE),
            })}</span>
            <button type="button"
              disabled={positionOffset + POSITION_PAGE_SIZE >= (positions.data?.total ?? 0)}
              onClick={() => setPositionOffset(positionOffset + POSITION_PAGE_SIZE)}>
              {t('Next')}
            </button>
          </nav>
        )}
      </section>
    </main>
  );
}
