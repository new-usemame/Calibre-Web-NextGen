import { useEffect, useRef, useState } from 'react';
import { ApiError, apiPostDownload } from '../lib/api';
import { useMe } from '../lib/queries';
import { useT } from '../lib/i18n';
import { Button } from './Button';
import styles from './BookListExport.module.css';

export interface BookExportSource {
  source: 'catalog' | 'advanced' | 'shelf' | 'smart_shelf' | 'global';
  id?: number;
  params: Record<string, unknown>;
}

/** `onPendingChange` lets a host menu stay open while a download is being
 *  prepared: closing it would unmount this and abort the request (#2475). */
export function BookListExport({ source, disabled = false, onPendingChange }: {
  source: BookExportSource; disabled?: boolean; onPendingChange?: (pending: boolean) => void;
}) {
  const t = useT();
  const { data: me } = useMe();
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const [failed, setFailed] = useState(false);
  const key = JSON.stringify([me?.id, source]);
  const latest = useRef(key);
  latest.current = key;
  const active = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { setMessage(''); setFailed(false); return () => { active.current?.abort(); }; }, [key]);
  useEffect(() => { onPendingChange?.(pending); }, [pending, onPendingChange]);
  useEffect(() => () => { onPendingChange?.(false); }, [onPendingChange]);
  async function download(format: 'csv' | 'txt') {
    const requested = key;
    const controller = new AbortController(); active.current = controller;
    setPending(true); setFailed(false); setMessage(t('Preparing book list…'));
    try {
      const file = await apiPostDownload('/api/v1/books/export', { ...source, format }, { signal: controller.signal });
      if (!mounted.current || latest.current !== requested) return;
      const url = URL.createObjectURL(file.blob);
      const link = document.createElement('a');
      link.href = url; link.download = file.filename || `calibre-web-books.${format}`;
      document.body.appendChild(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
      setMessage(t('Book list download started.'));
    } catch (error) {
      if (!mounted.current || latest.current !== requested) return;
      setFailed(true);
      setMessage(error instanceof ApiError && error.status === 413
        ? t('Too many books to export. Narrow the filters and try again.')
        : t('Could not export the book list. Please try again.'));
    } finally { if (mounted.current) setPending(false); }
  }
  if (!me || me.role?.anonymous) return null;
  return <div className={styles.box} role="group" aria-label={t('Export this book list')} aria-busy={pending}>
    <div className={styles.actions}>
      <Button variant="ghost" type="button" disabled={disabled || pending} onClick={() => void download('csv')}>{t('Export CSV')}</Button>
      <Button variant="ghost" type="button" disabled={disabled || pending} onClick={() => void download('txt')}>{t('Export TXT')}</Button>
    </div>
    <p className={styles.hint}>{t('Exports all matching books, including pages you have not loaded.')}</p>
    <p role="status" className={failed ? styles.error : styles.status}>{message}</p>
  </div>;
}
