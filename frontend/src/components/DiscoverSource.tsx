import { useEffect, useId, useState } from 'react';
import { useDiscoverSource, useMe, useSaveDiscoverSource, type DiscoverSourceSettings } from '../lib/queries';
import { useT } from '../lib/i18n';
import { Button } from './Button';
import styles from './DiscoverSource.module.css';

export function DiscoverSource() {
  const me = useMe().data;
  const query = useDiscoverSource();
  const t = useT();
  if (!me || me.role.anonymous) return null;
  if (query.error) return <div className={styles.box}><p role="alert">{t('Could not load Discover sources.')}</p>
    <Button variant="ghost" onClick={() => void query.refetch()}>{t('Retry')}</Button></div>;
  if (!query.data) return <p role="status">{t('Loading…')}</p>;
  return <SourceForm key={me.id} settings={query.data} />;
}

function SourceForm({ settings }: { settings: DiscoverSourceSettings }) {
  const t = useT();
  const id = useId();
  const update = useSaveDiscoverSource();
  const [choice, setChoice] = useState(settings.source);
  const [edited, setEdited] = useState(false);
  const [status, setStatus] = useState('');
  const [failed, setFailed] = useState(false);
  useEffect(() => { if (!edited) setChoice(settings.source); }, [settings.source, edited]);
  const unavailable = !settings.sources.some(source => source.value === choice);
  return <form className={styles.box} aria-busy={update.isPending} onSubmit={event => {
    event.preventDefault(); setStatus(''); setFailed(false);
    update.mutate(choice, {
      onSuccess: data => { setChoice(data.source); setEdited(false); setStatus(t('Discover source saved.')); },
      onError: () => { setFailed(true); setStatus(t('Could not save Discover source. Please try again.')); },
    });
  }}>
    <div className={styles.controls}>
      <label htmlFor={id}>{t('Discover source')}</label>
      <select id={id} value={choice} disabled={update.isPending}
        aria-invalid={failed || unavailable || undefined} aria-describedby={`${id}-hint ${id}-status`}
        onChange={event => { setChoice(event.target.value); setEdited(true); setStatus(''); setFailed(false); }}>
        {settings.sources.map(source => <option key={source.value} value={source.value}>
          {source.kind === 'library' ? t('Current library') : source.kind === 'smart'
            ? t('Smart shelf: {name}', { name: source.name }) : t('Shelf: {name}', { name: source.name })}
        </option>)}
        {unavailable && <option value={choice} disabled>{t('Unavailable source')}</option>}
      </select>
      <Button type="submit" disabled={update.isPending || unavailable}>{update.isPending ? t('Saving…') : t('Save Discover source')}</Button>
    </div>
    <p id={`${id}-hint`} className={styles.hint}>{t('Only Discover picks change; books remain in your library.')}</p>
    {!settings.available && <p className={styles.warning}>{t('This Discover source is unavailable. Choose another source.')}</p>}
    <p id={`${id}-status`} role="status" className={failed ? styles.error : styles.status}>{status}</p>
  </form>;
}
