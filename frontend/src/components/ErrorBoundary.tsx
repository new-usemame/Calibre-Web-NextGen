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
import { Component, createRef, type ErrorInfo, type ReactNode } from 'react';
import { useT, type TFunction } from '../lib/i18n';
import styles from './ErrorBoundary.module.css';

/**
 * Render a thrown value as text without ever throwing again.
 *
 * A boundary is a JavaScript runtime seam, not a typed one: `throw null`,
 * `throw 'oops'` and objects with a hostile `toString`/`message` getter are all
 * legal. Since a fallback that throws puts the blank screen straight back, every
 * read of the thrown value goes through here.
 */
function errorText(error: unknown): string {
  try {
    if (error instanceof Error && error.message) return error.message;
    return String(error);
  } catch {
    return 'Unknown error';
  }
}

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
  /* Tracked separately from the thrown value: `throw null` is legal, and keying
   * the fallback off the value's truthiness would re-render the crashing subtree
   * and loop straight back to the blank screen this exists to prevent. */
  hasError: boolean;
  error: unknown;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  private headingRef = createRef<HTMLHeadingElement>();

  static getDerivedStateFromError(error: unknown): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    // Keep the original diagnostics in the console. Previously the crash left
    // nothing on screen AND the user had to be talked through opening devtools
    // to tell us anything; the fallback below now surfaces the message too.
    console.error('[CWNG] Unhandled UI error:', error, info.componentStack);
    // The crash unmounts whatever the user had focused, which would otherwise
    // drop focus to <body> — keyboard and screen-reader users would have to hunt
    // for the recovery controls. RouteA11y can't cover this: it reacts to route
    // changes, and this replaces the current route in place.
    this.headingRef.current?.focus();
  }

  componentDidUpdate(prev: Props) {
    if (this.state.hasError && prev.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false, error: null });
    }
  }

  render() {
    const { hasError, error } = this.state;
    if (!hasError) return this.props.children;

    // Never let the fallback crash over a missing or misbehaving translator.
    const t = (key: string) => {
      try {
        return this.props.t?.(key) || key;
      } catch {
        return key;
      }
    };
    const home = this.props.homeHref || '/';

    return (
      // The fallback IS the whole page at this point, so it owns the main
      // landmark. role="alert" stays on the one concise sentence — putting it on
      // the container would make headings, buttons, link and disclosure a single
      // atomic announcement.
      <main
        className={styles.wrap}
        aria-labelledby="app-error-title"
        data-testid="app-error-boundary"
      >
        <div className={styles.card}>
          <h1 id="app-error-title" ref={this.headingRef} tabIndex={-1} className={styles.title}>
            {t('Something went wrong')}
          </h1>
          <p className={styles.body} role="alert">
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
            <pre className={styles.pre}>{errorText(error)}</pre>
          </details>
        </div>
      </main>
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
