// Support pack download feedback for the new UI (fork issue #2728).
//
// Building a pack takes a while, and a plain link gives no sign that a click is
// doing anything, so people click again. This keeps the normal download but
// reports when it has actually started.
//
// How it knows, without holding the file in memory (saving from a blob: URL is
// blocked by this app's CSP in Firefox, #2710): the page makes up a random token,
// requests the normal download URL in a hidden iframe with ?dl_token=<token>, and
// watches for the cwng_pack_token cookie that the server sets, with that token,
// only on a successful download. An iframe means a failure (429 busy, 500, a
// proxy's 504) loads into the hidden frame and cannot replace the app; its text
// says why. The classic Admin page uses the same protocol
// (cps/static/js/support_pack_download.js).

export const PACK_COOKIE = 'cwng_pack_token';
export const POLL_MS = 250;
/** After the frame loads, wait this long for the cookie to land before judging it. */
export const GRACE_MS = 400;
export const GIVE_UP_MS = 150_000;
/** Never pull the frame out from under a download that is still running. */
export const FRAME_REMOVE_MS = 60_000;
export const DONE_MESSAGE_MS = 8_000;

const TOKEN_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-';

export type PackErrorKind = 'busy' | 'failed' | 'timeout';

export interface PackFrame {
  /** What the hidden frame loaded (the server's JSON, or an error page). */
  text(): string;
  /** Detach the frame from the page. */
  remove(): void;
}

/** Every browser dependency, injected so the protocol can be tested without one. */
export interface PackEnv {
  token: string;
  cookies(): string;
  clearCookie(name: string): void;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(id: unknown): void;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(id: unknown): void;
  openFrame(src: string, onLoad: () => void): PackFrame;
  removeFrameLater(frame: PackFrame): void;
}

export interface PackHandlers {
  onDone(): void;
  onError(kind: PackErrorKind, message: string | null): void;
}

// The Path the server gives the cookie: the app's mount prefix, or "/" at the
// domain root (the server's spa.mount_cookie_path()). A cookie is only removed by
// a write with the same Path, so this must stay in step with it, including falling
// back to "/" for a prefix the server would not trust.
const SAFE_PREFIX = /^(\/[A-Za-z0-9._~-]+)+$/;

export function cookiePathFor(prefix: string): string {
  const trimmed = String(prefix || '').replace(/\/+$/, '');
  return trimmed && SAFE_PREFIX.test(trimmed) && !trimmed.includes('..') ? trimmed : '/';
}

export function clearCookieString(name: string, path: string): string {
  return `${name}=; Max-Age=0; path=${path}`;
}

export function cookieValue(cookieString: string, name: string): string | null {
  for (const part of String(cookieString || '').split(';')) {
    const piece = part.replace(/^\s+/, '');
    if (piece.startsWith(`${name}=`)) return piece.slice(name.length + 1);
  }
  return null;
}

/** A correlation id, not a secret: 22 URL-safe characters (the server accepts 8-64). */
export function makeToken(random: () => number): string {
  let token = '';
  for (let i = 0; i < 22; i += 1) {
    token += TOKEN_ALPHABET.charAt(Math.floor(random() * TOKEN_ALPHABET.length));
  }
  return token;
}

export function withToken(url: string, token: string): string {
  return `${url}${url.includes('?') ? '&' : '?'}dl_token=${token}`;
}

/** What the hidden frame loaded when no download started: the server's 429 JSON
 *  ("already being prepared"), or anything else (500, a proxy's 504 page, a login
 *  page, nothing at all). */
export function interpretFrame(text: string): { kind: 'busy' | 'failed'; message: string | null } {
  try {
    const data = JSON.parse(text) as { busy?: unknown; message?: unknown } | null;
    if (data && data.busy === true) {
      return { kind: 'busy', message: typeof data.message === 'string' ? data.message : null };
    }
  } catch {
    // not JSON: an HTML error page, or nothing at all
  }
  return { kind: 'failed', message: null };
}

/** Start a pack download and report when it starts or fails. Returns `cancel`,
 *  which stops watching without reporting (for when the component goes away). */
export function startPackDownload(
  env: PackEnv,
  url: string,
  handlers: PackHandlers,
): { cancel(): void } {
  let finished = false;
  let poll: unknown;
  let giveUp: unknown;
  let frame: PackFrame | null = null;

  const signalled = () => cookieValue(env.cookies(), PACK_COOKIE) === env.token;
  const stop = () => {
    finished = true;
    env.clearInterval(poll);
    env.clearTimeout(giveUp);
    env.clearCookie(PACK_COOKIE);
    if (frame) env.removeFrameLater(frame);
  };
  const done = () => {
    if (finished) return;
    stop();
    handlers.onDone();
  };
  const fail = (kind: PackErrorKind, message: string | null) => {
    if (finished) return;
    stop();
    handlers.onError(kind, message);
  };

  poll = env.setInterval(() => {
    if (!finished && signalled()) done();
  }, POLL_MS);
  giveUp = env.setTimeout(() => fail('timeout', null), GIVE_UP_MS);
  frame = env.openFrame(withToken(url, env.token), () => {
    // A successful attachment download normally never fires "load". If it does
    // (some browsers), the cookie, set with the same response, decides.
    env.setTimeout(() => {
      if (finished) return;
      if (signalled()) {
        done();
        return;
      }
      const verdict = interpretFrame(frame ? frame.text() : '');
      fail(verdict.kind, verdict.message);
    }, GRACE_MS);
  });

  return {
    cancel() {
      if (!finished) stop();
    },
  };
}

function browserRandom(win: Window): () => number {
  const cryptoApi = win.crypto;
  if (cryptoApi && typeof cryptoApi.getRandomValues === 'function') {
    return () => cryptoApi.getRandomValues(new Uint32Array(1))[0] / 4294967296;
  }
  return Math.random;
}

/** The real browser environment: cookie jar, timers and a hidden same-origin iframe.
 *  `mountPrefix` is the reverse-proxy prefix the app is served under ('' at the
 *  domain root); the cookie is scoped to it, so it is cleared there too. */
export function browserEnv(doc: Document, win: Window, mountPrefix = ''): PackEnv {
  const cookiePath = cookiePathFor(mountPrefix);
  return {
    token: makeToken(browserRandom(win)),
    cookies: () => doc.cookie,
    clearCookie: (name) => {
      doc.cookie = clearCookieString(name, cookiePath);
    },
    setInterval: (fn, ms) => win.setInterval(fn, ms),
    clearInterval: (id) => win.clearInterval(id as number),
    setTimeout: (fn, ms) => win.setTimeout(fn, ms),
    clearTimeout: (id) => win.clearTimeout(id as number),
    openFrame(src, onLoad) {
      const node = doc.createElement('iframe');
      node.setAttribute('aria-hidden', 'true');
      node.setAttribute('tabindex', '-1');
      node.style.display = 'none';
      node.addEventListener('load', onLoad);
      node.src = src;
      doc.body.appendChild(node);
      return {
        text: () => {
          try {
            return node.contentDocument?.body?.textContent ?? '';
          } catch {
            return '';
          }
        },
        remove: () => node.remove(),
      };
    },
    removeFrameLater: (frame) => {
      win.setTimeout(() => frame.remove(), FRAME_REMOVE_MS);
    },
  };
}
