import { useEffect, useState } from 'react';
import { CheckCircle2, Copy, RefreshCw, Trash2 } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { apiUrl, ApiError } from '../lib/api';
import {
  useCreateKoboSyncToken, useDeleteKoboSyncToken, useKoboSyncToken,
} from '../lib/queries';
import {
  koboConfigLine, latestPairingDevice, pairingVisibility,
} from '../lib/koboPairing';
import { relativeWhen } from '../lib/relativeTime';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useT } from '../lib/i18n';
import type { Device } from './DeviceInventory';
import styles from '../pages/Devices.module.css';

async function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const input = document.createElement('textarea');
  input.value = value;
  input.setAttribute('readonly', '');
  input.style.position = 'fixed';
  input.style.opacity = '0';
  document.body.appendChild(input);
  input.select();
  const copied = document.execCommand('copy');
  input.remove();
  if (!copied) throw new Error('copy failed');
}

export function KoboPairing({ devices, enabled }: { devices: Device[]; enabled: boolean }) {
  const t = useT();
  const announce = useAnnouncer();
  const queryClient = useQueryClient();
  // Viewing and revoking an existing credential remain available if an admin
  // later disables stock-Kobo sync. Only token creation follows that flag.
  const token = useKoboSyncToken();
  const createToken = useCreateKoboSyncToken();
  const deleteToken = useDeleteKoboSyncToken();
  const [copied, setCopied] = useState<'kobo' | 'koreader' | null>(null);
  const [copyError, setCopyError] = useState('');
  const seen = latestPairingDevice(devices);
  const mutationError = createToken.error ?? deleteToken.error;
  const configured = !!token.data?.configured && !!token.data.sync_url;
  const visibility = pairingVisibility(enabled, configured);
  const serverUrl = token.data?.server_url
    ?? new URL(apiUrl('/'), window.location.origin).toString().replace(/\/$/, '');

  // The Devices route first renders a loader, so the browser's initial hash
  // scroll happens before this target exists. Re-apply it when the pairing
  // section actually mounts; Account's direct pairing link then lands here.
  useEffect(() => {
    if (window.location.hash !== '#kobo-pairing') return undefined;
    const frame = window.requestAnimationFrame(() => {
      document.getElementById('kobo-pairing')?.scrollIntoView();
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const copy = async (kind: 'kobo' | 'koreader', value: string) => {
    setCopyError('');
    try {
      await copyText(value);
      setCopied(kind);
      announce(t('Copied to clipboard.'));
    } catch {
      setCopied(null);
      setCopyError(t('Could not copy. Select the address and copy it manually.'));
    }
  };

  const revoke = () => {
    if (!window.confirm(t('Delete this Kobo sync URL? The current URL will stop working on every paired Kobo.'))) return;
    deleteToken.mutate(undefined, {
      onSuccess: () => announce(t('Kobo sync URL deleted.')),
      onError: () => announce(t('Could not delete the Kobo sync URL.'), { assertive: true }),
    });
  };

  const checkAgain = async () => {
    await queryClient.refetchQueries({ queryKey: ['annotation-devices'], type: 'active' });
    const refreshedDevices = queryClient
      .getQueriesData<{ devices: Device[] }>({ queryKey: ['annotation-devices'] })
      .flatMap(([, page]) => page?.devices ?? []);
    const refreshedSeen = latestPairingDevice(refreshedDevices);
    if (refreshedSeen?.public_id === seen?.public_id
        && refreshedSeen?.last_seen === seen?.last_seen) {
      announce(t('No new device check-in yet.'));
    }
  };

  return (
    <section id="kobo-pairing" className={styles.pairing} aria-labelledby="kobo-pairing-title">
      <h2 id="kobo-pairing-title">{t('Pair a Kobo or KOReader')}</h2>
      <p className={styles.pairingIntro}>
        {t('Connect this account to an e-reader, then sync once to confirm it reached your library.')}
      </p>
      {mutationError && (
        <p role="alert" className={styles.pairingAlert}>
          {mutationError instanceof ApiError ? mutationError.message : t('Could not update pairing details.')}
        </p>
      )}

      {token.data?.is_localhost && (
        <p role="alert" className={styles.pairingAlert}>
          {t('Open NextGen using an address your e-reader can reach before copying these settings. Localhost only points back to the device itself.')}
        </p>
      )}

      <div className={styles.pairingColumns}>
        <div>
          <h3>{t('Stock Kobo')}</h3>
          {token.isLoading ? (
            <p role="status" className={styles.pairingStatus}>{t('Loading pairing details…')}</p>
          ) : token.error && !token.data ? (
            <div>
              <p role="alert" className={styles.pairingAlert}>
                {token.error instanceof ApiError ? token.error.message : t('Could not load pairing details.')}
              </p>
              <button type="button" className={styles.primaryButton} onClick={() => void token.refetch()}>
                {t('Try again')}
              </button>
            </div>
          ) : visibility.showStockSetup && token.data?.sync_url ? (
            <>
              {!enabled && (
                <p role="status" className={styles.pairingStatus}>{t('Kobo sync is not enabled on this server.')}</p>
              )}
              <div className={styles.pairingUrlBlock}>
                <p className={styles.pairingLabel}>{t('Stock Kobo sync URL')}</p>
                <div className={styles.pairingUrlRow}>
                  <code>{token.data.sync_url}</code>
                  <button type="button" onClick={() => void copy('kobo', token.data!.sync_url!)}>
                    <Copy size={16} aria-hidden="true" focusable={false} />
                    {copied === 'kobo' ? t('Copied') : t('Copy sync URL')}
                  </button>
                </div>
              </div>
              <ol>
                <li>{t('Connect the Kobo to a computer and open .kobo/Kobo/Kobo eReader.conf in a text editor.')}</li>
                <li>{t('Add or replace this line in the [OneStoreServices] section:')}</li>
              </ol>
              <code className={styles.configLine}>{koboConfigLine(token.data.sync_url)}</code>
              <ol start={3}>
                <li>{t('Save the file, safely eject the Kobo, then restart it.')}</li>
                <li>{t('Tap Sync on the Kobo. Your NextGen books should appear.')}</li>
              </ol>
              <button type="button" className={styles.deleteTokenButton}
                disabled={deleteToken.isPending} onClick={revoke}>
                <Trash2 size={16} aria-hidden="true" focusable={false} />
                {deleteToken.isPending ? t('Deleting…') : t('Delete sync URL')}
              </button>
            </>
          ) : visibility.canGenerateStockToken ? (
            <div className={styles.pairingStart}>
              <p>{t('Generate a private sync URL for this account. You can delete it at any time.')}</p>
              <button type="button" className={styles.primaryButton}
                disabled={createToken.isPending} onClick={() => createToken.mutate(undefined, {
                  onSuccess: () => announce(t('Kobo sync URL ready.')),
                })}>
                {createToken.isPending ? t('Generating…') : t('Generate sync URL')}
              </button>
            </div>
          ) : (
            <p role="status" className={styles.pairingStatus}>{t('Kobo sync is not enabled on this server.')}</p>
          )}
        </div>

        {visibility.showKoreader && (
          <div>
            <h3>{t('KOReader')}</h3>
            <ol>
              <li><a href={apiUrl('/kosync')}>{t('Install or update the NextGen Sync plugin.')}</a></li>
              <li>{t('In KOReader, open NextGen Progress Sync and choose Set NextGen Server.')}</li>
              <li>{t('Paste this server address (the plugin adds /kosync itself):')}</li>
            </ol>
            <div className={styles.pairingUrlRow}>
              <code>{serverUrl}</code>
              <button type="button" onClick={() => void copy('koreader', serverUrl)}>
                <Copy size={16} aria-hidden="true" focusable={false} />
                {copied === 'koreader' ? t('Copied') : t('Copy server address')}
              </button>
            </div>
            <ol start={4}>
              <li>{t('Choose Login and sign in with this NextGen account or one of its app passwords.')}</li>
              <li>{t('Run a sync from the plugin.')}</li>
            </ol>
          </div>
        )}
      </div>

      <div className={seen ? styles.deviceSeen : styles.deviceWaiting}>
        {seen ? <CheckCircle2 size={20} aria-hidden="true" focusable={false} /> : null}
        <p role="status">
          {seen
            ? t('Device seen: {name}, last synced {when}. Pairing is working.', {
              name: seen.label, when: relativeWhen(seen.last_seen),
            })
            : t('Waiting for a Kobo or KOReader to sync with this account.')}
        </p>
        <button type="button" onClick={() => void checkAgain()}>
          <RefreshCw size={16} aria-hidden="true" focusable={false} /> {t('Check again')}
        </button>
      </div>
      <p className={styles.copyError} role="alert">{copyError}</p>
    </section>
  );
}
