import { useNamedPreference, type NamedPreferenceOptions } from './useNamedPreference';

/** Shared contract between the catalogue toggle and every reusable book card. */
export const READING_TAGS_HIDDEN_KEY = 'cwng:reading-tags-hidden-v1';

/** Whether the compact Reading / Read status badges are hidden on book cards.
 * Defaults to false so existing accounts keep today's visible badges. */
export function useReadingTagsHidden(options: NamedPreferenceOptions = {}) {
  return useNamedPreference(
    'reading_tags_hidden', READING_TAGS_HIDDEN_KEY, false, options,
  );
}
