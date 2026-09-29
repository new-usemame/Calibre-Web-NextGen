/* Wording shared by the two acquisition pages.
 *
 * The runtime reasons used to be translated on the user's page and printed
 * raw on the administrator's — so the person who can actually *fix*
 * `ingest_unwritable` was the one shown the machine code, while the person
 * who can do nothing about it got the sentence. One mapping, both pages.
 *
 * This is a .ts module rather than a page-local hook because
 * scripts/extract_spa_strings.py scans .ts as well as .tsx, so the literals
 * below stay anchored in cps/spa_strings.py and survive re-extraction.
 */
import { useCallback } from 'react';

import { useT } from './i18n';

/** Why the server says acquisition cannot run. Deliberately specific: "the
 *  scheduler is not running" and "the ingest folder is not writable" need
 *  different actions, and a single generic sentence hides which one applies.
 *
 *  An unrecognised reason falls through to the raw code rather than being
 *  swallowed: a newer server inventing a reason should still say something,
 *  and a machine code an administrator can search for beats silence. */
export function useRuntimeReasonText(): (reason: string) => string {
  const t = useT();
  return useCallback((reason: string) => {
    switch (reason) {
      case 'scheduler_unavailable': return t('The background scheduler is not running.');
      case 'migration_unavailable': return t('The acquisition tables are not ready.');
      case 'formats_disabled': return t('No accepted upload format allows EPUB or PDF.');
      case 'key_unavailable': return t('The acquisition key is missing.');
      case 'repository_unavailable': return t('The acquisition database could not be opened.');
      case 'ingest_unwritable': return t('The ingest folder is not writable.');
      case 'ingest_unavailable': return t('The ingest folder could not be found.');
      case 'library_unavailable': return t('The Calibre library folder could not be found.');
      case 'ingest_service_unavailable': return t('The ingest service is not running.');
      default: return reason;
    }
  }, [t]);
}
