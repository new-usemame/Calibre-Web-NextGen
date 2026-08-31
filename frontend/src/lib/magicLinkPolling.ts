export type MagicLinkPollErrorAction = 'retry' | 'rate_limited' | 'fatal';

/**
 * Network failures and server errors are transient. Client-error responses are
 * definitive for this polling session: retrying the same request cannot recover
 * and, for a 429, only keeps hammering an already-exhausted shared-IP bucket.
 */
export function classifyMagicLinkPollError(error: unknown): MagicLinkPollErrorAction {
  const status = error && typeof error === 'object' && 'status' in error
    ? (error as { status?: unknown }).status
    : undefined;
  if (typeof status !== 'number' || status >= 500 || status === 408 || status === 425) return 'retry';
  return status === 429 ? 'rate_limited' : 'fatal';
}
