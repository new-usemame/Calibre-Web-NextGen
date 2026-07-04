import { useEffect } from 'react';
import { Sparkles, ArrowRight } from 'lucide-react';
import { Link } from 'wouter';
import { useI18n, type TFunction } from '../lib/i18n';
import { markWhatsNewSeen } from '../lib/whatsNew';
import { WHATS_NEW, type WhatsNewCategory, type WhatsNewRelease } from '../data/whatsNew';
import { EmptyState } from '../components/EmptyState';
import styles from './WhatsNew.module.css';

/** Category → CSS-module chip class. Each category gets a subtle, distinct tint
 *  so the eye can group changes at a glance without a loud legend. */
const CATEGORY_CLASS: Record<WhatsNewCategory, string> = {
  Reading: styles.catReading,
  Library: styles.catLibrary,
  Sync: styles.catSync,
  Account: styles.catAccount,
  Admin: styles.catAdmin,
  'Under the hood': styles.catHood,
};

/** Humanize an ISO date (2026-07-03 → "July 3, 2026") without a date library.
 *  Parsed as UTC so the day stays stable regardless of the viewer's time zone. */
function humanDate(iso: string, locale: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return iso;
  try {
    return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(locale || undefined, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      timeZone: 'UTC',
    });
  } catch {
    return iso;
  }
}

function ReleaseBlock({
  release,
  defaultOpen,
  locale,
  t,
}: {
  release: WhatsNewRelease;
  defaultOpen: boolean;
  locale: string;
  t: TFunction;
}) {
  return (
    <details className={styles.release} open={defaultOpen}>
      <summary className={styles.summary}>
        <span className={styles.node} aria-hidden="true" />
        <span className={styles.releaseHead}>
          <span className={styles.version}>{release.version}</span>
          <span className={styles.date}>{humanDate(release.date, locale)}</span>
          <span className={styles.count}>
            {t(release.items.length === 1 ? '{n} update' : '{n} updates', {
              n: release.items.length,
            })}
          </span>
        </span>
        <ArrowRight size={16} className={styles.summaryChevron} aria-hidden="true" />
      </summary>

      {release.summary && <p className={styles.releaseSummary}>{release.summary}</p>}

      <ul className={styles.items}>
        {release.items.map((item, i) => (
          <li className={styles.item} key={i}>
            <div className={styles.itemTop}>
              <span className={`${styles.chip} ${CATEGORY_CLASS[item.category]}`}>
                {t(item.category)}
              </span>
              <h3 className={styles.itemTitle}>{item.title}</h3>
            </div>
            <p className={styles.itemBody}>{item.body}</p>
            {item.link && (
              <Link href={item.link.to} className={styles.tryLink}>
                {t(item.link.label)}
                <ArrowRight size={14} aria-hidden="true" />
              </Link>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function WhatsNew() {
  const { t, locale } = useI18n();

  // Opening the page = the user has seen everything up to the newest entry, so
  // the Help-menu "unread" dot clears.
  useEffect(() => {
    markWhatsNewSeen();
  }, []);

  return (
    <main className={styles.container}>
      <header className={styles.header}>
        <div className={styles.headerIconWrap} aria-hidden="true">
          <Sparkles size={22} />
        </div>
        <div>
          <h1 className={styles.title}>{t("What's new")}</h1>
          <p className={styles.subtitle}>
            {t('The latest features and fixes in Calibre-Web NextGen — newest first.')}
          </p>
        </div>
      </header>

      {WHATS_NEW.length === 0 ? (
        <EmptyState message={t('No release notes yet — check back after the next update.')} />
      ) : (
        <>
          <div className={styles.timeline}>
            {WHATS_NEW.map((release, i) => (
              <ReleaseBlock
                key={release.version}
                release={release}
                defaultOpen={i === 0}
                locale={locale}
                t={t}
              />
            ))}
          </div>
          <p className={styles.footnote}>
            {t('The interface is translated into your language; these update notes are written in English.')}
          </p>
        </>
      )}
    </main>
  );
}
