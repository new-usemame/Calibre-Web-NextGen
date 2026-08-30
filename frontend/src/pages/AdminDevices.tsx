import { Link } from 'wouter';
import { Fragment, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, Server, Users } from 'lucide-react';
import { apiGet } from '../lib/api';
import { clampOffset } from '../lib/pagination';
import { relativeWhen } from '../lib/relativeTime';
import { useT } from '../lib/i18n';
import { type Device } from '../components/DeviceInventory';
import { DeviceSummary } from '../components/DeviceSummary';
import { EmptyState } from '../components/EmptyState';
import { SpinnerCentered } from '../components/Spinner';
import styles from './AdminDevices.module.css';

interface AdminDevice extends Device {
  user: { id: number; name: string };
}

interface AdminDevicePage {
  devices: AdminDevice[];
  limit: number;
  offset: number;
  total: number;
}

const ADMIN_DEVICE_PAGE_SIZE = 50;

export function AdminDevices() {
  const t = useT();
  const [offset, setOffset] = useState(0);
  const { data, isLoading, error } = useQuery<AdminDevicePage>({
    queryKey: ['admin-devices', offset],
    queryFn: () => apiGet(
      `/api/admin/devices?limit=${ADMIN_DEVICE_PAGE_SIZE}&offset=${offset}`,
    ),
  });
  const correctedOffset = data
    ? clampOffset(offset, data.total, ADMIN_DEVICE_PAGE_SIZE)
    : offset;
  const stalePage = correctedOffset !== offset;
  useEffect(() => {
    if (stalePage) setOffset(correctedOffset);
  }, [correctedOffset, stalePage]);
  if (isLoading || stalePage) return <SpinnerCentered size={40} />;
  const devices = data?.devices ?? [];
  // The API sorts by user, so a user's devices are contiguous on the page;
  // regroup here to render a header per account (presentational only).
  const groups: { user: AdminDevice['user']; devices: AdminDevice[] }[] = [];
  for (const device of devices) {
    const last = groups[groups.length - 1];
    if (last && last.user.id === device.user.id) last.devices.push(device);
    else groups.push({ user: device.user, devices: [device] });
  }
  // Card titles sit one heading level under the group header when accounts are
  // grouped, and directly under the page h1 when a single account fills the page.
  const DeviceTitle = groups.length > 1 ? 'h3' : 'h2';
  return (
    <main className={styles.container}>
      <Link href="/admin" className={styles.back}>
        <ChevronLeft size={16} aria-hidden="true" focusable={false} /> {t('Admin')}
      </Link>
      <div className={styles.heading}>
        <Server aria-hidden="true" focusable={false} />
        <h1>{t('Device administration')}</h1>
      </div>
      {error ? (
        <EmptyState message={t('Could not load the device board.')} />
      ) : devices.length === 0 ? (
        <EmptyState message={t('No registered devices.')} />
      ) : (
        <>
          <p role="status" className={styles.countLine}>{t('Page {page} of {pages}', {
            page: Math.floor(offset / ADMIN_DEVICE_PAGE_SIZE) + 1,
            pages: Math.max(1, Math.ceil((data?.total ?? 0) / ADMIN_DEVICE_PAGE_SIZE)),
          })}</p>
          <ul className={styles.list} role="list" data-testid="admin-device-list">
            {groups.map((group) => (
              <Fragment key={group.user.id}>
                {groups.length > 1 && (
                  <li className={styles.groupHeader}>
                    <h2>{group.user.name}</h2>
                  </li>
                )}
                {group.devices.map((device) => (
                  <li key={device.public_id} className={styles.card} data-active={device.active}>
                    <header>
                      <div>
                        <DeviceTitle>{device.label}</DeviceTitle>
                        <p className={styles.account}>
                          <Users size={13} aria-hidden="true" focusable={false} />
                          {t('Account: {name}', { name: device.user.name })}
                        </p>
                      </div>
                      <span className={device.active ? styles.stateOk : styles.stateMuted}>
                        {device.active ? t('Active') : t('Inactive')}
                      </span>
                    </header>
                    <DeviceSummary device={device} />
                    <p className={styles.lastSeen}>{t('Last seen: {when}', {
                      when: device.last_seen ? relativeWhen(device.last_seen) : t('Never'),
                    })}</p>
                  </li>
                ))}
              </Fragment>
            ))}
          </ul>
          {(data?.total ?? 0) > ADMIN_DEVICE_PAGE_SIZE && (
            <nav className={styles.pagination} aria-label={t('Device administration')}>
              <button type="button" disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - ADMIN_DEVICE_PAGE_SIZE))}>
                {t('Previous')}
              </button>
              <span>{t('Page {page} of {pages}', {
                page: Math.floor(offset / ADMIN_DEVICE_PAGE_SIZE) + 1,
                pages: Math.ceil((data?.total ?? 0) / ADMIN_DEVICE_PAGE_SIZE),
              })}</span>
              <button type="button"
                disabled={offset + ADMIN_DEVICE_PAGE_SIZE >= (data?.total ?? 0)}
                onClick={() => setOffset(offset + ADMIN_DEVICE_PAGE_SIZE)}>
                {t('Next')}
              </button>
            </nav>
          )}
        </>
      )}
    </main>
  );
}
