import { RefreshCw } from 'lucide-react';

import { useT } from '../lib/i18n';
import styles from './SectionError.module.css';

/** A read that failed, said out loud and with a way back.
 *
 *  Every acquisition section used to render a failed read as its empty state,
 *  so "we could not ask the server" and "the server says there is nothing"
 *  looked identical. `role="alert"` because it stands in for content the
 *  reader asked for; the retry is here because reloading the whole page to
 *  re-run one query is a poor answer to a transient 502.
 *
 *  Shared between the admin page and the browse page so the two cannot drift:
 *  a stale-results warning that looks different on each screen reads as two
 *  different problems. */
export function SectionError({ message, onRetry, retrying }: {
  message: string;
  onRetry: () => void;
  retrying?: boolean;
}) {
  const t = useT();
  return (
    <div className={styles.sectionError} role="alert">
      <p>{message}</p>
      <button type="button" className={styles.retry} onClick={onRetry} disabled={retrying}>
        <RefreshCw size={14} aria-hidden="true" focusable={false} />
        <span>{t('Try again')}</span>
      </button>
    </div>
  );
}
