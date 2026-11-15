import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiGet, apiPut } from '../lib/api';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { Button } from './Button';
import styles from './IngestFolderLabels.module.css';

type Settings = { target: string; nested: boolean; custom_columns: { lookup: string; name: string }[] };
const url = '/api/v1/admin/ingest-folder-label-settings';
const key = ['admin-ingest-folder-labels'];
export function IngestFolderLabels() {
  const t = useT(); const announce = useAnnouncer(); const qc = useQueryClient();
  const query = useQuery({ queryKey: key, queryFn: () => apiGet<Settings>(url), retry: false });
  const [form, setForm] = useState<{ target: string; nested: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => { if (query.data && !form) setForm({ target: query.data.target, nested: query.data.nested }); }, [query.data, form]);
  const update = useMutation({ mutationFn: (values: { target: string; nested: boolean }) => apiPut<Settings>(url, values),
    onSuccess: data => { qc.setQueryData(key, data); setForm({ target: data.target, nested: data.nested }); setSaved(true); announce(t('Ingest folder settings saved.')); },
    onError: (err: Error) => { const message = err.message || t('Could not save ingest folder settings.'); setError(message); announce(message, { assertive: true }); },
  });
  const columns = query.data?.custom_columns ?? [];
  const unavailable = form && !['disabled', 'tags', ...columns.map(c => c.lookup)].includes(form.target);
  return <section id="ingest-folder-labels" className={styles.section} aria-labelledby="ingest-folder-heading">
    <h2 id="ingest-folder-heading">{t('Ingest folder labels')}</h2>
    <p>{t('Add ingest subfolder names to book tags or an existing custom column. Existing values are kept; files in the ingest root add no labels.')}</p>
    <p className={styles.hint}>{t('For example, ingest/James/scifi adds James by default. Include nested folders to add scifi too.')}</p>
    {query.error ? <><p role="alert" className={styles.error}>{t('Could not load ingest folder settings.')}</p><Button variant="ghost" onClick={() => void query.refetch()}>{t('Retry')}</Button></> : !form ? <p role="status">{t('Loading…')}</p> :
      <form onSubmit={event => { event.preventDefault(); setError(null); setSaved(false); update.mutate(form); }} aria-busy={update.isPending}>
        <label htmlFor="ingest-folder-target">{t('Write subfolder names to')}</label>
        <select id="ingest-folder-target" value={form.target} disabled={update.isPending} aria-invalid={!!error || !!unavailable}
          aria-describedby={`ingest-folder-column-help${error ? ' ingest-folder-error' : ''}`}
          onChange={event => { setForm({ ...form, target: event.target.value }); setError(null); setSaved(false); }}>
          <option value="disabled">{t('Disabled')}</option><option value="tags">{t('Tags')}</option>
          {columns.map(column => <option key={column.lookup} value={column.lookup}>{column.name} ({column.lookup})</option>)}
          {unavailable && <option value={form.target} disabled>{t('Unavailable column: {name}', { name: form.target })}</option>}
        </select>
        <p id="ingest-folder-column-help" className={styles.hint}>{t('Custom columns must already exist and use comma-separated text, like tags. Create them in Calibre desktop first.')}</p>
        <label className={styles.checkbox}><input type="checkbox" checked={form.nested} disabled={update.isPending || form.target === 'disabled'}
          onChange={event => { setForm({ ...form, nested: event.target.checked }); setError(null); setSaved(false); }} />{t('Include nested folders')}</label>
        {unavailable && <p className={styles.error}>{t('The selected column is unavailable. Choose another target or disable folder labels before importing.')}</p>}
        {error && <p id="ingest-folder-error" className={styles.error}>{error}</p>}
        <Button type="submit" disabled={update.isPending || !!unavailable}>{update.isPending ? t('Saving…') : t('Save ingest folder settings')}</Button>
        <p>{saved ? t('Ingest folder settings saved.') : ''}</p>
      </form>}
  </section>;
}
