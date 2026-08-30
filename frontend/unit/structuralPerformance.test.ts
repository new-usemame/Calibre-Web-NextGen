import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const source = (relative: string) => fs.readFileSync(path.join(process.cwd(), relative), 'utf8');

test('desktop sidebar reveal is clip/transform driven with a fixed flow rail', () => {
  const css = source('src/components/Sidebar.module.css');
  const desktopStart = css.indexOf('@media (min-width: 768px) and (hover: hover) and (pointer: fine)');
  const reducedMotionStart = css.indexOf('@media (min-width: 768px)', desktopStart + 1);
  const desktop = css.slice(desktopStart, reducedMotionStart);

  assert.match(desktop, /\.rail\s*\{[\s\S]*?width:\s*64px;[\s\S]*?flex:\s*0 0 64px;/);
  assert.match(desktop, /:is\(\.nav, \.navOpen\)\s*\{[\s\S]*?position:\s*absolute;[\s\S]*?width:\s*220px;/);
  assert.match(desktop, /transition:\s*clip-path/);
  assert.match(desktop, /\.magicShelfIcon\s*\{[\s\S]*?transition:\s*transform/);
  assert.doesNotMatch(desktop, /transition\s*:[^;]*(?:width|margin(?:-right)?|left)/);
});

test('catalog owns one stable measured grid node and delegates its cards to the row window', () => {
  const catalog = source('src/pages/Catalog.tsx');
  const gridTestIds = catalog.match(/data-testid="catalog-grid"/g) ?? [];

  assert.equal(gridTestIds.length, 1);
  assert.match(catalog, /new ResizeObserver\(\(\) => \{[\s\S]*?getComputedStyle\(gridNode\)\.gridTemplateColumns/);
  assert.match(catalog, /<VirtualizedGridRows[\s\S]*?columnCount=\{columnCount\}/);
});
