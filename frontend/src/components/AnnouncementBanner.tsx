import { useState, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react';
import { LifeBuoy, ArrowUpRight, X } from 'lucide-react';
import { useT, type TFunction } from '../lib/i18n';
import { prioritizeAnnouncements } from '../lib/announcementQueue';
import { KofiMark, KOFI_URL } from './KofiMark';
import styles from './HelpBanner.module.css';

const DISMISS_KEY_PREFIX = 'cwng_banner_dismissed:';
const SUPPORT_PITCH = 'Support us on Ko-fi!';
const SUPPORT_LINK_LABEL = 'Open Ko-fi →';

type AnnouncementVariant = 'help' | 'support';
type AnnouncementClickAction = 'open-url-and-dismiss';

interface Announcement {
  id: string;
  priority: number;
  channel?: string;
  content: (t: TFunction) => ReactNode;
  variant: AnnouncementVariant;
  dismissLabel: string;
  legacyDismissKey?: string;
  clickAction?: AnnouncementClickAction;
  url?: string;
}

/** Add future top-slot announcements here.
 *
 * The queue shows the highest-priority eligible entry. Channel-less entries all
 * remain eligible exactly as declared. For a recurring series, set `channel` and
 * APPEND its new entry after every older entry in that channel: the last-declared
 * entry is deterministically the only one allowed into the queue, regardless of
 * whether an older entry was dismissed. Do not replace or reuse its `id`.
 * Dismissal remains per-id, so dismissing `kofi-support-v1` will NOT pre-dismiss a
 * later `kofi-support-v2`; the new ID gets its own fresh showing and queues at its
 * own priority. */
const ANNOUNCEMENTS: readonly Announcement[] = [
  {
    id: 'kofi-support-v1',
    priority: 100,
    channel: 'kofi',
    variant: 'support',
    dismissLabel: 'Dismiss Ko-fi support message',
    legacyDismissKey: 'cwng_kofi_banner_dismissed_v1',
    clickAction: 'open-url-and-dismiss',
    url: KOFI_URL,
    content: (t) => (
      <>
        <span className={styles.supportIconWrap}><KofiMark /></span>
        <span className={`${styles.text} ${styles.supportText}`}>
          <span className={styles.pitch}>{t(SUPPORT_PITCH)}</span>
          <span className={styles.supportLink}>{t(SUPPORT_LINK_LABEL)}</span>
        </span>
      </>
    ),
  },
  {
    id: 'help-announcement-v1',
    priority: 200,
    variant: 'help',
    dismissLabel: 'Dismiss help announcement',
    legacyDismissKey: 'cwng_help_banner_dismissed_v1',
    content: (t) => (
      <>
        <span className={styles.iconWrap}>
          <LifeBuoy size={17} aria-hidden="true" focusable={false} />
        </span>
        <span className={styles.text}>
          {t('Need to report an issue? Try the new')} <strong>{t('Help menu')}</strong>
          <ArrowUpRight size={15} className={styles.arrow} aria-hidden="true" focusable={false} />
        </span>
      </>
    ),
  },
];

const PRIORITIZED_ANNOUNCEMENTS = prioritizeAnnouncements(ANNOUNCEMENTS);

function dismissalKey(id: string) {
  return `${DISMISS_KEY_PREFIX}${id}`;
}

function persistDismissal(id: string) {
  try { localStorage.setItem(dismissalKey(id), '1'); } catch { /* private mode */ }
}

function initialDismissals() {
  const dismissed = new Set<string>();

  for (const announcement of ANNOUNCEMENTS) {
    try {
      const currentKeyDismissed = localStorage.getItem(dismissalKey(announcement.id)) === '1';
      const legacyKeyDismissed = announcement.legacyDismissKey
        ? localStorage.getItem(announcement.legacyDismissKey) === '1'
        : false;

      if (currentKeyDismissed || legacyKeyDismissed) {
        dismissed.add(announcement.id);
        if (!currentKeyDismissed && legacyKeyDismissed) persistDismissal(announcement.id);
      }
    } catch {
      // Storage can be unavailable in private mode; keep the in-memory queue usable.
    }
  }

  return dismissed;
}

export function AnnouncementBanner() {
  const t = useT();
  const [dismissedIds, setDismissedIds] = useState(initialDismissals);
  const announcement = PRIORITIZED_ANNOUNCEMENTS.find(({ id }) => !dismissedIds.has(id));

  if (!announcement) return null;

  const dismiss = (restoreKeyboardFocus = false) => {
    const hasNextAnnouncement = PRIORITIZED_ANNOUNCEMENTS.some(
      ({ id }) => id !== announcement.id && !dismissedIds.has(id),
    );
    persistDismissal(announcement.id);
    setDismissedIds((current) => new Set(current).add(announcement.id));
    if (restoreKeyboardFocus && !hasNextAnnouncement) {
      requestAnimationFrame(() => document.getElementById('main')?.focus());
    }
  };

  const activate = (event: ReactMouseEvent<HTMLAnchorElement>) => {
    if (announcement.clickAction !== 'open-url-and-dismiss' || !announcement.url) return;
    event.preventDefault();
    window.open(announcement.url, '_blank', 'noopener,noreferrer');
    dismiss(event.detail === 0);
  };

  const activateFromAuxClick = (event: ReactMouseEvent<HTMLAnchorElement>) => {
    if (event.button === 1) activate(event);
  };

  const content = announcement.content(t);
  const variantClass = announcement.variant === 'support' ? styles.supportBanner : '';

  return (
    <div
      className={`${styles.banner} ${variantClass}`}
      role="status"
      data-announcement-id={announcement.id}
    >
      {announcement.clickAction === 'open-url-and-dismiss' && announcement.url ? (
        <a
          className={`${styles.bannerSurface} ${styles.clickableSurface}`}
          href={announcement.url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={activate}
          onAuxClick={activateFromAuxClick}
        >
          {content}
        </a>
      ) : (
        <div className={styles.bannerSurface}>{content}</div>
      )}
      <button
        type="button"
        className={styles.close}
        onClick={(event) => dismiss(event.detail === 0)}
        aria-label={t(announcement.dismissLabel)}
      >
        <X size={16} aria-hidden="true" focusable={false} />
      </button>
    </div>
  );
}
