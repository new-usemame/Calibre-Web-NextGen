import { useCallback, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError, apiDelete, apiUpload } from '../lib/api';
import { useReaderFonts } from '../lib/queries';
import type { ReaderFont } from '../lib/readerFonts';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useT } from '../lib/i18n';
import { Button } from './Button';
import styles from './ReaderFontsAdmin.module.css';

function RemoveFontDialog({ font, busy, error, onClose, onRemove }: {
  font: ReaderFont; busy: boolean; error: string | null; onClose: () => void; onRemove: () => void;
}) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, { onClose });
  return createPortal(<div className={styles.backdrop}>
    <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="remove-reader-font-title" className={styles.dialog} tabIndex={-1}>
      <h3 id="remove-reader-font-title">{t('Remove {name}?', { name: font.label })}</h3>
      <p>{t('Readers using this font return to Book default when they next open a book.')}</p>
      {error && <p className={styles.error}>{error}</p>}
      <div className={styles.buttons}>
        <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>{t('Cancel')}</Button>
        <Button type="button" variant="danger" onClick={onRemove} disabled={busy}>{busy ? t('Removing…') : t('Remove font')}</Button>
      </div>
    </div>
  </div>, document.body);
}

export function ReaderFontsAdmin() {
  const t = useT();
  const announce = useAnnouncer();
  const { data, isLoading, error, refetch } = useReaderFonts();
  const qc = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [fileInvalid, setFileInvalid] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const [removing, setRemoving] = useState<ReaderFont | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const report = (text: string, error: boolean) => {
    setMessage({ text, error });
    announce(text, { assertive: error });
  };
  const refresh = () => Promise.all([
    qc.invalidateQueries({ queryKey: ['reader-fonts'] }),
    qc.invalidateQueries({ queryKey: ['reader-settings'] }),
  ]);
  const upload = useMutation({
    mutationFn: (form: FormData) => apiUpload<{ item: ReaderFont }>('/api/v1/admin/reader/fonts', form),
    onSuccess: async () => {
      await refresh();
      setFile(null); setName(''); setFileInvalid(false);
      if (fileInput.current) fileInput.current.value = '';
      report(t('Font is available in both EPUB readers.'), false);
    },
    onError: (err: Error) => {
      setFileInvalid(err instanceof ApiError && [400, 413].includes(err.status));
      report(err.message || t('Could not upload font.'), true);
    },
  });
  const remove = useMutation({
    mutationFn: (font: ReaderFont) => apiDelete(`/api/v1/admin/reader/fonts/${font.id.replace(/^custom:/, '')}`),
    onSuccess: async () => {
      await refresh(); setRemoving(null); setRemoveError(null);
      report(t('Font removed.'), false);
      queueMicrotask(() => heading.current?.focus());
    },
    onError: (err: Error) => {
      const text = err.message || t('Could not remove font.');
      setRemoveError(text); announce(text, { assertive: true });
    },
  });
  const removePending = useRef(false);
  removePending.current = remove.isPending;
  const closeRemove = useCallback(() => {
    if (!removePending.current) setRemoving(null);
  }, []);
  const submit = (event: React.FormEvent) => {
    event.preventDefault(); setMessage(null); setFileInvalid(false);
    if (!file || !data) return;
    if (file.size > data.limits.max_file_bytes) {
      setFileInvalid(true); report(t('Font file is too large.'), true); return;
    }
    const form = new FormData(); form.append('file', file);
    if (name.trim()) form.append('name', name.trim());
    upload.mutate(form);
  };
  const uploaded = data?.items.filter(font => !font.builtin) ?? [];
  return <section className={styles.section} aria-labelledby="reader-fonts-heading" id="reader-fonts">
    <h2 id="reader-fonts-heading" ref={heading} tabIndex={-1} className={styles.heading}>{t('Reader fonts')}</h2>
    <p className={styles.hint}>{t('Upload fonts once for everyone using the EPUB readers. Existing font choices stay available.')}</p>
    <p className={styles.hint}>{t('Choose fonts you are allowed to share with this server’s readers. Files stay in the configuration volume across upgrades.')}</p>
    {isLoading ? <p role="status">{t('Loading…')}</p> : error ? <div>
      <p role="alert" className={styles.error}>{t('Could not load reader fonts.')}</p>
      <Button variant="ghost" onClick={() => void refetch()}>{t('Retry')}</Button>
    </div> : data && <>
      <form className={styles.form} onSubmit={submit} aria-busy={upload.isPending}>
        <label className={styles.field}>{t('Font file')}
          <input ref={fileInput} type="file" accept=".ttf,.otf,.woff,.woff2" required disabled={upload.isPending}
            aria-invalid={fileInvalid || undefined}
            aria-describedby={`reader-font-file-help${fileInvalid ? ' reader-font-message' : ''}`}
            onChange={e => { setFile(e.target.files?.[0] ?? null); setFileInvalid(false); }} />
          <small id="reader-font-file-help">{t('TTF, OTF, WOFF or WOFF2, up to {n} MiB per file.', { n: data.limits.max_file_bytes / 1024 / 1024 })}</small>
        </label>
        <label className={styles.field}>{t('Display name (optional)')}
          <input value={name} maxLength={80} disabled={upload.isPending} onChange={e => setName(e.target.value)} />
        </label>
        <Button type="submit" disabled={!file || upload.isPending || remove.isPending}>
          {upload.isPending ? t('Uploading…') : t('Upload font')}
        </Button>
      </form>
      {uploaded.length ? <ul className={styles.list} aria-label={t('Uploaded reader fonts')}>
        {uploaded.map(font => <li key={font.id} className={styles.row}>
          <span className={styles.name}>{font.label}</span>
          <Button type="button" variant="ghost" disabled={upload.isPending || remove.isPending}
            onClick={() => { setRemoveError(null); setRemoving(font); }} aria-label={t('Remove {name}', { name: font.label })}>{t('Remove')}</Button>
        </li>)}
      </ul> : <p className={styles.hint}>{t('No uploaded fonts yet.')}</p>}
    </>}
    {message && <p id="reader-font-message" className={`${styles.message} ${message.error ? styles.error : ''}`}>{message.text}</p>}
    {removing && <RemoveFontDialog font={removing} busy={remove.isPending} error={removeError}
      onClose={closeRemove} onRemove={() => remove.mutate(removing)} />}
  </section>;
}
