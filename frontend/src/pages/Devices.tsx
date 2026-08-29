import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'wouter';
import { ChevronLeft, MoreHorizontal, Pencil, Smartphone } from 'lucide-react';
import { apiDelete, apiGet, apiPatch, apiPost, apiUrl } from '../lib/api';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useT } from '../lib/i18n';
import { EmptyState } from '../components/EmptyState';
import { SpinnerCentered } from '../components/Spinner';
import styles from './Devices.module.css';

interface Device {
  public_id: string;
  label: string;
  type: string;
  model: string | null;
  firmware: string | null;
  first_seen: string | null;
  last_seen: string | null;
  annotation_count: number;
  inventory_count: number;
  inventory_observed: string | null;
  storage_free: number | null;
  storage_total: number | null;
  storage_observed: string | null;
  active: boolean;
}

interface Counts { origin_count: number; assigned_count: number }
interface InventoryBook { inventory_item_id: number; book_id: number | null; lpath: string; checksum: string; size: number; mtime: number }
interface InventoryPayload {
  observed_at: string | null;
  books: InventoryBook[];
  limit: number;
  offset: number;
  total: number;
}

const DEVICE_INVENTORY_WINDOW = 200;

function DeviceInventory({ device }: { device: Device }) {
  const t = useT();
  const announce = useAnnouncer();
  const [requested, setRequested] = useState<Set<number>>(() => new Set());
  const { data, isLoading, error } = useQuery<InventoryPayload>({
    queryKey: ['device-inventory', device.public_id],
    queryFn: () => apiGet(
      `/api/annotations/devices/${device.public_id}/inventory?limit=${DEVICE_INVENTORY_WINDOW}&offset=0`,
    ),
  });
  const deletion = useMutation({
    mutationFn: (book: InventoryBook) => apiPost(
      `/api/annotations/devices/${device.public_id}/inventory/${book.inventory_item_id}/delete`,
    ),
    onSuccess: (_result, book) => {
      setRequested((current) => new Set(current).add(book.inventory_item_id));
      announce(t('Deletion requested'));
    },
    onError: () => announce(t('Could not request deletion from this device.'), { assertive: true }),
  });
  const requestDeletion = (book: InventoryBook) => {
    if (window.confirm(t('Delete {path} from {name} on its next sync?', {
      path: book.lpath, name: device.label,
    }))) deletion.mutate(book);
  };
  // Bounded independently of the server's own cap, so a mixed-version deployment
  // or a future contract regression still cannot flood this list.
  const books = (data?.books ?? []).slice(0, DEVICE_INVENTORY_WINDOW);
  const status = isLoading
    ? t('Loading device library…')
    : error
      ? t('Could not load this device library.')
      : books.length === 0
        ? t('No books were reported in the latest device inventory.')
        : t('Showing {shown} of {total} books from the latest device inventory.', {
          shown: books.length,
          total: data?.total ?? 0,
        });
  return (
    <>
      <p role={error ? 'alert' : 'status'}>{status}</p>
      {!isLoading && !error && books.length > 0 && (
        <ul className={styles.inventoryList} role="list">
          {books.map((book) => (
            <li key={`${book.lpath}:${book.checksum}`}>
              <div className={styles.inventoryRow}>
                <div>
                  {book.book_id ? <Link href={`/book/${book.book_id}`}>{book.lpath}</Link> : <span>{book.lpath}</span>}
                  <span className={styles.onDevice}>{t('On this device')}</span>
                  {!book.book_id && <span className={styles.unmatched}>{t('Not matched to this library')}</span>}
                </div>
                <button type="button" className={styles.inventoryDelete}
                  disabled={requested.has(book.inventory_item_id)
                    || (deletion.isPending && deletion.variables?.inventory_item_id === book.inventory_item_id)}
                  onClick={() => requestDeletion(book)}>
                  {requested.has(book.inventory_item_id) ? t('Deletion requested') : t('Delete from device')}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function relativeWhen(value: string | null): string {
  if (!value) return '—';
  const elapsed = new Date(value).getTime() - Date.now();
  const formatter = new Intl.RelativeTimeFormat(document.documentElement.lang || undefined, { numeric: 'auto' });
  const hours = Math.round(elapsed / 3_600_000);
  if (Math.abs(hours) < 48) return formatter.format(hours, 'hour');
  return formatter.format(Math.round(hours / 24), 'day');
}

function formatStorage(bytes: number): string {
  const gibibytes = bytes / (1024 ** 3);
  if (gibibytes >= 1) return `${gibibytes.toFixed(1)} GB`;
  return `${(bytes / (1024 ** 2)).toFixed(1)} MB`;
}

function RemoveDialog({ device, counts, onCancel, onRemove }: {
  device: Device; counts: Counts; onCancel: () => void; onRemove: () => void;
}) {
  const t = useT();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
      if (event.key !== 'Tab' || !dialogRef.current) return;
      const controls = [...dialogRef.current.querySelectorAll<HTMLElement>('button:not([disabled])')];
      if (!controls.length) return;
      const first = controls[0]; const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);
  const descriptionId = `remove-device-${device.public_id}`;
  return (
    <div className={styles.scrim}>
      <div ref={dialogRef} className={styles.dialog} role="alertdialog" aria-modal="true"
        aria-labelledby={`${descriptionId}-title`} aria-describedby={descriptionId}>
        <h2 id={`${descriptionId}-title`}>{t('Remove {name}?', { name: device.label })}</h2>
        <div id={descriptionId}>
          {counts.origin_count > 0 && <p>{t('{n} highlights and notes were made on this device. They are not deleted. Their origin history is kept.', { n: counts.origin_count })}</p>}
          {counts.assigned_count > 0 && <p>{t('{n} highlights and notes assigned to this device will become Unknown device.', { n: counts.assigned_count })}</p>}
          <p>{t('This device will no longer sync.')}</p>
        </div>
        <div className={styles.dialogActions}>
          <button ref={cancelRef} type="button" className={styles.button} onClick={onCancel}>{t('Cancel')}</button>
          <button type="button" className={styles.dangerButton} onClick={onRemove}>{t('Remove device')}</button>
        </div>
      </div>
    </div>
  );
}

export function Devices() {
  const t = useT();
  const announce = useAnnouncer();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const [label, setLabel] = useState('');
  const [menu, setMenu] = useState<string | null>(null);
  const [expandedInventory, setExpandedInventory] = useState<string | null>(null);
  const [removing, setRemoving] = useState<{ device: Device; counts: Counts } | null>(null);
  const [undoDevice, setUndoDevice] = useState<Device | null>(null);
  const invokerRef = useRef<HTMLButtonElement | null>(null);
  const { data, isLoading, error } = useQuery<{ devices: Device[] }>({
    queryKey: ['annotation-devices'], queryFn: () => apiGet('/api/annotations/devices?active=true'),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['annotation-devices'] });
  const rename = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => apiPatch(`/api/annotations/devices/${id}`, { label: name }),
    onSuccess: () => { setEditing(null); refresh(); announce(t('Device renamed.')); },
  });
  const remove = useMutation({
    mutationFn: (device: Device) => apiDelete(`/api/annotations/devices/${device.public_id}`),
    onSuccess: (_result, device) => {
      setRemoving(null); setUndoDevice(device); refresh();
      announce(t('{name} removed.', { name: device.label }));
      invokerRef.current?.focus();
    },
  });
  const restore = useMutation({
    mutationFn: (device: Device) => apiPost(`/api/annotations/devices/${device.public_id}/restore`),
    onSuccess: () => { setUndoDevice(null); refresh(); announce(t('Device restored.')); },
  });

  const openRemove = async (device: Device, invoker: HTMLButtonElement) => {
    invokerRef.current = invoker;
    const counts = await apiGet<Counts>(`/api/annotations/devices/${device.public_id}/delete-preflight`);
    setMenu(null); setRemoving({ device, counts });
  };

  if (isLoading) return <SpinnerCentered size={40} />;
  const devices = data?.devices ?? [];
  return (
    <main className={styles.container}>
      <Link href="/account" className={styles.back}><ChevronLeft size={16} aria-hidden="true" focusable={false} /> {t('Account')}</Link>
      <div className={styles.heading}><Smartphone aria-hidden="true" focusable={false} /><h1>{t('E-readers')}</h1></div>
      {error ? <EmptyState message={t('Could not load e-readers.')} /> : devices.length === 0 ? (
        <section className={styles.empty}>
          <h2>{t('No e-readers yet.')}</h2>
          <p>{t('Devices appear here after their first sync.')}</p>
          <a href={apiUrl('/me')}>{t('Set up Kobo sync')}</a>
        </section>
      ) : (
        <ul className={styles.list} role="list">
          {devices.map((device) => (
            <li key={device.public_id} className={styles.card}>
              <div className={styles.cardMain}>
                {editing === device.public_id ? (
                  <form onSubmit={(event) => { event.preventDefault(); rename.mutate({ id: device.public_id, name: label }); }} className={styles.renameForm}>
                    <input autoFocus aria-label={t('Device name')} value={label} maxLength={60}
                      onChange={(event) => setLabel(event.target.value)}
                      onKeyDown={(event) => { if (event.key === 'Escape') setEditing(null); }} />
                    <button type="submit" disabled={!label.trim() || rename.isPending}>{t('Save')}</button>
                    <button type="button" onClick={() => setEditing(null)}>{t('Cancel')}</button>
                  </form>
                ) : <h2>{device.label}</h2>}
                <p>{[device.model, device.firmware && `FW ${device.firmware}`].filter(Boolean).join(' · ')}</p>
                <p>{t('{n} highlights and notes', { n: device.annotation_count })} · {t('Last seen {when}', { when: relativeWhen(device.last_seen) })}
                  {device.last_seen && Date.now() - new Date(device.last_seen).getTime() > 30 * 86400000 && <> · {t('Not seen lately')}</>}</p>
                <p>{t('{n} books in latest inventory', { n: device.inventory_count })}</p>
                {device.storage_free !== null && device.storage_total !== null && (
                  <p>{t('{free} free of {total}', {
                    free: formatStorage(device.storage_free), total: formatStorage(device.storage_total),
                  })}</p>
                )}
                <button type="button" className={styles.inventoryToggle}
                  aria-expanded={expandedInventory === device.public_id}
                  aria-controls={`device-inventory-${device.public_id}`}
                  onClick={() => setExpandedInventory(
                    expandedInventory === device.public_id ? null : device.public_id)}>
                  {expandedInventory === device.public_id ? t('Hide device library') : t('View device library')}
                </button>
                {expandedInventory === device.public_id && (
                  <div id={`device-inventory-${device.public_id}`} className={styles.inventory}>
                    <DeviceInventory device={device} />
                  </div>
                )}
              </div>
              <div className={styles.cardActions}>
                <button type="button" aria-label={t('Rename {name}', { name: device.label })}
                  onClick={() => { setEditing(device.public_id); setLabel(device.label); }}><Pencil size={17} aria-hidden="true" focusable={false} /></button>
                <button type="button" aria-label={t('More actions for {name}', { name: device.label })}
                  aria-expanded={menu === device.public_id} onClick={() => setMenu(menu === device.public_id ? null : device.public_id)}>
                  <MoreHorizontal aria-hidden="true" focusable={false} />
                </button>
                {menu === device.public_id && <div className={styles.menu}>
                  <button type="button" onClick={(event) => void openRemove(device, event.currentTarget)}>{t('Remove device')}</button>
                </div>}
              </div>
            </li>
          ))}
        </ul>
      )}
      <section className={styles.setup}>
        <h2>{t('Kobo setup')}</h2>
        <p>{t('Manage your Kobo sync URL in the classic account page.')}</p>
        <a href={apiUrl('/me')}>{t('Set up Kobo sync')}</a>
      </section>
      {/* The KOReader plugin reports the inventory and storage figures this page
          displays, but its setup page was reachable only from the classic admin
          screen. Now that the SPA is the default surface, a KOReader user could
          land here with no route to the plugin download or install steps. Both
          strings are existing catalog entries, so this ships translated. */}
      <section className={styles.setup}>
        <h2>{t('KOReader Sync Plugin')}</h2>
        <a href={apiUrl('/kosync')}>{t('Setup KOReader Sync')}</a>
      </section>
      {undoDevice && <div className={styles.toast} role="status">
        <span>{t('{name} removed.', { name: undoDevice.label })}</span>
        <button type="button" onClick={() => restore.mutate(undoDevice)}>{t('Undo')}</button>
      </div>}
      {removing && <RemoveDialog device={removing.device} counts={removing.counts}
        onCancel={() => { setRemoving(null); invokerRef.current?.focus(); }}
        onRemove={() => remove.mutate(removing.device)} />}
    </main>
  );
}
