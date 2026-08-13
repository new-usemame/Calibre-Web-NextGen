/*
 * Tests for the zero-egress report builder.
 *
 * Uses Node's BUILT-IN test runner (`node --test`) and native TypeScript type
 * stripping. That is a deliberate choice: the project ships no unit-test
 * framework, and adding one (vitest/jest) would be a new dependency, which is
 * operator-gated under hard rule 6. Node's runner is already on the machine
 * that builds this project, so the coverage lands today rather than waiting on
 * a dependency decision.
 *
 * Run: node --test frontend/tests/unit/reportBuilder.test.ts
 *
 * Lives OUTSIDE src/ deliberately: it is a Node program, and the app tsconfig
 * (include: ["src"], lib: DOM) would typecheck it against browser-only types
 * and fail on every node: import.
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
  routePattern,
  coarseBrowser,
  viewportBucket,
  scrubFreeText,
  buildBody,
  buildTitle,
  githubIssueUrl,
  reportTarget,
  collectContext,
  type ReportContext,
} from '../../src/lib/reportBuilder.ts';

const CTX: ReportContext = {
  version: 'v4.1.31',
  routePattern: '/app/book/:id',
  browser: 'Safari on macOS',
  theme: 'dark',
  viewport: 'narrow (<600px)',
};

/* ── The load-bearing guarantee ───────────────────────────────────────────── */

describe('zero-egress invariant', () => {
  // This is the whole feature. A future edit that adds a fetch turns a
  // privacy-preserving composer back into the telemetry the operator killed,
  // and it would look entirely reasonable in review ("just report the error
  // automatically"). So the property is asserted mechanically, at the source.
  test('the module contains no transmit call whatsoever', () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const source = readFileSync(join(here, '..', '..', 'src', 'lib', 'reportBuilder.ts'), 'utf8');
    // Strip comments first: the file DISCUSSES fetch/beacon at length in its
    // header, and matching prose would make this test permanently red — the
    // classic "gate everyone deletes because it cries wolf".
    const code = source
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '');

    for (const forbidden of [
      'fetch(', 'XMLHttpRequest', 'sendBeacon', 'navigator.send',
      'WebSocket', 'EventSource', 'import(', 'new Image', 'axios',
    ]) {
      assert.ok(
        !code.includes(forbidden),
        `reportBuilder must never transmit — found "${forbidden}". ` +
        `This module composes text for the USER to post; it never sends anything itself.`,
      );
    }
  });

  test('no report field is derived from the full URL', () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const source = readFileSync(join(here, '..', '..', 'src', 'lib', 'reportBuilder.ts'), 'utf8');
    const code = source
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '');
    // location.href / .host / .hostname / .origin all carry the instance
    // identity. pathname is the only member this module may read.
    for (const forbidden of ['location.href', 'location.host', 'location.origin', 'document.referrer']) {
      assert.ok(!code.includes(forbidden), `must not read ${forbidden} — it identifies the instance`);
    }
  });
});

/* ── Route shaping ────────────────────────────────────────────────────────── */

describe('routePattern', () => {
  test('replaces numeric ids', () => {
    assert.equal(routePattern('/app/book/1234'), '/app/book/:id');
    assert.equal(routePattern('/app/author/42/series/7'), '/app/author/:id/series/:id');
  });

  test('replaces slugs too — a title names the user library just as surely as an id', () => {
    assert.equal(routePattern('/app/book/the-hobbit'), '/app/book/:seg');
    assert.equal(routePattern('/app/author/ursula-k-le-guin'), '/app/author/:seg');
  });

  test('keeps known static segments legible', () => {
    assert.equal(routePattern('/app/settings'), '/app/settings');
    assert.equal(routePattern('/app/whats-new'), '/app/whats-new');
  });

  test('handles root and junk without throwing', () => {
    assert.equal(routePattern('/'), '/');
    assert.equal(routePattern(''), '/');
    assert.equal(routePattern(undefined as unknown as string), '/');
  });

  test('an unrecognised route added later still redacts by default', () => {
    // The allowlist must fail CLOSED: a route nobody updated this list for
    // should hide its data, not leak it.
    assert.equal(routePattern('/app/somefuturepage/secret-value'), '/app/:seg/:seg');
  });
});

/* ── Browser coarsening ───────────────────────────────────────────────────── */

describe('coarseBrowser', () => {
  test('identifies the common families', () => {
    const safari = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15';
    assert.equal(coarseBrowser(safari), 'Safari on macOS');

    const chrome = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36';
    assert.equal(coarseBrowser(chrome), 'Chrome on Windows');

    const firefox = 'Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0';
    assert.equal(coarseBrowser(firefox), 'Firefox on Linux');
  });

  test('Chromium families are not misread as Safari', () => {
    // Every Chromium UA also says "safari" — ordering is the whole trick here.
    const edge = 'Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0 Safari/537.36 Edg/120.0';
    assert.equal(coarseBrowser(edge), 'Edge on Windows');
  });

  test('never echoes any part of the input — a hostile UA cannot smuggle text', () => {
    const hostile = 'Mozilla/5.0 SECRET-TOKEN-abc123 (Macintosh; Mac OS X) Safari/605';
    const out = coarseBrowser(hostile);
    assert.ok(!out.includes('SECRET-TOKEN'), 'output must be assembled from literals only');
    assert.equal(out, 'Safari on macOS');
  });

  test('unknown input degrades safely', () => {
    assert.equal(coarseBrowser(''), 'Unknown browser');
    assert.equal(coarseBrowser(undefined), 'Unknown browser');
  });
});

/* ── Viewport bucketing ───────────────────────────────────────────────────── */

describe('viewportBucket', () => {
  test('buckets rather than reporting exact pixels', () => {
    assert.equal(viewportBucket(390), 'narrow (<600px)');
    assert.equal(viewportBucket(1440), 'extra wide (>=1280px)');
    // The exact number must not survive into the output.
    assert.ok(!viewportBucket(1437).includes('1437'));
  });

  test('handles nonsense', () => {
    assert.equal(viewportBucket(undefined), 'unknown');
    assert.equal(viewportBucket(NaN), 'unknown');
  });
});

/* ── Free-text scrubbing ──────────────────────────────────────────────────── */

describe('scrubFreeText', () => {
  test('removes the instance URL', () => {
    const out = scrubFreeText('Failed to fetch https://books.example.com/api/v1/book/12');
    assert.ok(!out.includes('books.example.com'), 'the instance host must not survive');
    assert.ok(out.includes('[url removed]'));
  });

  test('removes unix library paths', () => {
    const out = scrubFreeText('ENOENT: /books/Ursula K. Le Guin/The Dispossessed.epub');
    assert.ok(!out.includes('Le Guin'), 'a path names the user library contents');
    assert.ok(out.includes('[path removed]'));
  });

  test('removes windows paths including the drive letter', () => {
    const out = scrubFreeText(String.raw`Cannot open C:\Users\alex\Calibre\metadata.db`);
    assert.ok(!out.includes('alex'));
    assert.ok(!out.includes('C:'));
  });

  test('removes email addresses', () => {
    const out = scrubFreeText('login failed for someone@example.com');
    assert.ok(!out.includes('someone@example.com'));
  });

  test('a URL is fully removed rather than half-eaten into a bare host', () => {
    // Ordering regression: scrubbing unix paths first would consume "/a/b" and
    // leave "https://host" behind as a surviving token.
    const out = scrubFreeText('see https://secret.example.com/a/b/c for details');
    assert.ok(!out.includes('secret.example.com'));
  });

  test('keeps short, useful, non-identifying fragments legible', () => {
    const out = scrubFreeText('GET /api/v1 returned 500');
    assert.ok(out.includes('500'), 'the diagnostic value must survive scrubbing');
  });

  test('handles empty and non-string input', () => {
    assert.equal(scrubFreeText(''), '');
    assert.equal(scrubFreeText(undefined), '');
  });
});

/* ── Composition ──────────────────────────────────────────────────────────── */

describe('buildBody', () => {
  test('leads with the user description, not the diagnostics', () => {
    const body = buildBody('bug', CTX, 'The cover never loads.');
    assert.ok(body.indexOf('The cover never loads.') < body.indexOf('### Environment'));
  });

  test('includes the allowlisted environment fields', () => {
    const body = buildBody('bug', CTX, 'x');
    assert.ok(body.includes('v4.1.31'));
    assert.ok(body.includes('/app/book/:id'));
    assert.ok(body.includes('Safari on macOS'));
  });

  test('scrubs the error message on the way into the body', () => {
    const body = buildBody('bug', { ...CTX, errorMessage: 'boom at https://my.library.lan/x' }, 'x');
    assert.ok(!body.includes('my.library.lan'));
  });

  test('states plainly that nothing has been sent', () => {
    // The disclosure is load-bearing: the user is being asked to trust that
    // composing a report did not already report it.
    const body = buildBody('bug', CTX, 'x');
    assert.ok(/nothing was sent/i.test(body));
  });

  test('caps the component stack so one crash cannot fill the URL budget', () => {
    const stack = Array.from({ length: 100 }, (_, i) => `    at Component${i}`).join('\n');
    const body = buildBody('bug', { ...CTX, componentStack: stack }, 'x');
    assert.ok(!body.includes('Component50'));
  });
});

describe('buildTitle', () => {
  test('uses the error for a crash report', () => {
    const t = buildTitle('bug', { ...CTX, errorMessage: 'Cannot read property x of undefined' });
    assert.ok(t.startsWith('[Bug]'));
    assert.ok(t.includes('Cannot read property'));
  });

  test('falls back to the route shape', () => {
    assert.equal(buildTitle('feature', CTX), '[Feature request] on /app/book/:id');
  });

  test('never leaks a path through the title', () => {
    const t = buildTitle('bug', { ...CTX, errorMessage: 'ENOENT /books/Private Author/Book.epub' });
    assert.ok(!t.includes('Private Author'));
  });
});

/* ── URL construction ─────────────────────────────────────────────────────── */

describe('githubIssueUrl', () => {
  test('precomposes title and body as query parameters', () => {
    const url = githubIssueUrl('T', 'B');
    const parsed = new URL(url);
    assert.equal(parsed.searchParams.get('title'), 'T');
    assert.equal(parsed.searchParams.get('body'), 'B');
  });

  test('points at our own tracker', () => {
    assert.ok(githubIssueUrl('T', 'B').startsWith('https://github.com/new-usemame/Calibre-Web-NextGen/issues/new'));
  });

  test('truncates rather than producing a URL GitHub will reject', () => {
    // The bad failure mode this prevents: user clicks Report, gets a GitHub
    // error page, and the report is simply lost.
    const huge = 'x'.repeat(50000);
    const url = githubIssueUrl('T', huge);
    assert.ok(url.length <= 6000, `expected <=6000, got ${url.length}`);
    assert.ok(decodeURIComponent(new URL(url).searchParams.get('body') || '').includes('trimmed'));
  });

  test('special characters survive the round trip', () => {
    const body = 'crash: `a & b` <tag> 100% "quoted" #1234\nsecond line';
    const back = new URL(githubIssueUrl('T', body)).searchParams.get('body');
    assert.equal(back, body);
  });
});

describe('reportTarget', () => {
  test('bugs and features route to the tracker', () => {
    assert.ok(reportTarget('bug', CTX, 'x').url.includes('github.com'));
    assert.ok(reportTarget('feature', CTX, 'x').url.includes('github.com'));
  });

  test('questions route to Discord, where the text must be pasted', () => {
    const r = reportTarget('question', CTX, 'how do I...');
    assert.ok(r.url.includes('discord.gg'));
    assert.equal(r.needsManualPaste, true, 'Discord has no URL prefill — the user pastes it');
    assert.ok(r.body.includes('how do I...'), 'the composed body is still returned for the clipboard');
  });

  test('bug reports carry the bug label', () => {
    const url = new URL(reportTarget('bug', CTX, 'x').url);
    assert.equal(url.searchParams.get('labels'), 'bug');
  });
});

/* ── Regression pins for the space-in-path defect ─────────────────────────── */

describe('scrubFreeText: paths containing spaces (regression)', () => {
  // The original implementation used a \w-based segment pattern, which cannot
  // match a space — so it failed on precisely the paths that carry personal
  // information (author and title folders) while succeeding on tidy programmer
  // paths that carry none. Caught by the test above, pinned here.
  test('an author/title path is removed whole, not partially', () => {
    const out = scrubFreeText('ENOENT: /books/Ursula K. Le Guin/The Dispossessed.epub not found');
    assert.ok(!out.includes('Le Guin'), 'the author name must not survive');
    assert.ok(!out.includes('Dispossessed'), 'the title must not survive');
    assert.ok(out.includes('not found'), 'the diagnostic tail should survive');
  });

  test('a calibre library root with spaces is removed', () => {
    const out = scrubFreeText('cannot open /calibre-library/Iain M. Banks/Use of Weapons.epub');
    assert.ok(!out.includes('Iain'));
    assert.ok(!out.includes('Weapons'));
  });

  test('prose mentioning an in-app route is NOT eaten wholesale', () => {
    // The precision trade-off: rooted paths are consumed greedily, ordinary
    // route mentions are not, so an error stays readable.
    const out = scrubFreeText('open the /app/settings page and try again');
    assert.ok(out.includes('and try again'), `over-redacted: ${out}`);
  });
});

describe('scrubFreeText: initial-laden author directories (regression)', () => {
  // An extension anchor that does not require a token boundary stops at the
  // ".RR" of "J.R.R.", removing the path prefix but passing the surname on to a
  // public tracker — a partial redaction that reads as a successful one.
  test('a J.R.R.-style directory does not leak the surname', () => {
    const out = scrubFreeText('failed: /books/J.R.R. Tolkien/The Hobbit.epub missing');
    assert.ok(!out.includes('Tolkien'), `surname leaked: ${out}`);
    assert.ok(!out.includes('Hobbit'));
    assert.ok(out.includes('missing'), 'the diagnostic tail should still survive');
  });

  test('a bare library directory is removed even with no filename', () => {
    const out = scrubFreeText('cannot write to /storage/media/Private Author');
    assert.ok(!out.includes('Private Author'));
  });
});

/* ── Markdown injection ───────────────────────────────────────────────────── */

/** The longest run of backticks anywhere in `s`. */
function longestRun(s: string): number {
  return (s.match(/`+/g) || []).reduce((m, r) => Math.max(m, r.length), 0);
}

/** The opening fence of the first fenced block in a body. */
function openingFence(body: string): string {
  const m = body.match(/^`{3,}$/m);
  assert.ok(m, 'no fenced block found');
  return m![0];
}

describe('markdown injection via library-controlled text', () => {
  // An error message can quote a book title or filename. A plain ``` fence
  // closes at the first ``` inside it, and everything after renders as live
  // Markdown in a PUBLIC issue — headings, links, and images GitHub will fetch.
  // Scrubbing is no help: breaking the structure needs no URL.
  //
  // The correctness property is CommonMark's own: a fence of N backticks
  // contains any run of fewer than N. So assert the fence is strictly longer
  // than the longest run in the payload — not that fences "balance", since a
  // naive count also matches the payload's own backticks.
  test('a backtick fence in the error cannot break out of the code block', () => {
    const hostile = 'Failed to open ```\n## Injected heading\n```';
    const body = buildBody('bug', { ...CTX, errorMessage: hostile }, 'x');
    assert.ok(
      openingFence(body).length > longestRun(hostile),
      `fence ${openingFence(body).length} must exceed payload run ${longestRun(hostile)}`,
    );
  });

  test('a backtick fence in the component stack cannot break out either', () => {
    const stack = 'at Foo\n```\n### injected\n';
    const body = buildBody('bug', { ...CTX, componentStack: stack }, 'x');
    assert.ok(openingFence(body).length > longestRun(stack));
  });

  test('longer backtick runs escalate the fence further', () => {
    const hostile = 'x ````` y';
    const body = buildBody('bug', { ...CTX, errorMessage: hostile }, 'x');
    assert.ok(openingFence(body).length >= 6, `got ${openingFence(body).length}`);
  });

  test('ordinary errors still use a plain three-backtick fence', () => {
    const body = buildBody('bug', { ...CTX, errorMessage: 'Cannot read x of undefined' }, 'x');
    assert.ok(body.includes('```\nCannot read x of undefined\n```'));
  });
});

/* ── collectContext: runs on the crash path ───────────────────────────────── */

/**
 * Install a minimal global shim; returns a restore fn. No jsdom (new dep).
 *
 * Uses defineProperty rather than assignment: Node ships a real, GETTER-ONLY
 * `navigator` global, so `globalThis.navigator = x` throws outright.
 */
function withGlobals(g: Record<string, unknown>): () => void {
  const keys = ['window', 'navigator', 'document'];
  const saved = keys.map((k) => [k, Object.getOwnPropertyDescriptor(globalThis, k)] as const);
  for (const k of keys) {
    Object.defineProperty(globalThis, k, { value: g[k], configurable: true, writable: true });
  }
  return () => {
    for (const [k, desc] of saved) {
      if (desc) Object.defineProperty(globalThis, k, desc);
      else delete (globalThis as never as Record<string, unknown>)[k];
    }
  };
}

describe('collectContext', () => {
  // This runs INSIDE the ErrorBoundary, i.e. after the app has already crashed
  // once. A throw here replaces the recovery UI with the blank screen the
  // boundary exists to prevent, so every read is individually guarded — and
  // that guarding is what these tests exercise.
  test('reads the allowlisted fields when everything is present', () => {
    const restore = withGlobals({
      window: { __CWNG_VERSION__: 'v4.1.31', location: { pathname: '/app/book/12' }, innerWidth: 390 },
      navigator: { userAgent: 'Mozilla/5.0 (iPhone) AppleWebKit Safari/605' },
      document: { documentElement: { getAttribute: () => 'dark' } },
    });
    try {
      const ctx = collectContext();
      assert.equal(ctx.version, 'v4.1.31');
      assert.equal(ctx.routePattern, '/app/book/:id');
      assert.equal(ctx.browser, 'Safari on iOS');
      assert.equal(ctx.theme, 'dark');
      assert.equal(ctx.viewport, 'narrow (<600px)');
    } finally {
      restore();
    }
  });

  test('an older server that injects no version degrades to "unknown"', () => {
    const restore = withGlobals({
      window: { location: { pathname: '/app' }, innerWidth: 1440 },
      navigator: { userAgent: 'Firefox/121.0' },
      document: { documentElement: { getAttribute: () => null } },
    });
    try {
      assert.equal(collectContext().version, 'unknown');
    } finally {
      restore();
    }
  });

  test('hostile getters cannot throw out of collectContext', () => {
    // Each read is guarded separately, so one bad global must not take the
    // whole report — and must never surface as a second crash.
    const boom = () => { throw new Error('nope'); };
    const restore = withGlobals({
      window: {
        get __CWNG_VERSION__() { return boom(); },
        get location() { return boom(); },
        get innerWidth() { return boom(); },
      },
      navigator: { get userAgent() { return boom(); } },
      document: { get documentElement() { return boom(); } },
    });
    try {
      const ctx = collectContext();
      assert.equal(ctx.version, 'unknown');
      assert.equal(ctx.routePattern, '/');
      assert.equal(ctx.browser, 'Unknown browser');
      assert.equal(ctx.theme, undefined);
    } finally {
      restore();
    }
  });

  test('overrides win, so the boundary can supply the error it is holding', () => {
    const restore = withGlobals({
      window: { location: { pathname: '/app' }, innerWidth: 800 },
      navigator: { userAgent: 'Safari/605' },
      document: { documentElement: { getAttribute: () => null } },
    });
    try {
      const ctx = collectContext({ errorMessage: 'boom', componentStack: 'at X' });
      assert.equal(ctx.errorMessage, 'boom');
      assert.equal(ctx.componentStack, 'at X');
    } finally {
      restore();
    }
  });

  test('the full path from a crash to a URL never throws', () => {
    const restore = withGlobals({ window: undefined, navigator: undefined, document: undefined });
    try {
      const url = reportTarget('bug', collectContext({ errorMessage: 'x' }), '').url;
      assert.ok(url.startsWith('https://github.com/'));
    } finally {
      restore();
    }
  });
});
