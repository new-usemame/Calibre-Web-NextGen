import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'wouter';
import { ChevronLeft, MoreHorizontal, Pencil, Smartphone } from 'lucide-react';
import { apiDelete, apiGet, apiPatch, apiPost } from '../lib/api';
import { useMe } from '../lib/queries';
import { clampOffset } from '../lib/pagination';
import { parseApiTimestamp, relativeWhen } from '../lib/relativeTime';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { useT } from '../lib/i18n';
import { EmptyState } from '../components/EmptyState';
import { SpinnerCentered } from '../components/Spinner';
import { DeviceInventory, type Device } from '../components/DeviceInventory';
import { KoboPairing } from '../components/KoboPairing';
import styles from './Devices.module.css';

interface Counts { origin_count: number; assigned_count: number }
interface DevicePage { devices: Device[]; limit: number; offset: number; total: number }

const DEVICE_PAGE_SIZE = 100;

function formatStorage(bytes: number): string {
  const gibibytes = bytes / (1024 ** 3);
  if (gibibytes >= 1) return `${gibibytes.toFixed(1)} GB`;
  return `${(bytes / (1024 ** 2)).toFixed(1)} MB`;
}

function isDeviceStale(lastSeen: string | null): boolean {
  if (!lastSeen) return false;
  const timestamp = parseApiTimestamp(lastSeen);
  return timestamp !== null && Date.now() - timestamp > 30 * 86400000;
}

function RemoveDialog({ device, counts, onCancel, onRemove, pending, error }: {
  device: Device; counts: Counts; onCancel: () => void; onRemove: () => void;
  pending: boolean; error: boolean;
}) {
  const t = useT();
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, { onClose: onCancel });
  const descriptionId = `remove-device-${device.public_id}`;
  return (
    <div className={styles.scrim}>
      <div ref={dialogRef} className={styles.dialog} role="alertdialog" aria-modal="true" tabIndex={-1}
        aria-labelledby={`${descriptionId}-title`} aria-describedby={descriptionId}>
        <h2 id={`${descriptionId}-title`}>{t('Remove {name}?', { name: device.type === 'webreader' && device.label === 'Browser' ? t('Browser') : device.label })}</h2>
        <div id={descriptionId}>
          {counts.origin_count > 0 && <p>{t('{n} annotations were made on this source. They are not deleted. Their origin history is kept.', { n: counts.origin_count })}</p>}
          {counts.assigned_count > 0 && <p>{t('{n} annotations assigned to this source will become Unknown device.', { n: counts.assigned_count })}</p>}
          <p>{device.type === 'webreader'
            ? t('Reading data is kept. Browser reappears when you next save reading progress or annotations.')
            : t('This device will no longer sync.')}</p>
        </div>
        <p role="alert" className={error ? styles.inventoryAlert : undefined}>{error ? t('Could not remove this source. Try again.') : ''}</p>
        <div className={styles.dialogActions}>
          <button type="button" className={styles.button} onClick={onCancel}>{t('Cancel')}</button>
          <button type="button" className={styles.dangerButton} disabled={pending} onClick={onRemove}>{pending ? t('Removing…') : t('Remove device')}</button>
        </div>
      </div>
    </div>
  );
}

export function Devices() {
  const t = useT();
  const announce = useAnnouncer();
  const queryClient = useQueryClient();
  const me = useMe().data;
  const [editing, setEditing] = useState<string | null>(null);
  const [label, setLabel] = useState('');
  const [menu, setMenu] = useState<string | null>(null);
  const [expandedInventory, setExpandedInventory] = useState<string | null>(null);
  const [removing, setRemoving] = useState<{ device: Device; counts: Counts } | null>(null);
  const [undoDevice, setUndoDevice] = useState<Device | null>(null);
  const [deviceOffset, setDeviceOffset] = useState(0);
  const [preflightError, setPreflightError] = useState(false);
  const [preflightPending, setPreflightPending] = useState(false);
  const preflightRef = useRef(false);
  const undoRef = useRef<HTMLButtonElement>(null);
  const renameInvokerRef = useRef<HTMLButtonElement | null>(null);
  const menuInvokerRef = useRef<HTMLButtonElement | null>(null);
  const menuDismissLayerRef = useRef<HTMLDivElement | null>(null);
  const { data, isLoading, error } = useQuery<DevicePage>({
    queryKey: ['annotation-devices', deviceOffset],
    queryFn: () => apiGet(
      `/api/annotations/devices?active=true&limit=${DEVICE_PAGE_SIZE}&offset=${deviceOffset}`,
    ),
  });
  const correctedDeviceOffset = data
    ? clampOffset(deviceOffset, data.total, DEVICE_PAGE_SIZE)
    : deviceOffset;
  const staleDevicePage = correctedDeviceOffset !== deviceOffset;
  useEffect(() => {
    if (staleDevicePage) setDeviceOffset(correctedDeviceOffset);
  }, [correctedDeviceOffset, staleDevicePage]);
  useEffect(() => {
    if (menu === null) return undefined;
    const dismissLayer = menuDismissLayerRef.current;
    const dismissOnTouchStart = (event: TouchEvent) => {
      event.preventDefault();
      event.stopPropagation();
      setMenu(null);
    };
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setMenu(null);
      menuInvokerRef.current?.focus();
    };
    dismissLayer?.addEventListener('touchstart', dismissOnTouchStart, { passive: false });
    document.addEventListener('keydown', dismissOnEscape);
    return () => {
      dismissLayer?.removeEventListener('touchstart', dismissOnTouchStart);
      document.removeEventListener('keydown', dismissOnEscape);
    };
  }, [menu]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['annotation-devices'] });
  const rename = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => apiPatch(`/api/annotations/devices/${id}`, { label: name }),
    onSuccess: () => { setEditing(null); refresh(); announce(t('Device renamed.')); renameInvokerRef.current?.focus(); },
  });
  const remove = useMutation({
    mutationFn: (device: Device) => apiDelete(`/api/annotations/devices/${device.public_id}`),
    onSuccess: (_result, device) => {
      setRemoving(null); setUndoDevice(device); refresh();
      announce(t('{name} removed.', { name: device.type === 'webreader' && device.label === 'Browser' ? t('Browser') : device.label }));
      requestAnimationFrame(() => undoRef.current?.focus());
    },
  });
  const restore = useMutation({
    mutationFn: (device: Device) => apiPost(`/api/annotations/devices/${device.public_id}/restore`),
    onSuccess: async () => {
      setUndoDevice(null); await refresh(); announce(t('Device restored.'));
      document.getElementById('main')?.focus();
    },
  });

  const cancelRemoval = useCallback(() => { setRemoving(null); }, []);
  const openRemove = async (device: Device) => {
    if (preflightRef.current) return;
    preflightRef.current = true; setPreflightPending(true); setPreflightError(false); remove.reset();
    try {
      const counts = await apiGet<Counts>(`/api/annotations/devices/${device.public_id}/delete-preflight`);
      // The disclosure's Remove button disappears when the dialog opens. Capture
      // the surviving trigger for the shared trap's focus restoration instead.
      menuInvokerRef.current?.focus();
      setMenu(null); setRemoving({ device, counts });
    } catch { setPreflightError(true); }
    finally { preflightRef.current = false; setPreflightPending(false); }
  };

  if (isLoading || staleDevicePage) return <SpinnerCentered size={40} />;
  const devices = data?.devices ?? [];
  return (
    <div className={styles.container}>
      <Link href="/account" className={styles.back}><ChevronLeft size={16} aria-hidden="true" focusable={false} /> {t('Account')}</Link>
      <div className={styles.heading}><Smartphone aria-hidden="true" focusable={false} /><h1>{t('Devices and browsers')}</h1></div>
      <p role="alert" className={preflightError ? styles.inventoryAlert : undefined}>{preflightError ? t('Could not load removal details. Try again.') : ''}</p>
      {error ? <EmptyState message={t('Could not load devices and browsers.')} /> : devices.length === 0 ? (
        <section className={styles.empty}>
          <h2>{t('No devices or browser reading data yet.')}</h2>
          <p>{t('Devices appear after their first sync. Browser appears after saving reading progress or annotations.')}</p>
          <a href="#kobo-pairing">{t('Pair an e-reader')}</a>
        </section>
      ) : (
        <>
          <p role="status" className={styles.countLine}>{t('Page {page} of {pages}', {
            page: Math.floor(deviceOffset / DEVICE_PAGE_SIZE) + 1,
            pages: Math.max(1, Math.ceil((data?.total ?? 0) / DEVICE_PAGE_SIZE)),
          })}</p>
          {['ereaders', 'browsers'].map((group) => {
            const grouped = devices.filter(device => (device.type === 'webreader') === (group === 'browsers'));
            if (!grouped.length) return null;
            return <section key={group} id={group === 'browsers' ? 'browser-reading-sources' : undefined} aria-label={group === 'browsers' ? t('Browser reading source') : t('E-readers')}>
              {group === 'browsers' && <>
                <h2>{t('Browser reading source')}</h2>
                <p>{t('All browsers and computers signed in to your account share one Browser reading source.')}</p>
              </>}
          <ul className={styles.list} role="list">
            {grouped.map((device) => (
            <li key={device.public_id} className={styles.card}>
              <div className={styles.cardMain}>
                {editing === device.public_id ? (
                  <form onSubmit={(event) => { event.preventDefault(); if (!rename.isPending && label.trim()) rename.mutate({ id: device.public_id, name: label }); }} className={styles.renameForm}>
                    <input autoFocus aria-label={t('Device name')} value={label} maxLength={60}
                      aria-invalid={rename.isError || undefined} aria-describedby={rename.isError ? `rename-error-${device.public_id}` : undefined}
                      onChange={(event) => setLabel(event.target.value)}
                      onKeyDown={(event) => { if (event.key === 'Escape') setEditing(null); }} />
                    <button type="submit" disabled={!label.trim() || rename.isPending}>{t('Save')}</button>
                    <button type="button" onClick={() => { setEditing(null); renameInvokerRef.current?.focus(); }}>{t('Cancel')}</button>
                    <span id={`rename-error-${device.public_id}`} role="alert">{rename.isError ? t('Could not rename this source. Try again.') : ''}</span>
                  </form>
                ) : <h2><Link href={`/account/devices/${device.public_id}`}>{device.type === 'webreader' && device.label === 'Browser' ? t('Browser') : device.label}</Link></h2>}
                <p className={styles.deviceMeta}>{[device.model, device.firmware && `FW ${device.firmware}`].filter(Boolean).join(' · ')}</p>

                <p className={styles.deviceStats}>
                  {device.origin_annotation_count != null && <><span>{t('{n} annotations from this source', { n: device.origin_annotation_count })}</span> · </>}
                  <span>{t('{n} annotations assigned to this source', { n: device.annotation_count })}</span> · {t('Last seen {when}', { when: relativeWhen(device.last_seen) })}
                  {isDeviceStale(device.last_seen) && <> <span className={styles.stalePill}>{t('Not seen lately')}</span></>}</p>
                {device.type !== 'webreader' && <p className={styles.deviceMeta}>{device.inventory_observed
                  ? t('{n} books in latest inventory', { n: device.inventory_count })
                  : t('Inventory not reported')}</p>}
                {device.type !== 'webreader' && device.storage_free != null && device.storage_total != null && (
                  <p className={styles.storage}>
                    <span>{t('{free} free of {total}', {
                      free: formatStorage(device.storage_free), total: formatStorage(device.storage_total),
                    })}</span>
                    <span className={styles.storageMeter} aria-hidden="true">
                      <span style={{
                        width: `${device.storage_total > 0
                          ? Math.min(100, Math.max(0, ((device.storage_total - device.storage_free) / device.storage_total) * 100))
                          : 0}%`,
                      }} />
                    </span>
                  </p>
                )}
                {device.type !== 'webreader' && <button type="button" className={styles.inventoryToggle}
                  aria-expanded={expandedInventory === device.public_id}
                  aria-controls={`device-inventory-${device.public_id}`}
                  onClick={() => setExpandedInventory(
                    expandedInventory === device.public_id ? null : device.public_id)}>
                  {expandedInventory === device.public_id ? t('Hide device library') : t('View device library')}
                </button>}
                {device.type !== 'webreader' && expandedInventory === device.public_id && (
                  <div id={`device-inventory-${device.public_id}`} className={styles.inventory}>
                    <DeviceInventory device={device} />
                  </div>
                )}
              </div>
              <div className={styles.cardActions}>
                <button type="button" aria-label={t('Rename {name}', { name: device.type === 'webreader' && device.label === 'Browser' ? t('Browser') : device.label })}
                  onClick={(event) => { renameInvokerRef.current = event.currentTarget; rename.reset(); setEditing(device.public_id); setLabel(device.label); }}><Pencil size={17} aria-hidden="true" focusable={false} /></button>
                <button type="button" aria-label={t('More actions for {name}', { name: device.type === 'webreader' && device.label === 'Browser' ? t('Browser') : device.label })}
                  aria-expanded={menu === device.public_id}
                  className={menu === device.public_id ? styles.menuTriggerOpen : undefined}
                  onClick={(event) => {
                    menuInvokerRef.current = event.currentTarget;
                    setMenu(menu === device.public_id ? null : device.public_id);
                  }}>
                  <MoreHorizontal aria-hidden="true" focusable={false} />
                </button>
                {menu === device.public_id && <>
                  <div ref={menuDismissLayerRef} className={styles.menuDismissLayer} aria-hidden="true"
                    onPointerDown={(event) => {
                      if (event.pointerType === 'touch') return;
                      event.preventDefault();
                      event.stopPropagation();
                      setMenu(null);
                    }} />
                  <div className={styles.menu}>
                    <button type="button" disabled={preflightPending} onClick={() => void openRemove(device)}>{preflightPending ? t('Loading…') : t('Remove device')}</button>
                  </div>
                </>}
              </div>
            </li>
            ))}
          </ul>
            </section>;
          })}
          {(data?.total ?? 0) > DEVICE_PAGE_SIZE && (
            <nav className={styles.pagination} aria-label={t('Devices and browsers')}>
              <button
                type="button"
                disabled={deviceOffset === 0}
                onClick={() => setDeviceOffset(Math.max(0, deviceOffset - DEVICE_PAGE_SIZE))}
              >
                {t('Previous')}
              </button>
              <span>{t('Page {page} of {pages}', {
                page: Math.floor(deviceOffset / DEVICE_PAGE_SIZE) + 1,
                pages: Math.ceil((data?.total ?? 0) / DEVICE_PAGE_SIZE),
              })}</span>
              <button
                type="button"
                disabled={deviceOffset + DEVICE_PAGE_SIZE >= (data?.total ?? 0)}
                onClick={() => setDeviceOffset(deviceOffset + DEVICE_PAGE_SIZE)}
              >
                {t('Next')}
              </button>
            </nav>
          )}
        </>
      )}
      <KoboPairing devices={devices} enabled={!!me?.features?.kobo_sync} />
      {undoDevice && <div className={styles.toast} role="status">
        <span>{t('{name} removed.', { name: undoDevice.label })}</span>
        <button ref={undoRef} type="button" disabled={restore.isPending} onClick={() => restore.mutate(undoDevice)}>{t('Undo')}</button>
        <span role="alert">{restore.isError ? t('Could not restore this source. Try again.') : ''}</span>
      </div>}
      {removing && <RemoveDialog device={removing.device} counts={removing.counts}
        onCancel={cancelRemoval} pending={remove.isPending} error={remove.isError}
        onRemove={() => { if (!remove.isPending) remove.mutate(removing.device); }} />}
    </div>
  );
}
