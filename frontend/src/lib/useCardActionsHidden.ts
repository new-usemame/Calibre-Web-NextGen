import { usePersistentBool } from './usePersistentBool';

/** Storage key for the book-card action-row preference (fork #1054). Exported
 *  so every card surface and its tests name it in exactly one place — the key
 *  is the contract between the toggle in the catalog's View settings and the
 *  five other surfaces that render the same card. */
export const CARD_ACTIONS_HIDDEN_KEY = 'cwng:card-actions-hidden-v1';

/** Whether the "Read now" + edit row is switched off on book cards.
 *
 *  @Glennza1962 asked for it in #1054 — "it makes the main page look messy [...]
 *  many users are reading on their ereaders, so Read Now is redundant". Default
 *  false, so the row stays on unless a user turns it off.
 *
 *  Owned by the PAGE, never by BookCard itself: usePersistentBool seeds useState
 *  from localStorage once, so a per-card copy would keep its stale value when the
 *  toggle flips and the grid would only update on reload. */
export function useCardActionsHidden() {
  return usePersistentBool(CARD_ACTIONS_HIDDEN_KEY, false);
}
