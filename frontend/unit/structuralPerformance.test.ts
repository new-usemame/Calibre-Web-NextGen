import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { DRAWER_MODE_QUERY } from '../src/lib/a11y/drawerMode.ts';

const source = (relative: string) => fs.readFileSync(path.join(process.cwd(), relative), 'utf8');

test('desktop sidebar reveal animates only the out-of-flow panel, never the flow rail', () => {
  const css = source('src/components/Sidebar.module.css');
  const desktopStart = css.indexOf('@media (min-width: 768px) and (hover: hover) and (pointer: fine)');
  const reducedMotionStart = css.indexOf('@media (min-width: 768px)', desktopStart + 1);
  const desktop = css.slice(desktopStart, reducedMotionStart);

  assert.match(desktop, /\.rail\s*\{[\s\S]*?width:\s*64px;[\s\S]*?flex:\s*0 0 64px;/);
  // The collapsed panel is a real 64px box (hit-testable where users aim), and
  // it is absolutely positioned so its width animation cannot relayout <main>.
  // clip-path collapse was tried and reverted: clipped regions drop out of
  // hit-testing, which made every link centre unclickable while collapsed.
  assert.match(desktop, /:is\(\.nav, \.navOpen\)\s*\{[\s\S]*?position:\s*absolute;[\s\S]*?width:\s*64px;/);
  assert.match(desktop, /transition:\s*width/);
  assert.doesNotMatch(desktop, /clip-path\s*:/);
  assert.match(desktop, /\.magicShelfIcon\s*\{[\s\S]*?transition:\s*transform/);
  assert.doesNotMatch(desktop, /transition\s*:[^;]*(?:margin(?:-right)?|left)/);
});

test('drawer-mode CSS stays synchronized with the JavaScript accessibility query', () => {
  const expectedRule = `@media ${DRAWER_MODE_QUERY}`;

  for (const stylesheet of [
    'src/components/Sidebar.module.css',
    'src/components/TopBar.module.css',
  ]) {
    const css = source(stylesheet);
    assert.equal(
      css.split(expectedRule).length - 1,
      1,
      `${stylesheet} must contain exactly one drawer-mode rule matching DRAWER_MODE_QUERY`,
    );
  }
});

test('catalog owns one stable measured grid node and delegates its cards to the row window', () => {
  const catalog = source('src/pages/Catalog.tsx');
  const gridTestIds = catalog.match(/data-testid="catalog-grid"/g) ?? [];

  assert.equal(gridTestIds.length, 1);
  assert.match(catalog, /new ResizeObserver\(\(\) => \{[\s\S]*?measure\(\);[\s\S]*?scheduleMeasure\(\);/);
  assert.match(catalog, /measureCatalogColumnCount\(\{[\s\S]*?gridTemplateColumns:[\s\S]*?gridWidth:/);
  assert.match(catalog, /columnGap: Number\.parseFloat\(style\.columnGap\)/);
  assert.match(catalog, /requestAnimationFrame\([\s\S]*?document\.fonts\?\.ready/);
  assert.match(catalog, /setTimeout\(\(\) => \{[\s\S]*?measureGridRef\.current\(\);[\s\S]*?setGridMeasured\(true\)/);
  assert.match(catalog, /<VirtualizedGridRows[\s\S]*?columnCount=\{columnCount\}/);
});
