/** The browse tree and the value editor must present the same control at the
 *  same size.
 *
 *  A request to shrink the browse view's disclosure button could not be judged
 *  on its own: 44px -> 28px changes nothing on screen unless the ROW shrinks
 *  too, because `.treeSummary` carried 8px of vertical padding and its own
 *  44px floor. Both had to move together, which means the relationship between
 *  them is easy to break silently -- someone tidying one file and not the
 *  other.
 *
 *  These are source assertions because the runner cannot import a .css module
 *  (only .ts). What they guard is the arithmetic: button + padding = row, and
 *  both views agreeing on the button.
 */
import { ok, strictEqual } from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';

const read = (relative: string) =>
  readFileSync(path.join(process.cwd(), relative), 'utf8');

/* cwd is frontend/ -- hence no ../ on these, unlike the tests that reach into
 * the repository root (e.g. ccNodeAvailability.test.ts reading ../../cps). */
const browse = read('src/pages/CcBrowse.module.css');
const editor = read('src/pages/CcValueEditor.module.css');
const tokens = read('src/styles/tokens.css');

/** The declared width/height of a `width: Npx; height: Npx` rule. */
function boxSize(css: string, selector: string): number {
  const at = css.indexOf(`\n${selector} {`);
  ok(at !== -1, `${selector} must exist`);
  const body = css.slice(at, css.indexOf('}', at));
  const width = /width:\s*(\d+)px/.exec(body);
  const height = /height:\s*(\d+)px/.exec(body);
  ok(width && height, `${selector} must declare both width and height`);
  strictEqual(width[1], height[1], `${selector} must be square`);
  return Number(width[1]);
}

const spacing = (token: string): number => {
  const match = new RegExp(`--${token}:\\s*([\\d.]+)rem`).exec(tokens);
  ok(match, `--${token} must be defined`);
  return Number(match[1]) * 16;
};

ok(spacing('sp-3') === 12,
  '`.children` indents with margin-left: calc(var(--sp-3) + 8px), so --sp-3 is 12px '
  + 'and the horizontal padding below depends on it');

test('the browse disclosure and the editor row button are the same size', () => {
  strictEqual(
    boxSize(browse, '.disclosure'),
    boxSize(editor, '.rowBtn'),
    'the two interfaces must present the same control at the same size',
  );
});

/** Resolve a length token to px. Handles `0`, `Npx` and `var(--sp-N)`. */
function px(token: string): number {
  if (token === '0') return 0;
  const rem = /^var\(--([a-z0-9-]+)\)$/.exec(token);
  if (rem) return spacing(rem[1]);
  const raw = /^([\d.]+)(px|rem)$/.exec(token);
  ok(raw, `cannot resolve a length from ${JSON.stringify(token)}`);
  return Number(raw[1]) * (raw[2] === 'rem' ? 16 : 1);
}

/** The declared padding of a rule, as [vertical, horizontal]. */
function padding(css: string, selector: string): [number, number] {
  const at = css.indexOf(`\n${selector} {`);
  ok(at !== -1, `${selector} must exist`);
  const body = css.slice(at, css.indexOf('}', at));
  const match = /padding:\s*([^;]+);/.exec(body);
  ok(match, `${selector} must declare padding`);
  const parts = match[1].trim().split(/\s+/).map(px);
  if (parts.length === 1) return [parts[0], parts[0]];
  return [parts[0], parts[1]];
}

/** The declared between-row gap of a rule, in px. */
function rowGap(css: string, selector: string, property: 'gap' | 'margin'): number {
  const at = css.indexOf(`\n${selector} {`);
  ok(at !== -1, `${selector} must exist`);
  const body = css.slice(at, css.indexOf('}', at));
  const match = new RegExp(`${property}:\\s*([^;]+);`).exec(body);
  ok(match, `${selector} must declare ${property}`);
  return px(match[1].trim().split(/\s+/)[0]);
}

test('a browse row is exactly as tall as an edit-mode row', () => {
  /* The editor is the reference: `.row` has no padding and no min-height, so an
   * editor row is exactly its 28px button. Matching that is what "the same as
   * edit mode" means, and the gap between the two views is the whole reason
   * this looked different. */
  const editorRow = boxSize(editor, '.rowBtn');       // .row has neither
  const browseRow = boxSize(browse, '.disclosure') + padding(browse, '.treeSummary')[0];

  strictEqual(browseRow, editorRow,
    'the browse row must be the same height as the edit-mode row');
});

test('the browse row floor does not exceed what the button and padding need', () => {
  /* A min-height above the natural height silently wins and the button change
   * is invisible -- exactly what happened twice. */
  const at = browse.indexOf('\n.treeSummary {');
  const body = browse.slice(at, browse.indexOf('}', at));
  const floor = /min-height:\s*(\d+)px/.exec(body);
  ok(floor, '.treeSummary must keep an explicit min-height');

  const natural = boxSize(browse, '.disclosure') + padding(browse, '.treeSummary')[0];
  ok(Number(floor[1]) <= natural,
    `min-height ${floor[1]}px exceeds the ${natural}px the row naturally needs, `
    + 'so it -- not the button -- is setting the height');
});

test('the horizontal padding is kept, because the nesting guides depend on it', () => {
  /* `.children` indents with margin-left: calc(var(--sp-3) + 8px). Reducing
   * `.treeSummary`'s horizontal padding would slide every row's content out of
   * alignment with the guides. Vertical only. */
  const [, horizontal] = padding(browse, '.treeSummary');
  strictEqual(horizontal, spacing('sp-3'),
    'horizontal padding must stay at --sp-3, which is what .children indents against');
});

test('the vertical gap between rows matches the editor', () => {
  strictEqual(
    rowGap(browse, '.treeNode', 'margin'),
    rowGap(editor, '.tree', 'gap'),
    'rows must be spaced the same in both interfaces',
  );
});

test('the disclosure keeps its padding reset', () => {
  /* At 28px, any inherited or default padding would push the icon off-centre
   * even though the box is square. */
  const at = browse.indexOf('\n.disclosure {');
  const body = browse.slice(at, browse.indexOf('}', at));
  ok(/padding:\s*0/.test(body), '.disclosure must reset padding to 0');
});
