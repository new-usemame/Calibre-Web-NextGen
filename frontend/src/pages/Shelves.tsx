import { Link } from 'wouter';
import { Plus, Globe, Lock, Settings } from 'lucide-react';
import { useShelves, useMe } from '../lib/queries';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import styles from './Shelves.module.css';

export function Shelves() {
  const t = useT();
  const { data, isLoading, error } = useShelves();
  const me = useMe().data;
  return <div className={styles.container}>
    <div className={styles.header}>
      <h1 className={styles.title}>{t('Shelves')}</h1>
      {data && <span className={styles.count}>{data.items.length}</span>}
      {me?.id && !me.role.anonymous && <Link href="/shelves/new" className={styles.createButton}>
        <Plus size={16} aria-hidden="true" focusable={false} /> {t('Create shelf')}
      </Link>}
    </div>
    <p className={styles.hint}>{t('Collect books in shelves. Use settings to rename, share or choose device visibility.')}</p>
    {isLoading ? <SpinnerCentered size={36} /> : error ? <EmptyState message={error.message} />
      : !data?.items.length ? <EmptyState message={t('No shelves yet. Create a shelf to start collecting books.')} /> :
        <ul className={styles.grid}>{data.items.map((shelf) => <li key={shelf.id} className={styles.tile}>
          <Link href={`/shelf/${shelf.id}`} className={styles.card}>
            <div className={styles.cardTop}>
              <span className={styles.shelfName}>{shelf.name}</span>
              <span className={styles.visibility} role="img" aria-label={shelf.is_public ? t('Public shelf') : t('Private shelf')}>
                {shelf.is_public ? <Globe size={14} aria-hidden="true" /> : <Lock size={14} aria-hidden="true" />}
              </span>
            </div>
            <div className={styles.cardBottom}>
              <span className={styles.bookCount}>{shelf.count === 1 ? t('{count} book', { count: shelf.count }) : t('{count} books', { count: shelf.count })}</span>
              {!shelf.is_owner && <span className={styles.sharedBadge}>{t('shared')}</span>}
            </div>
          </Link>
          {shelf.can_edit && <Link href={`/shelf/${shelf.id}/edit`} className={styles.settingsButton}
            aria-label={t('Settings for {name}', { name: shelf.name })}>
            <Settings size={16} aria-hidden="true" focusable={false} /> {t('Settings')}
          </Link>}
        </li>)}</ul>}
  </div>;
}
