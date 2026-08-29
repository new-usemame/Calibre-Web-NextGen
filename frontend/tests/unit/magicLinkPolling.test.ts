/*
 * Regression coverage for MagicLink's fatal-vs-transient poll failures.
 *
 * Run:
 *   NODE_OPTIONS=--experimental-strip-types node --test frontend/tests/unit/magicLinkPolling.test.ts
 */
import { describe, test } from 'node:test';
import assert from 'node:assert/strict';

import { ApiError } from '../../src/lib/api.ts';
import { classifyMagicLinkPollError } from '../../src/lib/magicLinkPolling.ts';

describe('classifyMagicLinkPollError', () => {
  test('ends polling with a rate-limited state on HTTP 429', () => {
    assert.equal(classifyMagicLinkPollError(new ApiError(429, 'rate limited')), 'rate_limited');
  });

  test('ends polling on other non-retryable HTTP responses', () => {
    assert.equal(classifyMagicLinkPollError(new ApiError(403, 'disabled')), 'fatal');
  });

  test('retries server failures and network errors', () => {
    assert.equal(classifyMagicLinkPollError(new ApiError(503, 'unavailable')), 'retry');
    assert.equal(classifyMagicLinkPollError(new TypeError('network failed')), 'retry');
  });
});
