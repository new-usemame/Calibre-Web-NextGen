/*
 * Regression coverage for MagicLink's fatal-vs-transient poll failures.
 *
 * Run:
 *   NODE_OPTIONS=--experimental-strip-types node --test frontend/tests/unit/magicLinkPolling.test.ts
 */
import { describe, test } from 'node:test';
import assert from 'node:assert/strict';

import { classifyMagicLinkPollError } from '../../src/lib/magicLinkPolling.ts';

const httpError = (status: number): { status: number } => ({ status });

describe('classifyMagicLinkPollError', () => {
  test('ends polling with a rate-limited state on HTTP 429', () => {
    assert.equal(classifyMagicLinkPollError(httpError(429)), 'rate_limited');
  });

  test('ends polling on other non-retryable HTTP responses', () => {
    assert.equal(classifyMagicLinkPollError(httpError(403)), 'fatal');
  });

  test('retries server failures, transient request responses, and network errors', () => {
    assert.equal(classifyMagicLinkPollError(httpError(503)), 'retry');
    assert.equal(classifyMagicLinkPollError(httpError(408)), 'retry');
    assert.equal(classifyMagicLinkPollError(httpError(425)), 'retry');
    assert.equal(classifyMagicLinkPollError(new TypeError('network failed')), 'retry');
  });
});
