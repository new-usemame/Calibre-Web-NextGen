import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const SPEC = path.resolve('e2e/visual-regression.spec.ts');

test('visual regression suite stays deliberately capped at six pixel assertions', () => {
  const source = fs.readFileSync(SPEC, 'utf8');
  const assertions = source.match(/\.toHaveScreenshot\(/g) ?? [];
  assert.equal(assertions.length, 6, 'keep exactly the six documented high-value views');
});

test('visual regression suite keeps a French rendering contract', () => {
  const source = fs.readFileSync(SPEC, 'utf8');
  assert.match(source, /installStableFixtures\(page, 'fr'\)/);
  assert.match(source, /toHaveAttribute\('lang', 'fr'\)/);
});
