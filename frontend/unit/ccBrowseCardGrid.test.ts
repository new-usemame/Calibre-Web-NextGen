import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const source = (relative: string) => fs.readFileSync(path.join(process.cwd(), relative), 'utf8');

/** Every class that CcBrowse renders as a grid of BookCards, and therefore
 *  relies on `display: grid` to bound each card's width. */
const CARD_GRIDS = ['.grid', '.insetGrid', '.columnGrid'];

/** Every declaration that applies to `selector`, across all the rules whose
 *  selector LIST names it. A class can be declared more than once — a grouped
 *  base rule plus a specific override — so reading only the first match would
 *  miss declarations that live in the other one. */
function body(css: string, selector: string): string {
  let found = '';
  let matched = false;
  for (const [, selectors, declarations] of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    const names = new Set(
      selectors.replace(/\/\*[\s\S]*?\*\//g, ' ').split(',').map((s) => s.trim()),
    );
    if (!names.has(selector)) continue;
    found += declarations;
    matched = true;
  }
  if (!matched) throw new Error(`no rule found for ${selector}`);
  return found;
}

test('every custom-column card grid actually lays out as a grid', () => {
  const css = source('src/pages/CcBrowse.module.css');

  // Regression: the inset list was authored by duplicating the plain list's
  // block and dropping `display: grid` from the copy. With no display value
  // the <ul> stayed a block box, each <li> took the full page width, and
  // BookCover's `width: 100%; aspect-ratio: 2/3` rendered every cover at page
  // width — reported on both a hierarchical and a flat column, because the
  // inset is used by both. The image itself was fine; its container was not.
  for (const selector of CARD_GRIDS) {
    assert.match(
      body(css, selector),
      /display:\s*grid/,
      `${selector} must set display: grid — without it each BookCard fills the page width`,
    );
    assert.match(body(css, selector), /grid-template-columns:\s*repeat\(/, `${selector} must size its columns`);
  }
});

test('the card grids reset list chrome so a card is not indented as a list item', () => {
  const css = source('src/pages/CcBrowse.module.css');

  for (const selector of CARD_GRIDS) {
    const declarations = body(css, selector);
    for (const property of ['list-style', 'padding', 'margin']) {
      assert.match(declarations, new RegExp(`${property}:`), `${selector} must reset ${property}`);
    }
  }
});

test('BookCover is bounded by its parent, so a card grid can never be sized by the image', () => {
  // The reason the missing display value was visible at all: BookCover is
  // width:100% + aspect-ratio, so it takes whatever width it is offered. If a
  // future change lets it size itself, the grid's track stops mattering.
  const cover = source('src/components/BookCover.module.css');
  assert.match(body(cover, '.wrap'), /width:\s*100%/);
  assert.match(body(cover, '.wrap'), /aspect-ratio:\s*2\s*\/\s*3/);
  assert.match(body(cover, '.img'), /width:\s*100%/);
  assert.match(body(cover, '.img'), /height:\s*100%/);
});

test('the custom-column page sizes both book placements from the same base rule', () => {
  // The two placements are the same list at two widths, so their shared half
  // is declared once in a grouped selector. That grouping is what stops the
  // next edit from re-introducing a per-list copy that drifts.
  const css = source('src/pages/CcBrowse.module.css');
  assert.match(
    css,
    /\.grid\s*,\s*\.insetGrid\s*\{[^}]*display:\s*grid;/,
    'the shared grid declarations belong in one grouped rule',
  );
});
