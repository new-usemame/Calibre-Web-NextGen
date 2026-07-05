/* "What's New" unread state.
 *
 * The Help menu shows a subtle dot when the running build has a What's New entry
 * the user hasn't opened yet. We key that to LATEST_WHATS_NEW_VERSION — the
 * newest version baked into the data file — rather than a runtime version query:
 * the data file and the version it announces ship in the same image, so "there's
 * a release you haven't seen" is exactly "the newest logged version != the last
 * one you opened". No network round-trip, no runtime-version dependency.
 *
 * Opening the page calls markWhatsNewSeen(), which persists the version AND fires
 * a same-tab window event so the dot clears live (the native `storage` event only
 * fires in OTHER tabs).
 */
import { useEffect, useState } from 'react';
import { LATEST_WHATS_NEW_VERSION } from '../data/whatsNew';

const SEEN_KEY = 'cwng_whats_new_seen_version';
const SEEN_EVENT = 'cwng:whats-new-seen';

function readSeen(): string | null {
  try {
    return localStorage.getItem(SEEN_KEY);
  } catch {
    return null;
  }
}

/** Record that the user has opened What's New at the current build's newest
 *  version, and notify any live listeners (this tab) so the dot clears. */
export function markWhatsNewSeen(): void {
  if (!LATEST_WHATS_NEW_VERSION) return;
  try {
    localStorage.setItem(SEEN_KEY, LATEST_WHATS_NEW_VERSION);
  } catch {
    /* storage unavailable (private mode / quota) — dot just won't persist */
  }
  try {
    window.dispatchEvent(new CustomEvent(SEEN_EVENT));
  } catch {
    /* no window (SSR/test) — nothing to notify */
  }
}

/** True when the running build's newest What's New entry differs from the one the
 *  user last opened — i.e. there's something to discover. This deliberately lights
 *  once for a user who has never opened the page (an upgrade into this feature, or
 *  a brand-new install): a single subtle nudge to discover it, cleared the moment
 *  they open it. It is discovery, not nagging — it never re-lights until a genuinely
 *  newer release ships. */
export function useWhatsNewUnread(): boolean {
  const [unread, setUnread] = useState(false);

  useEffect(() => {
    if (!LATEST_WHATS_NEW_VERSION) return;
    const evaluate = () => {
      setUnread(readSeen() !== LATEST_WHATS_NEW_VERSION);
    };
    evaluate();
    // Clear/refresh live when this tab marks it seen, and stay consistent across
    // tabs via the native storage event.
    window.addEventListener(SEEN_EVENT, evaluate);
    window.addEventListener('storage', evaluate);
    return () => {
      window.removeEventListener(SEEN_EVENT, evaluate);
      window.removeEventListener('storage', evaluate);
    };
  }, []);

  return unread;
}
