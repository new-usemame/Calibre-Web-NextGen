/* The acquisition job state machine, as the server actually implements it.
 *
 * Kept free of imports so it can be exercised directly by the node unit-test
 * lane, the same way lib/ereaderWording.ts is: this is a mirror of a backend
 * contract (`services/acquisition/storage.py` and `worker.py`), and getting it
 * wrong fails silently — the page simply stops asking the server for news and
 * the user watches a stale row forever.
 */

/** Terminal success is `imported` — the receipt exists. `staged`/`publishing`
 *  mean bytes are on disk but no book exists yet, which is exactly the
 *  distinction the status line has to keep visible.
 *
 *  Note `source_busy` is deliberately absent: the server only ever writes it
 *  as an `error_code` on a `failed` job, never to the state column. */
export type AcquisitionJobState =
  | 'awaiting_approval'
  | 'awaiting_selection'
  | 'queued'
  | 'resolving'
  | 'downloading'
  | 'staged'
  | 'publishing'
  | 'importing'
  | 'imported'
  | 'failed'
  | 'cancelled'
  | 'rejected';

/** The states on which the server owes nothing further. Everything else is
 *  defined against THIS set rather than against a list of busy states, because
 *  the bug that shape invites is forgetting one: `awaiting_approval` was
 *  missing from the old busy list, so a user whose only request was pending
 *  approval stopped polling and never saw it approved or imported without
 *  reloading. Deriving "still pending" by negation means an unfamiliar state —
 *  including one a newer server invented — keeps the page watching instead of
 *  going quiet. */
export const ACQUISITION_TERMINAL_STATES: ReadonlySet<string> = new Set<AcquisitionJobState>([
  'imported', 'failed', 'cancelled', 'rejected',
]);

/** True while the job may still change, by the worker or by an administrator. */
export function isAcquisitionPending(state: string): boolean {
  return !ACQUISITION_TERMINAL_STATES.has(state);
}

/** Exactly the states `AcquisitionRepository.cancel` accepts. Offering Cancel
 *  outside this set produces a 409 the user did not ask for: once a job reaches
 *  `publishing`/`importing` the bytes are already being handed to the library
 *  and the server refuses to stop it. */
export const ACQUISITION_CANCELLABLE_STATES: ReadonlySet<string> = new Set<AcquisitionJobState>([
  'awaiting_approval', 'awaiting_selection', 'queued', 'resolving', 'downloading', 'staged',
]);
