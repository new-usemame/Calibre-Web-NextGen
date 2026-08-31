import { describe, test } from 'node:test';
import assert from 'node:assert/strict';

import { apiUrl } from '../../src/lib/api.ts';

describe('frontend source module resolution', () => {
  test('loads api.ts through its extensionless source imports', () => {
    assert.equal(apiUrl('/api/v1/books'), '/api/v1/books');
  });
});
