import { useNamedPreference, type NamedPreferenceOptions } from './useNamedPreference';

/** Storage key for the shelf-tag preference on book cards (#1254). */
export const SHELF_BADGES_HIDDEN_KEY = 'cwng:shelf-badges-hidden-v1';

/** Whether the shelf tags on book covers are switched off.
 *
 *  The server stores this where the classic grid's "Hide shelf badges on
 *  covers" toggle already does, so both UIs answer the same way. Default false:
 *  the classic view showed them, and that is what @lguerard asked to get back. */
export function useShelfBadgesHidden(options: NamedPreferenceOptions = {}) {
  return useNamedPreference(
    'shelf_badges_hidden', SHELF_BADGES_HIDDEN_KEY, false, options,
  );
}
