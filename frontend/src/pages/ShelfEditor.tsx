import { useEffect, useState } from 'react';
import { Link, useLocation } from 'wouter';
import { useCreateShelf, useUpdateShelf, useShelf, useMe } from '../lib/queries';
import { ShelfOptions } from '../components/ShelfOptions';
import { Button } from '../components/Button';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import styles from './Shelves.module.css';

export function ShelfEditor({ id }: { id?: string }) {
  const t = useT();
  const [, navigate] = useLocation();
  const me = useMe().data;
  const existing = useShelf(id ?? '');
  const create = useCreateShelf();
  const update = useUpdateShelf(id ?? '');
  const [name, setName] = useState('');
  const [isPublic, setPublic] = useState(false);
  const [koboSync, setKobo] = useState(false);
  const [opdsExpose, setOpds] = useState(false);
  const [seeded, setSeeded] = useState(false);
  const [error, setError] = useState<string>();
  const saving = create.isPending || update.isPending;
  useEffect(() => {
    if (!id || seeded || !existing.data) return;
    setName(existing.data.name);
    setPublic(existing.data.is_public);
    setKobo(existing.data.kobo_sync);
    setOpds(!!existing.data.opds_expose);
    setSeeded(true);
  }, [id, seeded, existing.data]);
  const destination = id ? `/shelf/${id}` : '/shelves';
  if (id && existing.isLoading) return <SpinnerCentered />;
  if (id && (existing.error || !existing.data?.can_edit)) return <div className={styles.container}>
    <Link href={destination}>{t('Back')}</Link><EmptyState message={t('You are not allowed to edit this shelf')} />
  </div>;
  const owner = !id || !!existing.data?.is_owner;
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) { setError(t('Shelf name is required')); return; }
    setError(undefined);
    const payload = { name: name.trim(), is_public: isPublic,
      ...(owner ? { kobo_sync: koboSync } : {}), opds_expose: opdsExpose };
    const callbacks = { onSuccess: (shelf: { id: number }) => navigate(`/shelf/${shelf.id}`),
      onError: (err: Error) => setError(err.message || t('Could not save the shelf.')) };
    if (id) update.mutate(payload, callbacks);
    else create.mutate(payload, callbacks);
  };
  return <div className={styles.container}>
    <Link href={destination}>{t('Back')}</Link>
    <h1 className={styles.title}>{id ? t('Shelf settings') : t('Create shelf')}</h1>
    <form onSubmit={save} className={styles.editor}>
      <label className={styles.field}>{t('Name')}
        <input className={styles.createInput} value={name} maxLength={120} required
          onChange={(e) => setName(e.target.value)} aria-invalid={!!error}
          aria-describedby={error ? 'shelf-error' : undefined} />
      </label>
      <ShelfOptions me={me} owner={owner} canShare={owner ? !!me?.role.share_shelfs : !!me?.role.edit_shelfs}
        isPublic={isPublic} koboSync={koboSync} opdsExpose={opdsExpose}
        onPublic={setPublic} onKobo={setKobo} onOpds={setOpds} />
      <p role="alert" id="shelf-error" className={styles.formError}>{error}</p>
      <div className={styles.actions}>
        <Button type="submit" disabled={saving}>{saving ? t('Saving…') : id ? t('Save changes') : t('Create shelf')}</Button>
        <Link href={destination}>{t('Cancel')}</Link>
      </div>
    </form>
  </div>;
}
