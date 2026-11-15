import { useState } from 'react';
import { Link } from 'wouter';
import { Plus, Settings, Eye, EyeOff, Globe, Lock } from 'lucide-react';
import { useMagicShelves, useMagicShelfVisibility, useMe } from '../lib/queries';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import styles from './Shelves.module.css';

export function MagicShelves() {
  const t = useT();
  const announce = useAnnouncer();
  const me = useMe().data;
  const { data, isLoading, error } = useMagicShelves(true);
  const visibility = useMagicShelfVisibility();
  const [actionError, setError] = useState<string>();
  return <div className={styles.container}>
    <div className={styles.header}>
      <h1 className={styles.title}>{t('Smart shelves')}</h1>
      {data && <span className={styles.count}>{data.items.length}</span>}
      {me?.id && !me.role.anonymous && <Link href="/magic/new" className={styles.createButton}>
        <Plus size={16} aria-hidden="true" focusable={false} /> {t('Create smart shelf')}
      </Link>}
    </div>
    <p className={styles.hint}>{t('Smart shelves collect books automatically using rules. Hidden shelves stay here so you can show them again.')}</p>
    <p role="alert" className={styles.formError}>{actionError}</p>
    {isLoading ? <SpinnerCentered size={36} /> : error ? <EmptyState message={error.message} />
      : !data?.items.length ? <EmptyState message={t('No smart shelves yet. Create one to collect books automatically.')} /> :
        <ul className={styles.grid}>{data.items.map((shelf) => <li key={shelf.id} className={styles.tile}>
          <Link href={`/magic/${shelf.id}`} className={styles.card}>
            <div className={styles.cardTop}>
              <span className={styles.shelfName}><span aria-hidden="true">{shelf.icon} </span>{shelf.name}</span>
              <span className={styles.visibility} role="img" aria-label={shelf.is_public ? t('Public shelf') : t('Private shelf')}>
                {shelf.is_public ? <Globe size={14} aria-hidden="true" /> : <Lock size={14} aria-hidden="true" />}
              </span>
            </div>
            <span className={styles.bookCount}>{shelf.is_hidden ? t('Hidden from navigation') : shelf.is_system ? t('Built-in smart shelf') : t('Smart shelf')}</span>
          </Link>
          <div className={styles.tileActions}>
            {shelf.can_edit && <Link href={`/magic/${shelf.id}/edit`} className={styles.settingsButton}
              aria-label={t('Settings for {name}', { name: shelf.name })}>
              <Settings size={16} aria-hidden="true" focusable={false} /> {t('Settings')}
            </Link>}
            {shelf.can_hide && <button className={styles.settingsButton} disabled={visibility.isPending}
              aria-label={shelf.is_hidden ? t('Show {name}', { name: shelf.name }) : t('Hide {name}', { name: shelf.name })}
              onClick={() => {
                setError(undefined);
                visibility.mutate({ id: shelf.id, visible: !!shelf.is_hidden }, {
                  onSuccess: () => announce(t('Shelf visibility saved.')),
                  onError: (e) => setError(e.message),
                });
              }}>
              {shelf.is_hidden ? <Eye size={16} aria-hidden="true" focusable={false} /> : <EyeOff size={16} aria-hidden="true" focusable={false} />}
              {shelf.is_hidden ? t('Show') : t('Hide')}
            </button>}
          </div>
        </li>)}</ul>}
  </div>;
}
