import { useEffect, useRef, useState } from 'react';
import { Sparkles, Shuffle, X } from 'lucide-react';
import { BookCard } from './BookCard';
import { Spinner } from './Spinner';
import { useDiscover, useMe } from '../lib/queries';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import styles from './DiscoverSection.module.css';

const STRIP_COUNT = 12;

/** A boxed, visually-distinct strip of random book picks at the top of the
 *  library. Reshuffle for a fresh set, or dismiss with the × (the parent
 *  persists the hidden state and offers a "Show Discover section" toggle). */
export function DiscoverSection({ onClose }: { onClose: () => void }) {
  const t = useT();
  const announce = useAnnouncer();
  const [nonce, setNonce] = useState(0);
  const shuffled = useRef(false);
  const me = useMe().data;
  const configuredCount = me?.display?.random_books;
  const count = configuredCount && configuredCount > 0 ? configuredCount : STRIP_COUNT;
  const { data, isLoading, isFetching } = useDiscover(count, nonce);
  const books = data?.items ?? [];

  useEffect(() => {
    if (shuffled.current && !isFetching) {
      announce(t('Discover picks updated.'));
      shuffled.current = false;
    }
  }, [announce, isFetching, t]);

  // Empty library (or discover returned nothing): render nothing rather than an
  // empty box — there's nothing to discover.
  if (!isLoading && books.length === 0) return null;

  return (
    <section className={styles.box} aria-label={t('Discover')}>
      <div className={styles.head}>
        <div className={styles.titleWrap}>
          <span className={styles.sparkle}><Sparkles size={18} aria-hidden="true" focusable={false} /></span>
          <div className={styles.titleText}>
            <h2 className={styles.title}>{t('Discover')}</h2>
            <p className={styles.sub}>{t('A few random picks from your library')}</p>
          </div>
        </div>
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.iconBtn}
            onClick={() => {
              shuffled.current = true;
              announce(t('Loading new Discover picks.'));
              setNonce((n) => n + 1);
            }}
            disabled={isFetching}
            title={t('Shuffle picks')}
            aria-label={t('Shuffle picks')}
          >
            <Shuffle size={16} className={isFetching ? styles.spin : undefined} aria-hidden="true" focusable={false} />
          </button>
          <button
            type="button"
            className={styles.iconBtn}
            onClick={onClose}
            title={t('Hide Discover section')}
            aria-label={t('Hide Discover section')}
          >
            <X size={16} aria-hidden="true" focusable={false} />
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className={styles.loading}><Spinner size={22} /></div>
      ) : (
        <div className={styles.strip}>
          {books.map((b) => (
            <div className={styles.item} key={b.id}>
              <BookCard book={b} />
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
