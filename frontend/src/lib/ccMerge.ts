/** The SELECTION rules for the per-row "Merge" button.
 *
 * Kept out of the .tsx because the unit runner cannot import TSX -- same reason
 * `canPasteInto` lives in ccPaste.ts.
 *
 * ## What lives here, and what does not
 *
 * This file used to re-implement the merge guards that
 * `cps/api/cc_values.py` enforces. It no longer does. The rule that only
 * depends on a SINGLE value -- "a merge source must be a leaf" -- is published
 * by the server on every node as `available.can_merge_source`, computed by
 * `hierarchy.node_availability`. This module reads that flag.
 *
 * What genuinely cannot be published is here, because it depends on the
 * SELECTION, which the server cannot see:
 *
 * * The DESTINATION is the row the button was pressed on, and it is excluded
 *   from the sources. Merging a value into itself is refused server-side, so
 *   sending it would produce a guaranteed 400.
 * * At least two values must be ticked: the destination plus something to fold
 *   into it. One tick is a rename, not a merge.
 *
 * Note what is NOT refused: a DESTINATION may have sub-values. Merging "Ancient
 * Greece" into "Greece" when "Greece" has a child "Sparta" is well defined --
 * the source row is repointed at the survivor and the existing children are
 * untouched. An earlier version of this file wrongly refused that, which blocked
 * a valid operation the server happily accepts. */
export function mergeSourcePaths(chosen: string[], destination: string): string[] {
  return chosen.filter((p) => p !== destination);
}

/** Why the Merge button on a row is disabled, for its tooltip. */
export type MergeBlockReason =
  | 'not-ticked'
  | 'needs-another'
  | 'source-has-children'
  | null;

export interface MergeAvailability {
  ok: boolean;
  reason: MergeBlockReason;
  /** The source that caused a refusal, so the tooltip can name it. */
  offendingSource?: string;
}

/** Whether Merge may be pressed on `destination`, and if not, why not.
 *
 * `canBeSource` is a predicate rather than a single flag because the rule is
 * about the SOURCES, which can each differ; passing one boolean would forbid
 * legitimate multi-source merges. It answers the server's published question
 * ("may this value be a merge source?"), so the polarity is already the
 * right way round and this function cannot invert it.
 */
export function mergeAvailability(
  chosen: string[],
  destination: string,
  /** The server's per-source verdict: `available.can_merge_source`. */
  canBeSource: (path: string) => boolean,
): MergeAvailability {
  if (!chosen.includes(destination)) {
    return { ok: false, reason: 'not-ticked' };
  }
  const sources = mergeSourcePaths(chosen, destination);
  if (sources.length === 0) {
    return { ok: false, reason: 'needs-another' };
  }
  const offending = sources.find((p) => !canBeSource(p));
  if (offending !== undefined) {
    return { ok: false, reason: 'source-has-children', offendingSource: offending };
  }
  return { ok: true, reason: null };
}