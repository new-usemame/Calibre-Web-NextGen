/* Where a value held in the editor's paste buffer may be cut from and pasted to.
 *
 * Kept in its own module (not inside CcValueEditor.tsx) so it can be unit
 * tested directly: the runner type-checks but cannot import a .tsx file, and
 * these rules are the ones worth executing rather than pattern-matching.
 *
 * TWO INDEPENDENT GATES, both required:
 *
 *   1. SELECTION. Cut and Paste are only offered on a ticked row. Cut acts on a
 *      value, and Paste names a destination -- neither is safe to trigger on a
 *      row the user merely scrolled past. Ticking is the deliberate act, so a
 *      mis-click on the button itself cannot move anything.
 *
 *   2. NO CYCLES. A value must not become its own ancestor, so the node itself
 *      and everything beneath it are refused as a paste target.
 *
 * Note what is NOT refused: pasting into an ANCESTOR. Moving
 * History.AncientHistory.India under History yields History.India, a sibling of
 * AncientHistory rather than a descendant of India -- no cycle. Only the node
 * itself and its own descendants are.
 *
 * Mirrors the server's rule in cps/api/cc_values.py::move_cc_value
 * (`new_path == path or new_path.startswith(path + '.')`). The client copy
 * exists to disable the button and explain why; the server still refuses,
 * because another session may have changed the tree since it was cached.
 */

/** The separator between path segments — must match cps/hierarchy.py SEPARATOR. */
const SEPARATOR = '.';

/**
 * Can the value held in `cutPath` be pasted onto `targetPath`?
 *
 * Cycle check only. `''` means the top level, which is always a valid
 * destination — an empty string is neither the cut node nor a descendant.
 */
export function canPasteInto(cutPath: string | null, targetPath: string): boolean {
  if (!cutPath) return false;
  return targetPath !== cutPath && !targetPath.startsWith(cutPath + SEPARATOR);
}

/** Whether Cut may be pressed on a row. Requires the row to be ticked. */
export function canCut(targetChecked: boolean): boolean {
  return targetChecked;
}

/** Why the Paste button on a row is disabled, for its tooltip. */
export type PasteBlockReason =
  'not-ticked' | 'nothing-to-paste' | 'into-itself' | 'into-descendant' | null;

/** Whether Paste may be pressed on a row, and if not, why not.
 *
 * `targetChecked` is the selection gate; the cycle check is independent of it,
 * so a blocked button can always say which of the two is the problem.
 *
 * With nothing held there is nothing to paste, so the button stays disabled
 * rather than becoming a live control that silently does nothing. */
export function pasteAvailability(
  cutPath: string | null,
  targetPath: string,
  targetChecked: boolean,
): { ok: boolean; reason: PasteBlockReason } {
  if (!targetChecked) return { ok: false, reason: 'not-ticked' };
  if (!cutPath) return { ok: false, reason: 'nothing-to-paste' };
  if (targetPath === cutPath) return { ok: false, reason: 'into-itself' };
  if (targetPath.startsWith(cutPath + SEPARATOR)) {
    return { ok: false, reason: 'into-descendant' };
  }
  return { ok: true, reason: null };
}

/** Whether `targetPath` is a row this held value COULD be pasted onto.
 *
 * The cycle rule alone, ignoring the tick gate. The editor uses it to mark
 * every valid destination while nothing is ticked yet — otherwise the only
 * rows that light up are the ones already ticked, which is circular: the user
 * cannot tell a row is a target until they tick it, and has no reason to tick
 * it until they can see it is one. */
export function isValidPasteTarget(cutPath: string | null, targetPath: string): boolean {
  return canPasteInto(cutPath, targetPath);
}