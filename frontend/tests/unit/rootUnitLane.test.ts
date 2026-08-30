import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const FRONTEND_ROOT = fileURLToPath(new URL('../..', import.meta.url));

test('the CI-covered frontend suite executes the root unit lane', () => {
  const environment = { ...process.env };
  delete environment.NODE_OPTIONS;

  const result = spawnSync(
    process.platform === 'win32' ? 'npm.cmd' : 'npm',
    ['run', 'test:unit'],
    {
      cwd: FRONTEND_ROOT,
      encoding: 'utf8',
      env: environment,
      timeout: 30_000,
    },
  );

  assert.ifError(result.error);
  assert.equal(
    result.status,
    0,
    `npm run test:unit failed:\n${result.stdout}\n${result.stderr}`,
  );
});
