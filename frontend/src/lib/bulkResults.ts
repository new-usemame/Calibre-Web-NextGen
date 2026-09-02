export interface BulkFailureDetail {
  id: number;
  code?: string;
  message: string;
}

export interface BulkWarningDetail {
  id: number;
  code?: string;
  message: string;
}

export type BulkItemOutcome<T = unknown> =
  | { id: number; status: 'succeeded'; value: T }
  | { id: number; status: 'warning'; value: T; warning: BulkWarningDetail }
  | { id: number; status: 'failed'; error: unknown; failure: BulkFailureDetail };

/** Minimal accounting accepted from a structured batch endpoint. */
export interface BulkActionSummary {
  succeededIds: number[];
  failedIds: number[];
  failureDetails?: BulkFailureDetail[];
  warningIds?: number[];
  warnings?: BulkWarningDetail[];
}

/** Truthful item-by-item result returned to every bulk-action caller. */
export interface BulkActionResult<T = unknown> extends BulkActionSummary {
  succeededIds: number[];
  failedIds: number[];
  warningIds: number[];
  failureDetails: BulkFailureDetail[];
  warnings: BulkWarningDetail[];
  outcomes: BulkItemOutcome<T>[];
}

export interface BulkBatchResult extends BulkActionResult<undefined> {
  /** Request-level failures (for example batch_too_large). Per-id policy
   *  refusals stay in failedIds/failureDetails because the batch endpoint
   *  reports them in a successful response. */
  errors: unknown[];
}

export interface SettleByIdOptions<T> {
  /** Classify a fulfilled request as a warning instead of a clean success.
   *  Used when the server confirms the row change but reports incomplete
   *  cleanup; the value remains available on the per-item outcome. */
  warningFor?: (id: number, value: T) => BulkWarningDetail | undefined;
}

export interface BulkFailurePresentationItem {
  id: number;
  /** Omitted when every failed item shares the presentation's single reason. */
  reason?: string;
}

export interface BulkFailurePresentation {
  /** A reason common to every failed item, stated once before the item list. */
  sharedReason?: string;
  items: BulkFailurePresentationItem[];
}

export type BulkFailureReasonFor = (failure: BulkFailureDetail) => string;

const TERMINAL_PUNCTUATION = /([.!?…;:]+)([)\]}'"»”’]*)$/u;

/** Collapse a reason to a sentence fragment so a translated outer sentence can
 * add exactly one terminator. Closing quotes/brackets remain in place. */
function failureReasonFragment(value: string): string {
  let normalized = value.trim().replace(/\s+/gu, ' ');
  let previous = '';
  while (normalized !== previous) {
    previous = normalized;
    normalized = normalized.replace(TERMINAL_PUNCTUATION, '$2').trimEnd();
  }
  return normalized || 'Request failed';
}

/** Group failure details for one truthful announcement.
 *
 * Callers may provide the authoritative user-facing reason mapping for their
 * domain. When every item maps to the same reason it is returned once as the
 * shared reason, and per-book items contain only their ids. */
export function presentBulkFailures(
  details: readonly BulkFailureDetail[],
  reasonFor: BulkFailureReasonFor = (failure) => failure.message,
): BulkFailurePresentation {
  const resolved = details.map((failure) => ({
    id: failure.id,
    reason: failureReasonFragment(reasonFor(failure)),
  }));
  const sharedReason = resolved.length > 0
    && resolved.every((item) => item.reason === resolved[0].reason)
    ? resolved[0].reason
    : undefined;
  if (sharedReason) {
    return {
      sharedReason,
      items: resolved.map(({ id }) => ({ id })),
    };
  }
  return { items: resolved };
}

/** Join already-localized sentences without inventing doubled terminators. */
export function joinBulkSentences(...values: string[]): string {
  return values.flatMap((value) => {
    const trimmed = value.trim();
    if (!trimmed) return [];
    const terminal = trimmed.match(TERMINAL_PUNCTUATION);
    if (!terminal || terminal.index === undefined) return [`${trimmed}.`];
    const punctuation = terminal[1];
    const normalized = punctuation.includes('?')
      ? '?'
      : punctuation.includes('!')
        ? '!'
        : punctuation.includes('…')
          ? '…'
          : '.';
    return [`${trimmed.slice(0, terminal.index)}${normalized}${terminal[2]}`];
  }).join(' ');
}

function failureFromError(id: number, error: unknown): BulkFailureDetail {
  const detail = typeof error === 'object' && error !== null
    ? (error as { detail?: { code?: unknown } }).detail
    : undefined;
  const code = typeof detail?.code === 'string' ? detail.code : undefined;
  const message = error instanceof Error && error.message
    ? error.message
    : 'Request failed';
  return { id, ...(code ? { code } : {}), message };
}

/** Settle every per-book request while retaining which id produced each result. */
export async function settleById<T>(
  ids: number[],
  run: (id: number) => Promise<T>,
  options: SettleByIdOptions<T> = {},
): Promise<BulkActionResult<T>> {
  const results = await Promise.allSettled(ids.map(run));
  return results.reduce<BulkActionResult<T>>((accounting, result, index) => {
    const id = ids[index];
    if (result.status === 'rejected') {
      const failure = failureFromError(id, result.reason);
      accounting.failedIds.push(id);
      accounting.failureDetails.push(failure);
      accounting.outcomes.push({ id, status: 'failed', error: result.reason, failure });
      return accounting;
    }

    const classified = options.warningFor?.(id, result.value);
    if (classified) {
      const warning = { ...classified, id };
      accounting.warningIds.push(id);
      accounting.warnings.push(warning);
      accounting.outcomes.push({ id, status: 'warning', value: result.value, warning });
      return accounting;
    }

    accounting.succeededIds.push(id);
    accounting.outcomes.push({ id, status: 'succeeded', value: result.value });
    return accounting;
  }, {
    succeededIds: [], failedIds: [], warningIds: [],
    failureDetails: [], warnings: [], outcomes: [],
  });
}

/** Run a bounded batch endpoint without losing per-id accounting.
 *
 * Every requested id is classified exactly once. A rejected request marks its
 * whole chunk failed while successful earlier/later chunks remain recorded;
 * an id omitted by a malformed/truncated response is failed, never silently
 * treated as successful. */
export async function settleByBatch(
  ids: number[],
  batchSize: number,
  run: (ids: number[]) => Promise<BulkActionSummary>,
): Promise<BulkBatchResult> {
  if (!Number.isInteger(batchSize) || batchSize < 1) {
    throw new RangeError('batchSize must be a positive integer');
  }

  const chunks: number[][] = [];
  for (let start = 0; start < ids.length; start += batchSize) {
    chunks.push(ids.slice(start, start + batchSize));
  }
  const settled = await Promise.allSettled(chunks.map(run));

  return settled.reduce<BulkBatchResult>((accounting, result, index) => {
    const chunk = chunks[index];
    if (result.status === 'rejected') {
      chunk.forEach((id) => {
        const failure = failureFromError(id, result.reason);
        accounting.failedIds.push(id);
        accounting.failureDetails.push(failure);
        accounting.outcomes.push({ id, status: 'failed', error: result.reason, failure });
      });
      accounting.errors.push(result.reason);
      return accounting;
    }

    const succeeded = new Set(result.value.succeededIds);
    const warningById = new Map(
      (result.value.warnings ?? []).map((detail) => [detail.id, detail]),
    );
    const failureById = new Map(
      (result.value.failureDetails ?? []).map((detail) => [detail.id, detail]),
    );
    chunk.forEach((id) => {
      const warning = warningById.get(id);
      if (warning) {
        accounting.warningIds.push(id);
        accounting.warnings.push(warning);
        accounting.outcomes.push({ id, status: 'warning', value: undefined, warning });
        return;
      }
      if (succeeded.has(id)) {
        accounting.succeededIds.push(id);
        accounting.outcomes.push({ id, status: 'succeeded', value: undefined });
        return;
      }
      accounting.failedIds.push(id);
      const failure = failureById.get(id) ?? {
        id,
        message: 'No result was returned for this book.',
      };
      accounting.failureDetails.push(failure);
      accounting.outcomes.push({ id, status: 'failed', error: failure, failure });
    });
    return accounting;
  }, {
    succeededIds: [], failedIds: [], warningIds: [],
    errors: [], failureDetails: [], warnings: [], outcomes: [],
  });
}
