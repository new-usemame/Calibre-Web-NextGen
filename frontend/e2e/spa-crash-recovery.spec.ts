import { test, expect, Page } from '@playwright/test';
import { CONCRETE_THEMES } from '../src/lib/themes';

/*
 * #855 regression — the SPA had NO error boundary, so any error thrown during
 * render unmounted the whole tree back to the root. #root went empty and all the
 * user saw was the bare page background: "the screen went black and nothing
 * else. Had to close the browser and restart the session" (@monimkxl-web).
 *
 * The trigger used here is a real production failure, not a synthetic one: the
 * reader is code-split (App.tsx `lazy(() => import('./pages/Reader'))`), so when
 * its chunk cannot be fetched — a browser holding a stale index.html after a
 * container upgrade, or a flaky network — the dynamic import rejects and React
 * throws during render. Measured on the pre-fix build, that left
 * `#root.innerHTML === ''` with a `Failed to fetch dynamically imported module`
 * pageerror and no way back in-app.
 *
 * These tests assert the crash is CONTAINED and RECOVERABLE. They are red on
 * main (no fallback exists — the boundary testid never appears and #root is
 * empty) and green with the boundary.
 */

/** Simulate the unreachable lazy chunk. Returns a probe for how many times the
 *  chunk was actually intercepted, so a test can prove the fallback it sees came
 *  from THIS failure and not some unrelated startup error. */
async function breakReaderChunk(page: Page): Promise<() => number> {
  let aborted = 0;
  await page.route('**/assets/Reader-*.js', async (r) => {
    aborted += 1;
    await r.abort();
  });
  return () => aborted;
}

async function firstBookId(page: Page): Promise<number | null> {
  const res = await page.request.get('/api/v1/books?per_page=1');
  const body = (await res.json()) as { total: number; items: Array<{ id: number }> };
  return (body.total ?? 0) > 0 ? body.items[0].id : null;
}

test.describe('#855 SPA crash containment', () => {
  test('a failed lazy chunk shows a recoverable error screen, not a blank page', async ({ page }) => {
    const id = await firstBookId(page);
    test.skip(id === null, 'no seeded book to open in the reader');

    const abortCount = await breakReaderChunk(page);
    await page.goto(`/app/read/${id}`);

    const boundary = page.getByTestId('app-error-boundary');
    await expect(boundary).toBeVisible();

    // The fallback must be caused by the chunk we broke, not by something else.
    expect(abortCount(), 'Reader chunk was never intercepted').toBeGreaterThan(0);

    // The precise symptom from the report: the root must not be emptied out.
    const rootLen = await page.evaluate(
      () => document.getElementById('root')?.innerHTML.length ?? 0,
    );
    expect(rootLen, '#root was emptied — the whole tree unmounted (#855)').toBeGreaterThan(0);

    // Recovery controls the user can actually reach without restarting the browser.
    await expect(page.getByRole('button', { name: /reload/i })).toBeVisible();
    await expect(page.getByRole('link', { name: /back to library/i })).toBeVisible();

    // The concise message is the live region — not the whole container, which
    // would announce heading + prose + both controls + disclosure as one blob.
    await expect(boundary.locator('[role="alert"]')).toHaveCount(1);

    // The crash unmounts whatever was focused; focus must land on the fallback
    // heading so keyboard and screen-reader users are not dropped on <body>.
    await expect(page.getByRole('heading', { name: /something went wrong/i })).toBeFocused();
  });

  test('a falsy thrown value is contained too (throw null)', async ({ page }) => {
    const id = await firstBookId(page);
    test.skip(id === null, 'no seeded book to open in the reader');

    // `throw null` is legal JS, and a boundary that keys its fallback off the
    // truthiness of the caught value would render the crashing subtree again and
    // loop right back to the blank screen. Serving a chunk that throws a falsy
    // value at module scope reproduces that exactly: the dynamic import rejects
    // with `null`, so React hands `null` to getDerivedStateFromError.
    await page.route('**/assets/Reader-*.js', (r) =>
      r.fulfill({ status: 200, contentType: 'application/javascript', body: 'throw null;' }),
    );

    await page.goto(`/app/read/${id}`);

    await expect(page.getByTestId('app-error-boundary')).toBeVisible();
    const rootLen = await page.evaluate(
      () => document.getElementById('root')?.innerHTML.length ?? 0,
    );
    expect(rootLen, '#root emptied on a falsy throw').toBeGreaterThan(0);
  });

  test('"Back to library" escapes the broken route', async ({ page }) => {
    const id = await firstBookId(page);
    test.skip(id === null, 'no seeded book to open in the reader');

    await breakReaderChunk(page);
    await page.goto(`/app/read/${id}`);
    await expect(page.getByTestId('app-error-boundary')).toBeVisible();

    await page.getByRole('link', { name: /back to library/i }).click();

    // Lands on a working library — the session is not stranded.
    await expect(page).toHaveURL(/\/app\/?$/);
    await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();
    await expect(page.getByTestId('app-error-boundary')).toHaveCount(0);
  });

  test('the fallback is legible in every concrete theme', async ({ page }) => {
    const id = await firstBookId(page);
    test.skip(id === null, 'no seeded book to open in the reader');

    await breakReaderChunk(page);
    await page.goto(`/app/read/${id}`);
    await expect(page.getByTestId('app-error-boundary')).toBeVisible();

    // Every concrete palette, from the theme registry itself — a new theme must
    // not be able to ship a fallback nobody can read.
    for (const theme of CONCRETE_THEMES) {
      await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);

      const readable = await page.evaluate(() => {
        const el = document.querySelector('[data-testid="app-error-boundary"] h1');
        if (!el) return null;
        const cs = getComputedStyle(el);
        const box = el.getBoundingClientRect();
        const parse = (c: string) => (c.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
        // Relative luminance (WCAG) of the heading vs the card behind it.
        const lum = (rgb: number[]) => {
          const [r, g, b] = rgb.map((v) => {
            const s = v / 255;
            return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
          });
          return 0.2126 * r + 0.7152 * g + 0.0722 * b;
        };
        // Walk up for the first non-transparent background.
        let node: Element | null = el;
        let bg = 'rgba(0, 0, 0, 0)';
        while (node) {
          const c = getComputedStyle(node).backgroundColor;
          if (c && !/rgba\(0, 0, 0, 0\)|transparent/.test(c)) { bg = c; break; }
          node = node.parentElement;
        }
        const l1 = lum(parse(cs.color));
        const l2 = lum(parse(bg));
        const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
        return { ratio, visible: box.width > 0 && box.height > 0, text: (el.textContent || '').trim() };
      });

      expect(readable, `no heading rendered in ${theme}`).not.toBeNull();
      expect(readable!.visible, `fallback heading has no box in ${theme}`).toBeTruthy();
      expect(readable!.text.length, `fallback heading empty in ${theme}`).toBeGreaterThan(0);
      // WCAG AA for large text is 3:1; the heading is 22px bold-ish.
      expect(readable!.ratio, `fallback heading contrast too low in ${theme}`).toBeGreaterThanOrEqual(3);
    }
  });
});
