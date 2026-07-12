import { useState, useCallback } from 'react';
import { Link } from 'wouter';
import { UploadCloud, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react';
import { useUploadBooks } from '../lib/queries';
import { Button } from '../components/Button';
import type { UploadResult } from '../lib/api';
import { ApiError } from '../lib/api';
import { useT } from '../lib/i18n';
import styles from './Upload.module.css';

export function Upload() {
  const t = useT();
  const upload = useUploadBooks();
  const [dragover, setDragover] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const send = useCallback(
    (files: File[]) => {
      if (files.length === 0 || upload.isPending) return;
      setError(null);
      setResult(null);
      upload.mutate(files, {
        onSuccess: (r) => setResult(r),
        onError: (e) => setError(e instanceof ApiError ? e.message : 'Upload failed.'),
      });
    },
    [upload],
  );

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragover(false);
    if (upload.isPending) return;
    send(Array.from(e.dataTransfer.files));
  };

  return (
    <main className={styles.container}>
      <h1 className={styles.title}>{t('Upload books')}</h1>
      <p className={styles.subtitle}>
        {t("Files are queued for the library's ingest process and appear once imported.")}
      </p>

      <label
        className={dragover ? styles.dropzoneActive : styles.dropzone}
        onDragOver={(e) => { e.preventDefault(); if (!upload.isPending) setDragover(true); }}
        onDragLeave={() => setDragover(false)}
        onDrop={onDrop}
      >
        {upload.isPending ? (
          <Loader2 className={styles.spin} size={40} aria-hidden="true" focusable={false} />
        ) : (
          <UploadCloud size={40} className={styles.dropIcon} aria-hidden="true" focusable={false} />
        )}
        <p className={styles.dropText}>
          {upload.isPending ? t('Uploading…') : t('Drop files here, or click to choose')}
        </p>
        <input
          type="file"
          multiple
          className={styles.fileInput}
          disabled={upload.isPending}
          aria-label={t('Choose books to upload')}
          onChange={(e) => {
            send(Array.from(e.target.files ?? []));
            e.target.value = '';
          }}
        />
      </label>

      <div className={error ? styles.banner : styles.statusEmpty} role="alert">
        {error && <><AlertCircle size={16} aria-hidden="true" focusable={false} /> {error}</>}
      </div>

      <div className={result ? styles.results : styles.statusEmpty} role="status" aria-live="polite">
        {result && <>
          {result.queued.length > 0 && (
            <div className={styles.queued}>
              <p className={styles.resultHeading}>
                <CheckCircle2 size={16} aria-hidden="true" focusable={false} /> {result.queued.length} file
                {result.queued.length !== 1 ? 's' : ''} queued for import
              </p>
              <ul>{result.queued.map((f) => <li key={f}>{f}</li>)}</ul>
            </div>
          )}
          {result.errors.length > 0 && (
            <div className={styles.failed}>
              <p className={styles.resultHeading}>
                <AlertCircle size={16} aria-hidden="true" focusable={false} /> {result.errors.length} file
                {result.errors.length !== 1 ? 's' : ''} rejected
              </p>
              <ul>{result.errors.map((e) => <li key={e.filename}>{e.filename} — {e.error}</li>)}</ul>
            </div>
          )}
          {result.queued.length > 0 && (
            <div className={styles.afterActions}>
              <Link href="/"><Button variant="ghost">{t('Back to library')}</Button></Link>
            </div>
          )}
        </>}
      </div>
    </main>
  );
}
