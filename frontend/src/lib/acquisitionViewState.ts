/* Presentation decisions for the acquisition pages.
 *
 * Kept free of imports so the node unit-test lane can exercise it directly,
 * the same way lib/acquisitionJobStates.ts is. Everything here is a decision
 * the two pages were previously making inline, where each one was wrong in a
 * way that is invisible in a screenshot of the happy path:
 *
 *   - a failed read rendered as "nothing here",
 *   - a requester rendered as "a removed account" because a *different*
 *     request had not answered yet,
 *   - a catalog that could not be switched off because switching one *on*
 *     is what the server refuses,
 *   - every row's button disabled because one row's button was clicked.
 *
 * They live together because they are all the same mistake: inferring a fact
 * about the world from the absence of data, rather than from data.
 */

/** What a section's body should render.
 *
 *  `idle` is "this query was never asked" (a feature that is switched off),
 *  which is not the same as "asked and got nothing" (`empty`) and not the
 *  same as "asked and could not be told" (`error`). Collapsing those three
 *  into one blank area is the bug this type exists to prevent. */
export type AcquisitionSectionBody = 'idle' | 'loading' | 'error' | 'empty' | 'ready';

export interface AcquisitionSectionView {
  /** What to draw where the rows go. */
  body: AcquisitionSectionBody;
  /** Whether a failure message belongs beside the section. Separate from
   *  `body` because a refetch can fail while the previous, still-useful rows
   *  are on screen: blanking them would lose information the user had, but
   *  saying nothing would let them act on rows the server has stopped
   *  confirming. Both are wrong, so we keep the rows AND say so. */
  showError: boolean;
}

export interface AcquisitionSectionInput {
  /** False when the query is deliberately not run (react-query `enabled`). */
  enabled?: boolean;
  /** A first load is in flight. */
  isLoading?: boolean;
  /** The query settled in failure. */
  isError?: boolean;
  /** The query has ever produced a payload — including a stale one. */
  hasData?: boolean;
  /** That payload contained no rows. Only meaningful when `hasData`. */
  isEmpty?: boolean;
}

/** Decide what a data-backed section shows.
 *
 *  The invariant worth stating, because every one of the bugs above broke it:
 *  **"nothing here" is only ever shown when the server actually said nothing
 *  is here.** Absence of a payload — because the request failed, because it
 *  is still in flight, because react-query paused it while offline — is never
 *  evidence of emptiness. */
export function acquisitionSectionView(input: AcquisitionSectionInput): AcquisitionSectionView {
  // A query that was never asked has nothing to report, including failure.
  if (input.enabled === false) return { body: 'idle', showError: false };

  // Failure outranks a load in flight: a retry running behind a known failure
  // must not silently hide the failure it is retrying.
  if (input.isError) {
    // Stale rows are still worth showing; an error with nothing behind it is
    // an error, never an "empty".
    const keepStale = !!input.hasData && !input.isEmpty;
    return { body: keepStale ? 'ready' : 'error', showError: true };
  }

  if (input.isLoading) return { body: 'loading', showError: false };

  // Only a payload can justify the empty message.
  if (input.hasData) return { body: input.isEmpty ? 'empty' : 'ready', showError: false };

  // No payload, no error, not flagged as loading: react-query can sit here
  // (a paused/offline fetch). Keep waiting rather than claim emptiness.
  return { body: 'loading', showError: false };
}

/** How the approval queue should name the account behind a request. */
export type AcquisitionRequesterLabel =
  | { kind: 'named'; name: string }
  /** The directory that holds display names has not answered, or answered with
   *  a failure. We know a person asked; we cannot yet say who. */
  | { kind: 'unknown' }
  /** The directory answered and this account is not in it. */
  | { kind: 'removed' };

export interface AcquisitionRequesterInput {
  ownerId: number;
  /** Display names by account id, from the grants query. */
  names?: ReadonlyMap<number, string>;
  /** True only once the grants query has *successfully* produced that map.
   *  Without this the queue — which loads independently and can win the race —
   *  reads every pending request as belonging to a deleted user. */
  resolved?: boolean;
}

export function acquisitionRequesterLabel(
  input: AcquisitionRequesterInput,
): AcquisitionRequesterLabel {
  if (!input.resolved || !input.names) return { kind: 'unknown' };
  if (!input.names.has(input.ownerId)) return { kind: 'removed' };
  const name = (input.names.get(input.ownerId) ?? '').trim();
  // The account exists but carries no usable display name. Saying "removed"
  // would be a lie about a real person an administrator is about to judge.
  return name ? { kind: 'named', name } : { kind: 'unknown' };
}

/** The server's own rule, mirrored: `PATCH …/connections/<id>` answers
 *  `needs_review` only when the request tries to switch a catalog **on** while
 *  the migration is unresolved. Switching one off is always accepted. */
export function connectionEnableRejected(next: boolean, migrationStatus?: string): boolean {
  return next && migrationStatus !== 'ready';
}

export interface ConnectionSwitchInput {
  /** The catalog's current server-side state — i.e. which way the switch moves. */
  currentlyEnabled: boolean;
  migrationStatus?: string;
  /** A write to *this* catalog is in flight. Not "a write to any catalog". */
  pending?: boolean;
}

/** Whether the "Available to users" switch should be disabled.
 *
 *  Disabling it in both directions whenever the migration is unresolved took
 *  away the administrator's ability to withdraw a catalog at exactly the
 *  moment the database is in a state they may be trying to contain. The switch
 *  is only ever blocked in the direction the server would refuse. */
export function connectionSwitchDisabled(input: ConnectionSwitchInput): boolean {
  if (input.pending) return true;
  // Turning off is a safety control and the server never refuses it.
  return connectionEnableRejected(!input.currentlyEnabled, input.migrationStatus);
}

/** Whether a per-row control should show as busy.
 *
 *  One mutation hook serves every row, so `isPending` alone disables the whole
 *  list: testing one catalog took the Test button away from all of them. Only
 *  the row whose variables are in flight is busy. */
export function isRowPending<T>(pending: boolean, active: T | undefined, row: T): boolean {
  return pending && active !== undefined && active === row;
}

/** Whether an imported job produced receipt ids this account may open.
 *
 *  A job can reach `imported` while the requester cannot see the resulting
 *  book. Telling them it is "in your library" with nothing to open reads as a
 *  broken link; the honest statement is that it reached the library. */
export function hasVisibleReceipt(bookIds?: readonly number[]): boolean {
  return !!bookIds && bookIds.length > 0;
}

/* -------------------------------------------------------------------------
 * The browse listing.
 *
 * This is the one read on either page that was still deciding for itself, and
 * it had kept the same mistake in a new shape: it inferred "there is nothing
 * here" from the absence of *listing rows*, then returned before drawing the
 * facets and pagination the very same payload had supplied. A page with no
 * entries but a "next" link is not a dead end; rendering it as one strands
 * the reader with no control except the browser's back button.
 * ---------------------------------------------------------------------- */

/** Only the shape the decisions below read. Kept structural so this module
 *  stays import-free and the node unit lane can exercise it directly. */
export interface AcquisitionCatalogSectionShape {
  publications?: readonly unknown[];
  navigation?: readonly unknown[];
}

export interface AcquisitionCatalogShape extends AcquisitionCatalogSectionShape {
  groups?: readonly AcquisitionCatalogSectionShape[];
  facets?: readonly unknown[];
  pagination?: readonly unknown[];
}

function sectionHasRows(section?: AcquisitionCatalogSectionShape): boolean {
  return ((section?.publications?.length ?? 0) + (section?.navigation?.length ?? 0)) > 0;
}

/** Whether the listing area — the root section plus any groups — has no rows.
 *
 *  Deliberately ignores facets and pagination: those are controls, not
 *  results, and counting them would hide a genuinely empty result behind a
 *  populated-looking page. */
export function catalogListingIsEmpty(catalog: AcquisitionCatalogShape): boolean {
  return !sectionHasRows(catalog) && !(catalog.groups ?? []).some(sectionHasRows);
}

/** Whether the payload carries navigation that must outlive an empty listing.
 *
 *  Facets can widen a filter that matched nothing and pagination can step off
 *  a page that ran out; both are most useful in exactly the case the old code
 *  discarded them. */
export function catalogHasControls(catalog: AcquisitionCatalogShape): boolean {
  return ((catalog.facets?.length ?? 0) + (catalog.pagination?.length ?? 0)) > 0;
}

/** Which "nothing to show" sentence an empty listing has earned.
 *
 *  A search that matched nothing is a fact about the query. Reporting it as
 *  "this catalog page is empty" blames the catalog and invites the reader to
 *  go looking for a fault that is not there. */
export type AcquisitionEmptyListingKind = 'empty-page' | 'no-search-results';

export function catalogEmptyListingKind(query?: string): AcquisitionEmptyListingKind {
  // A submitted query is a search even if it was only spaces: the reader
  // asked a question and the honest answer is "that found nothing".
  return query === undefined || query === '' ? 'empty-page' : 'no-search-results';
}
