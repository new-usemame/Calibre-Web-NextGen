import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

interface PackageManifest {
  scripts?: Record<string, string>;
}

test('the unit-test lane type-checks the e2e and unit corpus first', () => {
  const manifest = JSON.parse(fs.readFileSync('package.json', 'utf8')) as PackageManifest;
  const unitScript = manifest.scripts?.['test:unit'] ?? '';

  assert.match(unitScript, /^tsc -p tsconfig\.e2e\.json --noEmit && /);
});
