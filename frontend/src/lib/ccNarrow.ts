/** Narrowing rules for the custom-column value editor.
 *
 *  A column of several thousand values is unusable unfiltered. These are VIEW
 *  filters: every row is already loaded, so narrowing costs no round trip --
 *  the same reasoning that made the rows server-rendered in the first place.
 *
 *  Lives in its own module, not in CcValueEditor.tsx, for the same reason
 *  ccMerge.ts and ccPaste.ts do: the frontend unit runner is `node --test` and
 *  **cannot import a .tsx file**, so a rule left inside the component would be
 *  untestable. The runner throws ERR_UNKNOWN_FILE_EXTENSION.
 *
 *  Mirrored by the classic script (cps/static/js/cc_value_editor.js). That
 *  duplication is deliberate -- these are view filters with no consequence, and
 *  the alternative was dragging React into the classic page -- and it is safe
 *  only because both sides are pinned: ccEditorNarrowing.test.ts here, and
 *  test_cc_editor_narrowing.py on the classic side.
 */

/** Whether `path` is the focused value or sits beneath it.
 *
 *  A path-SEGMENT prefix, not a bare string one: `Historyics` merely starts
 *  with `History` and is a sibling, not a child. Same rule as the cycle guard. */
export function isUnderFocus(path: string, focus: string): boolean {
  if (!focus) return true;
  return path === focus || path.startsWith(`${focus}.`);
}

/** The rows left after both narrowing controls are applied.
 *
 *  Generic over anything carrying a path, not a full node type: only `path` and
 *  `depth` are read, and a wider type would force every caller to build a node
 *  for a filter that never looks at one. */
export function narrowRows<T extends { node: { path: string }; depth: number }>(
  rows: T[],
  focusPath: string,
  depthLimit: number,
): T[] {
  // A focus that matches no row is stale -- the value was renamed or deleted
  // under it -- and is IGNORED rather than obeyed. Obeying it would filter
  // every row out and leave an empty panel whose only exit is a control the
  // user has to guess at. Handling it here rather than at each caller means
  // neither interface can get it wrong.
  const focusDepth = focusPath
    ? rows.find((r) => r.node.path === focusPath)?.depth
    : 0;
  const effectiveFocus = focusDepth === undefined ? '' : focusPath;
  const root = focusDepth ?? 0;

  return rows.filter((row) => {
    if (!isUnderFocus(row.node.path, effectiveFocus)) return false;
    if (depthLimit === 0) return true;
    // Measured from the VISIBLE root, not the true one. The absolute reading is
    // useless once you have focused: focusing two levels down and asking for two
    // levels would show one row and read as a broken filter.
    const relative = row.depth - root;
    return (relative < 0 ? 0 : relative) < depthLimit;
  });
}

/** The level options worth offering: one per level actually present.
 *
 *  Never returns an empty list -- an empty select cannot be un-stuck, and a
 *  column with nothing below its top level still needs the choice to exist. */
export function depthOptions(rows: { depth: number }[]): number[] {
  const max = rows.reduce((deepest, row) => Math.max(deepest, row.depth), 0);
  return Array.from({ length: max + 1 }, (_, i) => i + 1);
}
