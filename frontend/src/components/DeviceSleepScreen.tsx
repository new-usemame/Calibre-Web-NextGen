import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiDelete, apiGet, apiUpload, resourceUrl } from '../lib/api';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { AnnouncerProvider, useAnnouncer } from '../lib/a11y/announcer';
import { useT, type TFunction } from '../lib/i18n';
import { relativeWhen } from '../lib/relativeTime';
import type { Device } from './DeviceInventory';
import styles from './DeviceSleepScreen.module.css';

export interface SleepScreenMetadata {
  desired_revision: string;
  state: 'none' | 'waiting_for_device' | 'image' | 'remove';
  support: 'unknown' | 'supported' | 'unsupported';
  platform: string | null;
  plugin_version: string | null;
  screen_width: number | null;
  screen_height: number | null;
  observed_at: string | null;
  applied_revision: string | null;
  applied_status: 'applied' | 'removed' | null;
  applied_at: string | null;
  last_error: string | null;
  preview_url: string | null;
}

function statusText(data: SleepScreenMetadata, t: TFunction): string {
  if (data.last_error) return t('Could not apply sleep screen');
  const acknowledged = data.applied_revision === data.desired_revision;
  if (data.state === 'remove') return acknowledged && data.applied_status === 'removed'
    ? t('Previous settings restored') : t('Removal waiting for sync');
  if (data.support === 'unsupported') return t('This device cannot show a custom KOReader sleep screen.');
  if (data.support === 'unknown') return t('Update the CWNG plugin');
  if (!data.screen_width || !data.screen_height) return t('Waiting for device details');
  if (data.state === 'image' && acknowledged && data.applied_status === 'applied') return t('Applied on device');
  return data.state === 'none' ? '' : t('Waiting for next sync');
}

function RemoveConfirmation({ pending, onCancel, onRemove }: {
  pending: boolean; onCancel: () => void; onRemove: () => void;
}) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, { onClose: onCancel });
  return <div className={styles.scrim}>
    <div ref={ref} className={styles.confirmation} role="alertdialog" aria-modal="true"
      tabIndex={-1} aria-labelledby="sleep-remove-title" aria-describedby="sleep-remove-description">
      <h2 id="sleep-remove-title">{t('Remove sleep screen')}</h2>
      <p id="sleep-remove-description">{t('Remove this image? Your previous KOReader sleep-screen settings will return after the next sync.')}</p>
      <div className={styles.actions}>
        <button type="button" disabled={pending} onClick={onCancel}>{t('Cancel')}</button>
        <button type="button" className={styles.danger} disabled={pending} onClick={onRemove}>{t('Remove sleep screen')}</button>
      </div>
    </div>
  </div>;
}

/** Account-scoped, per-device editor shared by registry cards and device detail.
 * The caller snapshots the selected device and keys this component by its ID. */
export function DeviceSleepScreen({ device, onClose }: { device: Device; onClose: () => void }) {
  // The application announcer is inside the inert background. Reuse the same
  // primitive here, outside that background, for repeated save/error messages.
  return createPortal(<AnnouncerProvider>
    <SleepScreenDialog device={device} onClose={onClose} />
  </AnnouncerProvider>, document.body);
}

function SleepScreenDialog({ device, onClose }: { device: Device; onClose: () => void }) {
  const t = useT();
  const announce = useAnnouncer();
  const queryClient = useQueryClient();
  const dialogRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const busyRef = useRef(false);
  const [file, setFile] = useState<File | null>(null);
  const [localPreview, setLocalPreview] = useState<string | null>(null);
  const [inputError, setInputError] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const close = useCallback(() => { if (!busyRef.current) onClose(); }, [onClose]);
  useEffect(() => {
    // This dialog is portaled outside the app. Keep background controls out of
    // keyboard, touch and accessibility navigation for the modal's lifetime.
    const app = document.getElementById('root');
    if (!app) return;
    const previous = app.inert;
    app.inert = true;
    return () => { app.inert = previous; };
  }, []);
  useFocusTrap(dialogRef, { onClose: close, active: !confirmRemove });
  const queryKey = ['device-sleep-screen', device.public_id];
  const path = `/api/v1/devices/${encodeURIComponent(device.public_id)}/sleep-screen`;
  const metadata = useQuery<SleepScreenMetadata>({
    queryKey, queryFn: () => apiGet(path), retry: false, staleTime: 0,
  });
  useEffect(() => {
    if (!file) { setLocalPreview(null); return; }
    const url = URL.createObjectURL(file);
    setLocalPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const publish = (data: SleepScreenMetadata) => {
    queryClient.setQueryData(queryKey, data);
    void queryClient.invalidateQueries({ queryKey: ['annotation-devices'] });
    setFile(null);
    if (inputRef.current) inputRef.current.value = '';
    setInputError(false);
    announce(statusText(data, t));
  };
  const save = useMutation({
    onMutate: () => {
      setInputError(false);
      return queryClient.cancelQueries({ queryKey });
    },
    mutationFn: (image: File) => {
      const form = new FormData();
      form.append('file', image);
      return apiUpload<SleepScreenMetadata>(path, form, undefined, 'PUT');
    },
    onSuccess: publish,
    onError: () => { setInputError(true); },
  });
  const removal = useMutation({
    onMutate: () => queryClient.cancelQueries({ queryKey }),
    mutationFn: () => apiDelete<SleepScreenMetadata>(path),
    onSuccess: (data) => { publish(data); setConfirmRemove(false); },
    onError: () => { setConfirmRemove(false); setInputError(true); },
  });
  const busy = save.isPending || removal.isPending;
  busyRef.current = busy;
  const cancelRemove = useCallback(() => { if (!busyRef.current) setConfirmRemove(false); }, []);
  const data = metadata.data;
  const unsupported = data?.support === 'unsupported';
  const dimensionsKnown = !!data?.screen_width && !!data.screen_height;
  const preview = localPreview || (data?.preview_url ? resourceUrl(data.preview_url) : null);
  // A white preview frame remains visible before the first choice; no stock
  // Kindle resolution or artwork is inferred for an unreported device.
  const emptyPreview = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="1" height="1"%3E%3Cpath fill="white" d="M0 0h1v1H0z"/%3E%3C/svg%3E';
  const titleId = `sleep-screen-title-${device.public_id}`;
  const helpId = `sleep-screen-help-${device.public_id}`;
  const errorId = `sleep-screen-error-${device.public_id}`;

  return <div className={styles.scrim}>
    <div ref={dialogRef} className={styles.dialog} role="dialog" aria-modal="true" tabIndex={-1}
      aria-labelledby={titleId} aria-describedby={helpId} aria-busy={busy}>
      <h2 id={titleId}>{t('Sleep screen for {name}', { name: device.label })}</h2>
      <div id={helpId} className={styles.help}>
        <p>{t('Shown when this device sleeps in KOReader.')}</p>
        {(data?.platform === 'kindle' || (!data?.platform && /kindle/i.test(device.model || ''))) &&
          <p>{t('The native Kindle lock screen is unchanged.')}</p>}
      </div>
      {metadata.isLoading && <p role="status">{t('Loading…')}</p>}
      {metadata.isError && <div role="alert" className={styles.error}>
        <p>{t('Could not save sleep screen. Try again.')}</p>
        <button type="button" onClick={() => void metadata.refetch()}>{t('Try again')}</button>
      </div>}
      {data && <>
        <div className={styles.preview} style={{ aspectRatio: dimensionsKnown
          ? `${data.screen_width} / ${data.screen_height}` : '3 / 4' }}>
          <img src={preview || emptyPreview} alt={t('Sleep screen preview for {name}', { name: device.label })} />
        </div>
        {dimensionsKnown && <p className={styles.dimensions}>{data.screen_width} × {data.screen_height}</p>}
        <p role="status" className={styles.status}>{statusText(data, t)}</p>
        {data.support === 'unknown' && <p className={styles.help}>{t('Update the CWNG plugin on this device, then sync.')}</p>}
        <p className={styles.help}>{t('Last seen {when}', { when: relativeWhen(device.last_seen) })}</p>
        <p className={styles.help}>{t('Connect this device and use Sync now in KOReader.')}</p>
        <form onSubmit={(event) => {
          event.preventDefault();
          if (file && !busy && !unsupported) save.mutate(file);
        }}>
          <label className={styles.fileLabel} htmlFor={`sleep-screen-file-${device.public_id}`}>{t('Choose an image')}</label>
          <input ref={inputRef} id={`sleep-screen-file-${device.public_id}`} type="file" accept="image/jpeg,image/png"
            disabled={busy || unsupported} aria-invalid={inputError}
            aria-describedby={`${helpId}-file${inputError ? ` ${errorId}` : ''}`}
            onChange={(event) => {
              setInputError(false);
              save.reset(); removal.reset();
              const image = event.currentTarget.files?.[0] || null;
              if (image && (image.size > 10 * 1024 * 1024 || image.size === 0)) {
                setFile(null); setInputError(true);
              } else setFile(image);
            }} />
          <p id={`${helpId}-file`} className={styles.help}>{t('Use a JPEG or PNG image up to 10 MiB.')}</p>
          <p id={errorId} className={inputError ? styles.error : undefined} role="alert">
            {inputError ? t('Could not save sleep screen. Try again.') : ''}
          </p>
          <div className={styles.actions}>
            <button type="button" disabled={busy || unsupported} onClick={() => inputRef.current?.click()}>
              {t(data.preview_url ? 'Replace image' : 'Set sleep screen')}
            </button>
            <button type="submit" className={styles.primary} disabled={!file || busy || unsupported}>{t('Save')}</button>
            <button type="button" disabled={busy} onClick={close}>{t('Cancel')}</button>
          </div>
        </form>
        {data.preview_url && <button type="button" className={styles.remove} disabled={busy} onClick={() => {
          setInputError(false); setConfirmRemove(true);
        }}>{t('Remove sleep screen')}</button>}
      </>}
      {!data && <div className={styles.actions}><button type="button" onClick={close}>{t('Cancel')}</button></div>}
    </div>
    {confirmRemove && <RemoveConfirmation pending={busy} onCancel={cancelRemove}
      onRemove={() => { if (!busyRef.current) removal.mutate(); }} />}
  </div>;
}
