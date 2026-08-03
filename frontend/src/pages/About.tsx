import { Info, BookOpen, Users, Layers, Tag } from 'lucide-react';
import { useAbout } from '../lib/queries';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useMe } from '../lib/queries';
import { useT } from '../lib/i18n';
import styles from './About.module.css';

export function About() {
  const { data, isLoading, error } = useAbout();
  const me = useMe().data;
  const isAdmin = !!me?.role?.admin;
  const t = useT();

  if (isLoading) return <SpinnerCentered size={40} />;
  if (error || !data) {
    return (
      <main className={styles.container}>
        <EmptyState message={error instanceof Error ? error.message : t('Could not load stats.')} />
      </main>
    );
  }

  const stats = [
    { label: t('Books'), value: data.counts.books, icon: BookOpen },
    { label: t('Authors'), value: data.counts.authors, icon: Users },
    { label: t('Series'), value: data.counts.series, icon: Layers },
    { label: t('Categories'), value: data.counts.categories, icon: Tag },
  ];

  return (
    <main className={styles.container}>
      <div className={styles.header}>
        <Info size={22} className={styles.headerIcon} />
        <h1 className={styles.title}>{t('Statistics')}</h1>
      </div>

      <div className={styles.statGrid}>
        {stats.map(({ label, value, icon: Icon }) => (
          <div key={label} className={styles.statCard}>
            <Icon size={20} className={styles.statIcon} />
            <span className={styles.statValue}>{value.toLocaleString()}</span>
            <span className={styles.statLabel}>{label}</span>
          </div>
        ))}
      </div>

      {isAdmin && (
      <div>
      <h2 className={styles.subTitle}>{t('Versions')}</h2>
      <dl className={styles.versions}>
        {Object.entries(data.versions).map(([name, ver]) => (
          <div key={name} className={styles.verRow}>
            <dt className={styles.verName}>{name}</dt>
            <dd className={styles.verVal}>{ver}</dd>
          </div>
        ))}
      </dl>
      </div>
      )}
    </main>
  );
}
