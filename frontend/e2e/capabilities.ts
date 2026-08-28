import { test, type APIRequestContext } from '@playwright/test';

export interface RouteCapability {
  method: string;
  path: string;
  /** Human-readable feature/route name used in the skip report. */
  name: string;
  /** Every-PR test that pins route registration independently of E2E. */
  pinnedBy: string;
}

/**
 * Ask Flask whether a route+method is registered.
 *
 * This is deliberately a PRESENCE probe only. It never sends a feature
 * payload and never interprets a response body, so a present-but-broken route
 * still runs the real spec and fails on correctness. Flask's automatic OPTIONS
 * response is the contract: its Allow header lists methods for the matched
 * rule without invoking the handler.
 */
export async function hasRouteCapability(
  request: APIRequestContext,
  capability: Pick<RouteCapability, 'method' | 'path'>,
): Promise<boolean> {
  const method = capability.method.toUpperCase();
  if (method === 'GET' || method === 'HEAD' || method === 'OPTIONS') {
    throw new Error(
      `Route capability probes cannot safely use ${method}: the classic UI catch-all advertises GET, HEAD, and OPTIONS for unknown paths. `
      + 'Probe a feature-specific mutation route so absence cannot be mistaken for presence.',
    );
  }
  const response = await request.fetch(capability.path, {
    method: 'OPTIONS',
    failOnStatusCode: false,
  });
  if (response.status() === 404) return false;
  const allowed = new Set(
    (response.headers()['allow'] ?? '')
      .split(',')
      .map((method) => method.trim().toUpperCase())
      .filter(Boolean),
  );
  return allowed.has(method);
}

/** Skip this spec, with a self-retiring reason, when the backend predates it. */
export async function requireRouteCapability(
  request: APIRequestContext,
  capability: RouteCapability,
): Promise<void> {
  const present = await hasRouteCapability(request, capability);
  test.skip(
    !present,
    `${capability.name} is absent from this lane's backend (${capability.method.toUpperCase()} ${capability.path}). `
      + `This probes presence only; route registration is pinned on every PR by ${capability.pinnedBy}. `
      + 'The skip retires itself as soon as the image contains the route.',
  );
}
