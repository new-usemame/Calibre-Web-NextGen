import { useMemo, type MouseEvent } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { Link } from 'wouter';
import type { UserNotice } from '../lib/api';
import { useDismissNotice, useDismissNotices, useNotices } from '../lib/queries';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useT } from '../lib/i18n';
import styles from './UserNotices.module.css';

function noticeTitle(notice: UserNotice, t: ReturnType<typeof useT>) {
  if (notice.type === 'kepub-package-repair') return t('Kobo book compatibility repaired');
  const title = notice.payload.title;
  return typeof title === 'string' && title ? title : t('Library notice');
}

function restoreFocusIfKeyboard(event: MouseEvent<HTMLButtonElement>) {
  if (event.detail === 0) requestAnimationFrame(() => document.getElementById('main')?.focus());
}

function BookLinks({ notices }: { notices: UserNotice[] }) {
  const t = useT();
  const books = Array.from(new Map(
    notices.filter((notice) => notice.book).map((notice) => [notice.book!.id, notice]),
  ).values());
  if (!books.length) return null;
  return (
    <details className={styles.details}>
      <summary>{t('Show affected books')}</summary>
      <ul role="list" className={styles.bookList}>
        {books.map((notice) => (
          <li key={notice.id}>
            <Link href={`/book/${notice.book!.id}`}>
              {notice.book!.title || t('Untitled book')}
            </Link>
          </li>
        ))}
      </ul>
    </details>
  );
}

/*
 * #1748 final invariant: the repair instructions below are the operator-chosen
 * copy. They lead with sync, retain the remove-and-download fallback, and
 * deliberately omit the superseded pre-repair-highlight warning.
 * The squash message records intermediate drafts; these exact literals are the
 * final decision and are pinned by the unit and end-to-end tests.
 */

export function UserNoticeBanner() {
  const t = useT();
  const announce = useAnnouncer();
  const { data } = useNotices();
  const dismissMany = useDismissNotices();
  const notices = data?.notices ?? [];
  const group = useMemo(() => {
    if (!notices.length) return [];
    const firstType = notices[0].type;
    return notices.filter((notice) => notice.type === firstType);
  }, [notices]);
  if (!group.length) return null;

  const first = group[0];
  const affectedBookCount = new Set(
    group.flatMap((notice) => notice.book ? [notice.book.id] : []),
  ).size;
  const dismiss = (event: MouseEvent<HTMLButtonElement>) => {
    const count = group.length;
    dismissMany.mutate(group.map((notice) => notice.id), {
      onSuccess: () => announce(t('{count} notices dismissed permanently.', { count })),
      onError: () => announce(t('Could not dismiss the notices. Please try again.'), { assertive: true }),
    });
    restoreFocusIfKeyboard(event);
  };

  return (
    <section className={styles.banner} aria-labelledby="user-notice-title">
      <AlertTriangle size={18} aria-hidden="true" focusable={false} />
      <div className={styles.content}>
        <strong id="user-notice-title">{noticeTitle(first, t)}</strong>
        {first.type === 'kepub-package-repair' ? (
          <p>{affectedBookCount === 1
            ? t('We repaired this book for your Kobo. If you were having trouble highlighting, try after sync — and if it still doesn’t work, remove the book from your Kobo and let it download again.')
            : t('We repaired {count} books for your Kobo. If you were having trouble highlighting, try after sync — and if it still doesn’t work, remove them from your Kobo and let them download again.', { count: affectedBookCount })}</p>
        ) : (
          <p>{typeof first.payload.message === 'string' ? first.payload.message : t('There is new information about your library.')}</p>
        )}
        <BookLinks notices={group} />
      </div>
      <button type="button" className={styles.dismiss} onClick={dismiss}
        disabled={dismissMany.isPending}
        aria-label={group.length === 1
          ? t('Dismiss permanently')
          : t('Dismiss all {count} notices permanently', { count: group.length })}>
        <X size={17} aria-hidden="true" focusable={false} />
        <span>{group.length === 1 ? t('Dismiss permanently') : t('Dismiss all')}</span>
      </button>
    </section>
  );
}

export function BookUserNotices({ bookId }: { bookId: number }) {
  const t = useT();
  const announce = useAnnouncer();
  const { data } = useNotices(bookId);
  const dismissOne = useDismissNotice();
  const notices = data?.notices ?? [];
  if (!notices.length) return null;

  return (
    <div className={styles.bookNotices}>
      {notices.map((notice) => (
        <section key={notice.id} className={styles.bookNotice}
          aria-labelledby={`book-notice-${notice.id}`}>
          <div>
            <strong id={`book-notice-${notice.id}`}>{noticeTitle(notice, t)}</strong>
            <p>{notice.type === 'kepub-package-repair'
              ? t('We repaired this book for your Kobo. If you were having trouble highlighting, try after sync — and if it still doesn’t work, remove the book from your Kobo and let it download again. If you read it some other way, download it again to get the repaired copy.')
              : (typeof notice.payload.message === 'string'
                ? notice.payload.message : t('There is new information about this book.'))}</p>
          </div>
          <button type="button" className={styles.dismissBook}
            disabled={dismissOne.isPending}
            onClick={(event) => {
              dismissOne.mutate(notice.id, {
                onSuccess: () => announce(t('Notice dismissed permanently.')),
                onError: () => announce(t('Could not dismiss the notice. Please try again.'), { assertive: true }),
              });
              restoreFocusIfKeyboard(event);
            }}>
            {t('Dismiss permanently')}
          </button>
        </section>
      ))}
    </div>
  );
}
