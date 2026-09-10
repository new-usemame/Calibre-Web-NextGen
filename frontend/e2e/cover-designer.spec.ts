import { test, expect } from '@playwright/test';

/*
 * "Design a cover" — the picker panel that builds a cover from the book's own
 * title and author when no source has one.
 *
 * The renderer itself is unit-tested; what only a browser can tell us is the
 * client contract around it: the panel renders nothing until it is opened, the
 * preview it shows is what the SERVER returned (never a client-side drawing),
 * changing a design re-renders, and applying sends design ids — no pixels — and
 * updates the cover on the page.
 *
 * The render endpoint is route-mocked so the spec does not depend on Calibre
 * being installed in the container and does not pay for a subprocess per run.
 * Each mocked render is a distinct 1x1 JPEG-shaped payload keyed by the design,
 * so "the preview changed" is a real observation rather than a spinner.
 */

// Distinguishable data URLs: the trailing marker names the design that produced
// them, so an assertion can say WHICH render is on screen.
const dataUrlFor = (scheme: string, layout: string) =>
  `data:image/jpeg;base64,${Buffer.from(`rendered:${scheme}:${layout}`).toString('base64')}`;

test.describe('cover designer', () => {
  test('renders, re-renders on a design change, and applies design ids only', async ({ page }) => {
    await page.goto('/app/');
    const id = await page.evaluate(async () => {
      const r = await fetch('/api/v1/books?per_page=1', { headers: { Accept: 'application/json' } })
        .then((x) => (x.ok ? x.json() : null)).catch(() => null);
      return r?.items?.[0]?.id ?? null;
    });
    test.skip(!id, 'seed has no books');

    const previewBodies: Record<string, string>[] = [];
    await page.route('**/cover/design-preview*', async (route) => {
      const body = (route.request().postDataJSON() ?? {}) as Record<string, string>;
      previewBodies.push(body);
      await route.fulfill({
        json: {
          ok: true,
          data_url: dataUrlFor(body.scheme, body.layout),
          renderer: 'pil',
          resolved: { ...body, width: 600, height: 800 },
        },
      });
    });

    const applyBodies: Record<string, unknown>[] = [];
    await page.route('**/cover/apply', async (route) => {
      const body = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
      if (body.kind !== 'generated') { await route.fallback(); return; }
      applyBodies.push(body);
      await route.fulfill({ json: { ok: true, cover_url: `/cover/${id}/og?ts=designed` } });
    });

    // Candidate fan-out is irrelevant here and slow; keep the page quiet.
    await page.route('**/cover/candidates*', (route) =>
      route.fulfill({ json: { candidates: [], providers: [], query: 'seed' } }));

    await page.goto(`/app/book/${id}/cover`);

    const panel = page.locator('details').filter({ hasText: 'Design a cover' });
    const summary = panel.locator('summary');
    await expect(summary).toBeVisible();

    // Rendering costs a server subprocess: nothing is requested until the
    // reader actually opens the panel.
    expect(previewBodies).toHaveLength(0);

    await summary.click();
    const preview = page.getByRole('img', { name: 'Preview of the designed cover' });
    await expect(preview).toBeVisible();
    const firstSrc = await preview.getAttribute('src');
    expect(firstSrc).toBe(dataUrlFor(previewBodies[0].scheme, previewBodies[0].layout));

    // The panel opens on the library's default design, so exactly one chip is lit.
    await expect(panel.getByRole('radio', { checked: true })).toHaveCount(1);

    // Changing the arrangement re-renders, and the picture on screen changes to
    // the one the server returned for the NEW design.
    await panel.getByLabel('Arrangement').scrollIntoViewIfNeeded();
    await panel.getByLabel('Arrangement').selectOption('banner');
    const latest = () => previewBodies[previewBodies.length - 1];
    await expect.poll(() => latest()?.layout).toBe('banner');
    await expect(preview).not.toHaveAttribute('src', firstSrc!);
    await expect(preview).toHaveAttribute('src', dataUrlFor(latest().scheme, 'banner'));

    // Diverging from a preset must un-light its chip: the preset names a
    // combination, and after this change the combination is nobody's preset.
    // A chip that stayed lit would be the control lying about what is rendered.
    await expect(panel.getByRole('radio', { checked: true })).toHaveCount(0);

    // Picking a preset moves all three controls together. The chip is the
    // target, not the visually-collapsed radio behind it (SC 2.5.8).
    const ember = panel.getByText('Ember', { exact: true });
    await ember.scrollIntoViewIfNeeded();
    await ember.click();
    await expect(panel.getByRole('radio', { name: 'Ember' })).toBeChecked();
    await expect.poll(() => latest()?.scheme).toBe('ember');

    const useIt = panel.getByRole('button', { name: 'Use this design' });
    await useIt.scrollIntoViewIfNeeded();
    await useIt.click();
    await expect.poll(() => applyBodies.length).toBe(1);

    // The apply body carries design ids and nothing that could be pixels.
    const applied = applyBodies[0];
    expect(applied).toMatchObject({ kind: 'generated', scheme: 'ember' });
    expect(Object.keys(applied).sort()).toEqual(['font', 'kind', 'layout', 'scheme']);
    for (const value of Object.values(applied)) {
      expect(String(value)).not.toContain('data:');
    }

    await expect(page.getByRole('status').filter({ hasText: 'Cover updated.' })).toBeVisible();
  });

  test('a render failure is reported instead of leaving an empty frame', async ({ page }) => {
    await page.goto('/app/');
    const id = await page.evaluate(async () => {
      const r = await fetch('/api/v1/books?per_page=1', { headers: { Accept: 'application/json' } })
        .then((x) => (x.ok ? x.json() : null)).catch(() => null);
      return r?.items?.[0]?.id ?? null;
    });
    test.skip(!id, 'seed has no books');

    await page.route('**/cover/candidates*', (route) =>
      route.fulfill({ json: { candidates: [], providers: [], query: 'seed' } }));
    await page.route('**/cover/design-preview*', (route) => route.fulfill({
      status: 502,
      json: { ok: false, error_code: 'render_failed',
              error_message: 'Could not design a cover for this book.' },
    }));

    await page.goto(`/app/book/${id}/cover`);
    const panel = page.locator('details').filter({ hasText: 'Design a cover' });
    await panel.locator('summary').click();
    await expect(panel.getByRole('alert')
      .filter({ hasText: 'Could not design a cover for this book.' })).toBeVisible();
    // A failed render must not leave an apply button armed over nothing.
    await expect(panel.getByRole('button', { name: 'Use this design' })).toBeDisabled();
  });
});
