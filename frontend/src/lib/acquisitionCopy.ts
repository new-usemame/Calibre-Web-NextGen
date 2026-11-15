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

/** Safe, actionable protocol failures; no upstream body or URL is displayed. */
export function useAcquisitionErrorText(): (code: string) => string {
  const t = useT();
  return useCallback((code: string) => {
    switch (code) {
      case 'needs_auth': return t('The source or download client rejected its credentials. Ask an administrator to check the credential.');
      case 'source_busy': return t('The source asked us to wait.');
      case 'book_category_unavailable': return t('The indexer does not advertise this book category.');
      case 'search_unavailable': return t('The indexer does not advertise a supported keyword search.');
      case 'client_category_unavailable': return t('This category does not exist in the download client.');
      case 'client_path_mapping_mismatch': return t('The completed-folder mapping does not cover the configured download client folder.');
      case 'client_path_mapping_unverified': return t('The download client did not report a completed folder and category that could be verified.');
      case 'completed_path_unreadable': return t('CWNG cannot read the mapped completed folder. Check its mount and permissions.');
      case 'download_client_unavailable': return t('The download client is unavailable or its settings changed. Make a new selection after an administrator checks it.');
      case 'client_job_stalled': return t('The download client did not finish this download before the waiting limit. Check its queue before retrying.');
      case 'client_job_failed': return t('The download client reported that this download failed.');
      case 'client_job_missing': return t('This job is no longer in the download client queue or history. It will not be submitted again automatically.');
      case 'submission_ambiguous': return t('The submission could not be confirmed. Check the download client queue and history before trying again; CWNG will not blindly send a duplicate.');
      case 'no_usable_book': return t('The completed download contains no usable EPUB or PDF.');
      case 'artifact_unavailable': return t('The chosen book is no longer in the completed download. Restore its original file before trying again.');
      case 'artifact_changed':
      case 'completed_file_changed': return t('The book or completed download changed. Restore its original files before trying again.');
      case 'completed_files_limit':
      case 'completed_books_limit':
      case 'completed_size_limit':
      case 'bundle_files_limit': return t('The completed download exceeds the safe file limits. Choose a smaller release.');
      case 'multiple_books': return t('The completed download contains multiple books. A single-book release is required.');
      case 'unsafe_completed_path': return t('The completed download is outside its mapped folder or uses an unsafe file path.');
      case 'credentials_redirected': return t('A download redirect tried to send a credential to another origin and was blocked.');
      case 'network_not_allowed': return t('The source redirected to a network address that this connection does not allow.');
      case 'unsupported_client_version': return t('This download client API version is not supported. Check the documented supported versions.');
      case 'torrent_already_exists': return t('This torrent already exists outside this request. CWNG will not adopt or change an unrelated client job.');
      case 'untrusted_torrent_tracker': return t('This torrent uses a tracker outside the origins allowed by the administrator.');
      case 'invalid_torrent':
      case 'unsafe_torrent':
      case 'invalid_magnet': return t('The torrent descriptor is unsupported or contains unsafe paths or links.');
      case 'invalid_nzb': return t('The indexer did not return a valid NZB descriptor.');
      default: return t('The transfer did not complete.');
    }
  }, [t]);
}
