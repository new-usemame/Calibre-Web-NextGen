/* Global screen-reader announcer (WCAG 2.2 SC 4.1.3 Status Messages).
 *
 * The audit's single most-repeated gap: async status changes — login failure,
 * save banners, validation errors, result counts, load-more — never reached
 * screen readers because nothing was in an aria-live region. This provides ONE
 * app-wide pair of live regions and a `useAnnouncer()` hook so any component can
 * announce without adding its own region.
 *
 *   const announce = useAnnouncer();
 *   announce(t('Saved.'));                       // polite (default)
 *   announce(t('Login failed.'), { assertive: true });  // interrupts
 *
 * See ~/.claude/skills/CWNG_a11y/references/patterns.md → "Live-region announce".
 */
import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from 'react';

export type AnnounceFn = (message: string, opts?: { assertive?: boolean }) => void;

const AnnouncerContext = createContext<AnnounceFn>(() => {});

export function AnnouncerProvider({ children }: { children: ReactNode }) {
  const [polite, setPolite] = useState('');
  const [assertive, setAssertive] = useState('');
  const politeTimer = useRef<number | undefined>(undefined);
  const assertiveTimer = useRef<number | undefined>(undefined);

  const announce = useCallback<AnnounceFn>((message, opts) => {
    const [set, timer] = opts?.assertive
      ? [setAssertive, assertiveTimer]
      : [setPolite, politeTimer];
    // Clear first, then set after a tick: an identical consecutive message is a
    // no-op to the DOM and would NOT be re-announced otherwise.
    set('');
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => set(message), 60);
  }, []);

  return (
    <AnnouncerContext.Provider value={announce}>
      {children}
      {/* Two regions: polite waits for a pause, assertive interrupts. Both
          aria-atomic so the full message is read, not just the diff. */}
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {polite}
      </div>
      <div aria-live="assertive" aria-atomic="true" role="alert" className="sr-only">
        {assertive}
      </div>
    </AnnouncerContext.Provider>
  );
}

/** Announce a status message to screen readers via the app-wide live regions. */
export function useAnnouncer(): AnnounceFn {
  return useContext(AnnouncerContext);
}
