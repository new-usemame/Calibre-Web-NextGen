import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { ApiError, apiPost } from '../lib/api';
import { useT } from '../lib/i18n';
import { Button } from './Button';
import styles from './FileIsbnSuggestions.module.css';

export interface FileIsbnResult {
  available: boolean;
  unavailable_formats: string[];
  failed_formats: string[];
  candidates: { isbn: string; context: string; format: string }[];
  scanned_formats: string[];
  unsupported_formats: string[];
  truncated: boolean;
}

export function FileIsbnSuggestions({ bookId, onUse, disabled = false }:
  { bookId: string | number; onUse: (isbn: string) => void; disabled?: boolean }) {
  const t = useT();
  const [result, setResult] = useState<FileIsbnResult | null>(null);
  const [message, setMessage] = useState('');
  const [failed, setFailed] = useState(false);
  const scan = useMutation({
    mutationFn: () => apiPost<FileIsbnResult>(`/api/v1/books/${bookId}/isbn-candidates`, {}),
    onSuccess: data => { setResult(data); setMessage(data.candidates.length
      ? t('ISBN suggestions found: {n}.', { n: data.candidates.length }) : data.available ? t('No valid ISBN found in the scanned text.') : t('No supported book file could be scanned.')); },
    onError: error => { setFailed(true); setMessage(error instanceof ApiError && error.detail?.code === 'unsupported_storage'
      ? t('ISBN extraction is unavailable for Google Drive books.')
      : error instanceof ApiError && error.detail?.code === 'unsupported_platform'
        ? t('ISBN extraction is unavailable on this server platform.') : t('Could not scan book files. Please try again.')); },
  });
  return <section className={styles.box} aria-label={t('ISBN suggestions from book files')} aria-busy={scan.isPending}>
    <Button variant="ghost" type="button" disabled={disabled || scan.isPending}
      onClick={() => { setResult(null); setMessage(t('Scanning book files…')); setFailed(false); scan.mutate(); }}>
      {scan.isPending ? t('Scanning book files…') : t('Find ISBN in book files')}
    </Button>
    <p className={styles.hint}>{t('Scan EPUB, PDF or text files for ISBNs. Check the edition before using a suggestion.')}</p>
    <p className={styles.hint}>{t('Using a suggestion replaces only ISBN identifiers in this form. Save changes to keep it.')}</p>
    <p role="status" className={failed ? styles.error : styles.status}>{message}</p>
    {result && <>
      {result.scanned_formats.length > 0 && <p className={styles.hint}>{t('Scanned formats: {formats}', { formats: result.scanned_formats.join(', ') })}</p>}
      {result.unsupported_formats.length > 0 && <p className={styles.hint}>{t('Formats not scanned: {formats}', { formats: result.unsupported_formats.join(', ') })}</p>}
      {result.unavailable_formats.length > 0 && <p className={styles.hint}>{t('Unavailable files: {formats}', { formats: result.unavailable_formats.join(', ') })}</p>}
      {result.failed_formats.length > 0 && <p className={styles.hint}>{t('Files that could not be parsed: {formats}', { formats: result.failed_formats.join(', ') })}</p>}
      {result.truncated && <p className={styles.hint}>{t('The scan reached its limit. More ISBNs may be present in the file.')}</p>}
      {result.candidates.length > 0 && <ul className={styles.list}>
        {result.candidates.map(candidate => <li key={candidate.isbn} className={styles.candidate}>
          <div className={styles.text}><strong>{candidate.isbn}</strong><span className={styles.format}>{candidate.format}</span>
            <p dir="auto">{candidate.context}</p></div>
          <Button variant="ghost" type="button" disabled={disabled || scan.isPending} onClick={() => {
            onUse(candidate.isbn); setMessage(t('ISBN added to the form. Save changes to keep it.')); setFailed(false);
          }}>{t('Use ISBN {isbn}', { isbn: candidate.isbn })}</Button>
        </li>)}
      </ul>}
    </>}
  </section>;
}
