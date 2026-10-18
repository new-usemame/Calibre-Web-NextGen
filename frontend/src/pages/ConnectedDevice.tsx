import { useState } from 'react';
import { Link, useLocation } from 'wouter';
import { Download, MoreVertical, RefreshCw, Trash2, Unplug, Usb } from 'lucide-react';
import { useDevice, useDeviceSources, type DeviceEntry, type DeviceSource } from '../lib/device';
import { useMe, useUploadBooks } from '../lib/queries';
import { canUploadBooks } from '../lib/permissions';
import { ApiError } from '../lib/api';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { BookCover } from '../components/BookCover';
import { EmptyState } from '../components/EmptyState';
import { Menu } from '../components/Menu';
import { SpinnerCentered } from '../components/Spinner';
import styles from './ConnectedDevice.module.css';

function formatBytes(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1e3))} kB`;
}

/** The books on a USB-connected device, with eject. The connection itself lives in the app shell, so this
 *  page only reads and acts on it. Which device is the `id` in /device/<id>. */
export function ConnectedDevice({ id }: { id: string }) {
  const t = useT();
  const announce = useAnnouncer();
  const [, navigate] = useLocation();
  const device = useDevice(id);
  const sources = useDeviceSources();
  const [connectError, setConnectError] = useState<string | null>(null);

  /** Ask the browser for a device and go to its page, which is where it can then be used. */
  const connectVia = async (source: DeviceSource) => {
    setConnectError(null);
    try {
      const connected = await source.connect();
      if (connected) navigate(`/device/${encodeURIComponent(connected.id)}`);
    } catch (err) {
      setConnectError(err instanceof Error ? err.message : t('Could not connect.'));
    }
  };
  const me = useMe().data;
  const upload = useUploadBooks();
  // Per file on the device: importing, handed to the library's ingest, or why it failed.
  const [imports, setImports] = useState<Record<number, { state: 'busy' | 'queued' | 'error'; text?: string }>>({});
  const setImport = (handle: number, v: { state: 'busy' | 'queued' | 'error'; text?: string }) =>
    setImports((cur) => ({ ...cur, [handle]: v }));

  const importEntry = async (entry: DeviceEntry) => {
    if (!device) return;
    setImport(entry.handle, { state: 'busy' });
    try {
      const bytes = await device.read(entry.handle);
      const res = await upload.mutateAsync([new File([new Uint8Array(bytes)], entry.name)]);
      const failed = res.errors[0];
      if (failed) setImport(entry.handle, { state: 'error', text: failed.error });
      else {
        setImport(entry.handle, { state: 'queued' });
        announce(t('{name} is being added to the library.', { name: entry.name }));
      }
    } catch (err) {
      setImport(entry.handle, {
        state: 'error',
        text: err instanceof ApiError ? err.message : device.describeError(err) || t('Could not import this book.'),
      });
    }
  };
  const canImport = canUploadBooks(me);
  const [removeError, setRemoveError] = useState<string | null>(null);

  const removeEntry = (entry: DeviceEntry) => {
    if (!device) return;
    const what = entry.book ? entry.book.title : entry.name;
    const tail = entry.book
      ? t('The book stays in your library.')
      : t('This file is not in your library, so it will be gone.');
    if (!window.confirm(`${t('Remove "{title}" from {name}?', { title: what, name: device.name || device.label })} ${tail}`)) return;
    setRemoveError(null);
    device.remove(entry.handle).then(
      () => announce(t('Removed from {name}.', { name: device.name || device.label })),
      (err) => {
        const message = device.describeError(err);
        if (message !== null) setRemoveError(message || t('Could not remove this book from the device.'));
      },
    );
  };
  const entryMenu = (entry: DeviceEntry, className: string) => (
    <div className={className}>
      <Menu
        label={t('Actions for {title}', { title: entry.book ? entry.book.title : entry.name })}
        icon={<MoreVertical size={16} aria-hidden="true" focusable={false} />}
        triggerTestId="device-entry-menu"
        sections={[{ id: 'actions', items: [{
          id: 'remove', label: t('Remove from device'), icon: <Trash2 size={15} />, danger: true,
          disabled: device?.busy !== null, onSelect: () => removeEntry(entry), testId: 'device-entry-remove',
        }] }]}
      />
    </div>
  );

  if (!device) {
    if (sources.length === 0) {
      return (
        <div className={styles.container}>
          <EmptyState icon={Usb} title={t('Connected device')}
            message={t('This browser cannot talk to USB devices. Use Chrome or Edge on an https:// or localhost address, signed in with download permission.')} />
        </div>
      );
    }
    return (
      <div className={styles.container}>
        <EmptyState icon={Usb} title={t('Device not connected')}
          message={connectError ?? t('Plug in your device with a USB cable, unlock it, and connect it here.')}>
          {sources.map((source) => (
            <button key={source.kind} type="button" className={styles.primary} data-testid="device-connect"
              onClick={() => { void connectVia(source); }}>
              {t('Connect {name}', { name: source.label })}
            </button>
          ))}
        </EmptyState>
      </div>
    );
  }

  if (device.status === 'unsupported') {
    return (
      <div className={styles.container}>
        <EmptyState icon={Usb} title={device.label}
          message={t('This browser cannot talk to USB devices. Use Chrome or Edge on an https:// or localhost address, signed in with download permission.')} />
      </div>
    );
  }

  if (device.status === 'connecting') return <SpinnerCentered size={40} />;

  if (device.status !== 'connected') {
    const ejected = device.status === 'ejected';
    return (
      <div className={styles.container}>
        <EmptyState icon={Usb}
          title={ejected ? t('{name} ejected', { name: device.name || device.label }) : t('No {name} connected', { name: device.label })}
          message={device.error
            ?? (ejected
              ? t('It is safe to unplug. Connect again to see its books.')
              : t('Plug in your {name} with a USB cable, unlock it, and connect it here.', { name: device.label }))}>
          <button type="button" className={styles.primary} onClick={() => { void device.connect().catch(() => undefined); }}
            data-testid="device-connect">
            {ejected ? t('Reconnect') : t('Connect {name}', { name: device.label })}
          </button>
        </EmptyState>
      </div>
    );
  }

  const matched = device.entries.filter((e) => e.book);
  const others = device.entries.filter((e) => !e.book);

  return (
    <div className={styles.container}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title} tabIndex={-1} data-testid="device-heading">{device.name}</h1>
          <p className={styles.sub}>
            {t('{count} books on the device', { count: device.entries.length })}
            {device.space ? ` · ${t('{free} free of {total}', {
              free: formatBytes(device.space.free), total: formatBytes(device.space.capacity) })}` : ''}
          </p>
        </div>
        <div className={styles.actions}>
          {sources.filter((s) => s.kind === device.kind).map((source) => (
            <button key={source.kind} type="button" className={styles.secondary} data-testid="device-connect-another"
              onClick={() => { void connectVia(source); }}>
              <Usb size={15} aria-hidden="true" focusable={false} />
              {t('Connect another {name}', { name: source.label })}
            </button>
          ))}
          <button type="button" className={styles.secondary} onClick={() => { void device.refresh(); }}
            disabled={device.busy !== null}>
            <RefreshCw size={15} aria-hidden="true" focusable={false} />
            {device.busy ?? t('Refresh')}
          </button>
          <button type="button" className={styles.primary} data-testid="device-eject"
            disabled={device.busy !== null}
            onClick={() => {
              void device.eject().then(() => announce(t('{name} ejected. It is safe to unplug.', { name: device.name })));
            }}>
            <Unplug size={15} aria-hidden="true" focusable={false} />
            {t('Eject')}
          </button>
        </div>
      </header>

      {(removeError ?? connectError ?? device.error) && (
        <p className={styles.error} role="alert">{removeError ?? connectError ?? device.error}</p>
      )}

      {device.entries.length === 0 ? (
        <EmptyState icon={Usb} message={t('There are no books on this device yet. Send one from any book page.')} />
      ) : (
        <>
          {matched.length > 0 && (
            <ul className={styles.grid} role="list" data-testid="device-books">
              {matched.map((e) => (
                <li key={e.handle} className={styles.tile}>
                  {entryMenu(e, styles.tileMenu)}
                  <Link href={`/book/${e.book!.id}`} className={styles.tileLink}>
                    <div className={styles.cover}>
                      <BookCover coverUrl={e.book!.cover_url} title={e.book!.title} authors={e.book!.authors} />
                    </div>
                    <span className={styles.tileTitle}>{e.book!.title}</span>
                    <span className={styles.tileMeta}>{e.book!.authors.join(', ')}</span>
                    <span className={styles.tileMeta}>{e.ext.toUpperCase()} · {formatBytes(e.size)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}

          {others.length > 0 && (
            <section className={styles.others} aria-labelledby="device-others">
              <h2 id="device-others" className={styles.subhead}>{t('Not in this library')}</h2>
              <ul role="list" className={styles.otherList}>
                {others.map((e) => (
                  <li key={e.handle} className={styles.otherRow}>
                    <span className={styles.otherName}>{e.folder ? `${e.folder}/` : ''}{e.name}</span>
                    <span className={styles.tileMeta}>{formatBytes(e.size)}</span>
                    {canImport && (() => {
                      const imp = imports[e.handle];
                      if (imp?.state === 'queued') {
                        return <span className={styles.tileMeta} role="status">{t('Added to the library')}</span>;
                      }
                      return (
                        <>
                          {imp?.state === 'error' && <span className={styles.error} role="alert">{imp.text}</span>}
                          <button type="button" className={styles.secondary} data-testid="device-import"
                            disabled={imp?.state === 'busy' || device.busy !== null}
                            onClick={() => { void importEntry(e); }}>
                            <Download size={15} aria-hidden="true" focusable={false} />
                            {imp?.state === 'busy' ? t('Importing…') : t('Import')}
                          </button>
                        </>
                      );
                    })()}
                    {entryMenu(e, styles.rowMenu)}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
