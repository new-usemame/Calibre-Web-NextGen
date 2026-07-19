/*
 * Global crash safety net for the SPA (#855).
 *
 * React's contract: an error thrown during render with NO error boundary above
 * it unmounts the WHOLE tree back to the root node. #root goes empty and all
 * that is left is the bare page background — which is what @monimkxl-web saw as
 * "the screen went black and nothing else. Had to close the browser." There was
 * no in-app way back, because there was no fallback UI and no reload control.
 *
 * This boundary is the root-level fix for that class, not for any one page that
 * happens to throw. It is deliberately dependency-free (hard rule 6) and
 * deliberately trivial: a fallback that itself throws would reintroduce the
 * blank screen, so it reads no app data, calls no hooks, and issues no network
 * requests. It styles itself from theme tokens only, so it renders legibly in
 * every theme without the app chrome that may have just died.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react';
import { useT, type TFunction } from '../lib/i18n';
import styles from './ErrorBoundary.module.css';

interface Props {
  children: ReactNode;
  /** Translator. Optional so the boundary works ABOVE the i18n provider, where
   *  there is no context to read; it then renders its English source strings. */
  t?: TFunction;
  /** When this changes, a displayed error clears. The router passes the current
   *  location, so navigating away from a broken page recovers without a reload. */
  resetKey?: string;
  /** Where "Back to library" points. Full page load, so it recovers even when
   *  the router itself is the thing that threw. */
  homeHref?: string;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the original diagnostics in the console. Previously the crash left
    // nothing on screen AND the user had to be talked through opening devtools
    // to tell us anything; the fallback below now surfaces the message too.
    console.error('[CWNG] Unhandled UI error:', error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    // Identity fallback: never crash the fallback over a missing translator.
    const t = this.props.t ?? ((key: string) => key);
    const home = this.props.homeHref || '/';

    return (
      <div className={styles.wrap} role="alert" data-testid="app-error-boundary">
        <div className={styles.card}>
          <h1 className={styles.title}>{t('Something went wrong')}</h1>
          <p className={styles.body}>
            {t('This page ran into an error and could not be displayed. Your library is fine — reloading usually fixes it.')}
          </p>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.primary}
              onClick={() => window.location.reload()}
            >
              {t('Reload')}
            </button>
            <a className={styles.secondary} href={home}>
              {t('Back to library')}
            </a>
          </div>
          <details className={styles.details}>
            <summary className={styles.summary}>{t('Technical details')}</summary>
            <pre className={styles.pre}>{String(error?.message || error)}</pre>
          </details>
        </div>
      </div>
    );
  }
}

/**
 * Router-side boundary: translated, and self-resetting on navigation so one bad
 * route does not strand the session. Must be rendered inside the i18n provider.
 */
export function RoutedErrorBoundary({
  children,
  location,
  homeHref,
}: {
  children: ReactNode;
  location: string;
  homeHref?: string;
}) {
  const t = useT();
  return (
    <ErrorBoundary t={t} resetKey={location} homeHref={homeHref}>
      {children}
    </ErrorBoundary>
  );
}
