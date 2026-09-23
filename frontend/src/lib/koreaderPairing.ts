/* Pairing codes as a person types them, and the e-readers page deep link.
 *
 * A KOReader device shows an 8-character code such as K7M4-QX2P and the
 * address <server>/pair. The server redirects that address here with
 * `?pair=1`, and with `&code=K7M4QX2P` when the phone scanned the device's QR
 * code. The alphabet and the forgiving reading of a typed code mirror
 * cps/services/koreader_pairing.py.
 */

import { parseApiTimestamp } from './relativeTime.ts';

export const USER_CODE_ALPHABET = 'BCDFGHJKMNPQRSTVWXZ23456789';
export const USER_CODE_LENGTH = 8;

/** The stored form of a typed code, or null when it cannot be one. Case,
 *  spaces and the dash are forgiven; any other character is not. */
export function normalizeUserCode(input: string): string | null {
  const code = input.toUpperCase().replace(/[\s-]/g, '');
  if (code.length !== USER_CODE_LENGTH) return null;
  for (const ch of code) {
    if (!USER_CODE_ALPHABET.includes(ch)) return null;
  }
  return code;
}

/** `K7M4QX2P` -> `K7M4-QX2P`, the form the device shows. */
export function displayUserCode(code: string): string {
  const half = USER_CODE_LENGTH / 2;
  return `${code.slice(0, half)}-${code.slice(half)}`;
}

/** Keep the code box readable while typing: upper case, at most eight
 *  letters and digits, a dash after the fourth. */
export function formatTypedCode(input: string): string {
  const raw = input.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, USER_CODE_LENGTH);
  const half = USER_CODE_LENGTH / 2;
  return raw.length > half ? `${raw.slice(0, half)}-${raw.slice(half)}` : raw;
}

/** What the page URL asks for: open the code box, and which code to fill in. */
export function pairingDeepLink(search: string): { open: boolean; code: string | null } {
  const params = new URLSearchParams(search);
  const code = normalizeUserCode(params.get('code') ?? '');
  return { open: params.get('pair') === '1' || code !== null, code };
}

/** True when an address points back at the computer it is typed on, which an
 *  e-reader cannot reach. Accepts what a person types: with or without
 *  http://, with a port. */
export function isOwnComputerAddress(address: string): boolean {
  const value = address.trim();
  if (!value) return false;
  let host: string;
  try {
    host = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : `http://${value}`).hostname.toLowerCase();
  } catch {
    return false;
  }
  return host === 'localhost' || host.endsWith('.localhost') || host.startsWith('127.')
    || host === '[::1]' || host === '0.0.0.0';
}

/** How long ago a device asked, in minutes ("3 minutes ago"), or null when it
 *  was under a minute ago or the time is unknown. Codes live ten minutes, so
 *  hours never apply. */
export function minutesAgo(iso: string | null, now: number, locale?: string): string | null {
  const at = iso ? parseApiTimestamp(iso) : null;
  if (at === null) return null;
  const minutes = Math.floor((now - at) / 60_000);
  if (minutes < 1) return null;
  return new Intl.RelativeTimeFormat(locale, { numeric: 'always' }).format(-minutes, 'minute');
}

/** Hand a generated file to the browser as a download. */
export function saveDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoking at once can cancel the download in some browsers.
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
