import { useState, type MouseEvent as ReactMouseEvent } from 'react';
import { ArrowRight, RotateCcw, Sparkles, X } from 'lucide-react';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { ApiError } from '../lib/api';
import {
  useDismissMyLibraryAdminIntro, useEnableMyLibraryIntro,
  useMyLibraryIntro, useUndoMyLibraryIntro,
} from '../lib/queries';
import styles from './MyLibraryIntro.module.css';

/**
 * Server-wide "Try My Library" intro card for the User administration page.
 *
 * The state machine lives server-side (shared across admins, survives
 * sessions): NOT-ENABLED always shows and offers NO close affordance; ENABLED
 * swaps the pitch for a status line, activates Undo, and gains Close/x-mark
 * (dismiss is permanent); Undo restores the pre-enable snapshot and returns
 * the card to NOT-ENABLED. The enable/undo endpoints do the bulk role+mode
 * work; this component only renders state and reports outcomes.
 */
export function MyLibraryIntro() {
  const t = useT();
  const announce = useAnnouncer();
  const intro = useMyLibraryIntro();
  const enable = useEnableMyLibraryIntro();
  const undo = useUndoMyLibraryIntro();
  const dismiss = useDismissMyLibraryAdminIntro();
  const [error, setError] = useState('');

  const state = intro.data;
  if (!state || state.dismissed) return null;
  const enabled = state.status === 'enabled';
  const busy = enable.isPending || undo.isPending || dismiss.isPending;

  const failText = (err: unknown) =>
    err instanceof ApiError ? err.message : t('Could not complete the action.');

  const onTry = () => {
    setError('');
    enable.mutate(undefined, {
      onSuccess: (data) => {
        announce(data.errors > 0
          ? t('My Library is on for {accounts} accounts; {errors} accounts failed.', {
            accounts: data.accounts - data.errors, errors: data.errors,
          })
          : t('My Library is now on for {accounts} accounts.', { accounts: data.accounts }));
      },
      onError: (err) => {
        const text = failText(err);
        setError(text);
        announce(text, { assertive: true });
      },
    });
  };

  const onUndo = () => {
    setError('');
    undo.mutate(undefined, {
      onSuccess: (data) => {
        announce(t('Restored the previous library mode for {accounts} accounts.', {
          accounts: data.restored_accounts,
        }));
      },
      onError: (err) => {
        const text = failText(err);
        setError(text);
        announce(text, { assertive: true });
      },
    });
  };

  const onDismiss = (event: ReactMouseEvent) => {
    const fromKeyboard = event.detail === 0;
    setError('');
    dismiss.mutate(undefined, {
      onSuccess: () => {
        // The card unmounts; keyboard users need their focus landed somewhere
        // sensible — same restore idiom as the app-wide announcement banner.
        if (fromKeyboard) {
          requestAnimationFrame(() => document.getElementById('main')?.focus());
        }
      },
      onError: (err) => {
        const text = failText(err);
        setError(text);
        announce(text, { assertive: true });
      },
    });
  };

  return (
    <section className={styles.card} role="region" aria-labelledby="mylib-intro-title">
      {enabled && (
        <button type="button" className={styles.close} onClick={onDismiss}
          disabled={busy} aria-label={t('Dismiss introduction')}>
          <X size={16} aria-hidden="true" focusable={false} />
        </button>
      )}
      <div className={styles.headRow}>
        <Sparkles size={18} className={styles.titleIcon} aria-hidden="true" focusable={false} />
        <h2 id="mylib-intro-title" className={styles.title}>{t('New Feature!')}</h2>
      </div>

      {enabled ? (
        <>
          <p className={styles.body}>
            {t('Explore the changes, you can always undo later.')}
          </p>
          <div className={styles.actions}>
            <button type="button" className={styles.soft} onClick={onUndo} disabled={busy}>
              <RotateCcw size={15} aria-hidden="true" focusable={false} />
              {undo.isPending ? t('Undoing…') : t('Undo')}
            </button>
            <button type="button" className={styles.soft} onClick={onDismiss} disabled={busy}>
              {t('Close')}
            </button>
          </div>
        </>
      ) : (
        <>
          <p className={styles.body}>
            {t('Organize your library, your way. Checkout books from the Global Library to bring them into My Library.')}
          </p>
          <ul className={styles.points}>
            <li>
              <ArrowRight size={14} className={styles.pointIcon} aria-hidden="true" focusable={false} />
              <span>{t('Instead of forcing all of your books on friends, they can pick which ones they want!')}</span>
            </li>
            <li>
              <ArrowRight size={14} className={styles.pointIcon} aria-hidden="true" focusable={false} />
              <span>{t('Data hoarder? This will help users organize their accounts!')}</span>
            </li>
          </ul>
          <p className={styles.feedback}>
            {t("You're probably a long time user of calibre-web. Please let us know your feedback on this, why or why not this works with your flow.")}
          </p>
          <div className={styles.actions}>
            <button type="button" className={styles.primary} onClick={onTry} disabled={busy}>
              {enable.isPending ? t('Setting up…') : t('Try My Library')}
            </button>
            {/* Pre-enable Undo is intentionally disabled: it exists only to
                show the escape hatch WILL be there after enabling. */}
            <button type="button" className={styles.soft} disabled>
              <RotateCcw size={15} aria-hidden="true" focusable={false} />
              {t('Undo')}
            </button>
          </div>
          <p className={styles.small}>
            {t('Please give this a try, you can easily undo later.')}
          </p>
        </>
      )}

      {error && <p className={styles.error} role="alert">{error}</p>}
    </section>
  );
}
