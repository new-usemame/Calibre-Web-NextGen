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
import { collectContext, reportTarget } from '../lib/reportBuilder';
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
  /** Component names only — the most useful field for locating a crash, and
   *  safe to publish. Captured so "Report this problem" can precompose it. */
  componentStack?: string;
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
    // Held for the report link below. Guarded because this runs on the crash
    // path: a throw here would replace the recovery UI with the blank screen
    // this boundary exists to prevent.
    try {
      this.setState({ componentStack: info?.componentStack || undefined });
    } catch {
      /* the fallback renders fine without it */
    }
    // The crash unmounts whatever the user had focused, which would otherwise
    // drop focus to <body> — keyboard and screen-reader users would have to hunt
    // for the recovery controls. RouteA11y can't cover this: it reacts to route
    // changes, and this replaces the current route in place.
    this.headingRef.current?.focus();
  }

  componentDidUpdate(prev: Props) {
    if (this.state.hasError && prev.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false, error: null, componentStack: undefined });
    }
  }

  /**
   * The precomposed report URL, or null if composing it fails for any reason.
   *
   * This page is the sharpest case for precomposition in the whole app: it is
   * already holding the error object while asking the user to report it. Before
   * this, both here and the classic error page handed over a blank form and
   * asked the reporter to type out the version and their browser by hand.
   *
   * Nothing is transmitted — `reportTarget` composes a link, and only the user
   * clicking it (in their own GitHub session) posts anything at all.
   */
  private reportHref(): string | null {
    try {
      const ctx = collectContext({
        errorMessage: errorText(this.state.error),
        componentStack: this.state.componentStack,
      });
      return reportTarget('bug', ctx, '').url;
    } catch {
      return null;
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
    const reportHref = this.reportHref();

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
            {reportHref && (
              <p className={styles.reportRow}>
                {/* rel=noreferrer matters here beyond the usual tab-nabbing
                    reason: Referer would hand GitHub the instance URL, which is
                    exactly the fact this feature exists to keep private. */}
                <a
                  className={styles.reportLink}
                  href={reportHref}
                  target="_blank"
                  rel="noopener noreferrer"
                  data-testid="app-error-report"
                >
                  {t('Report this problem')}
                </a>
                <span className={styles.reportNote}>
                  {t('Opens a prefilled report on GitHub. Nothing is sent until you post it, and you can edit it first.')}
                </span>
              </p>
            )}
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
