import { test, expect, type Page } from '@playwright/test';
import { designerPanel, firstBookId } from './fixtures/cover-designer-v2';

/*
 * Cover designer v2 LIVE — the same flows as cover-designer.spec.ts but against
 * the real backend with NO page.route fixtures: the catalogue comes from the
 * server's /cover/state payload, previews are real renders (calibre or Pillow
 * in the container), presets persist server-side across a reload, and apply
 * repaints the book's cover. This is the seam proof for the two v2 legs.
 *
 * Renders cost a subprocess each, so this spec is deliberately one careful
 * pass with generous timeouts rather than a matrix.
 */

const RENDER = 30_000; // a single warm server render can take seconds
const FIRST_RENDER = 120_000; // cold calibre first render in a fresh container

/** The live flow saves a preset; a crashed earlier run would leave it behind
 *  and, since every run designs the same cover, the dropdown's design match
 *  would name the leftover instead of the fresh one. Remove any leftovers via
 *  the real API before (and after) the flow. */
async function cleanupLivePresets(page: Page, prefix: string) {
  await page.evaluate(async (startsWith) => {
    const list = await fetch('/cover-designer/presets', {
      headers: { Accept: 'application/json' }, credentials: 'include',
    }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    // Only this project's leftovers: the sibling project running in parallel
    // owns its own names and must not lose a preset mid-flight.
    const doomed = ((list?.presets ?? []) as { id: string; name: string }[])
      .filter((p) => p.name.startsWith(startsWith));
    if (!doomed.length) return;
    const csrf = await fetch('/api/v1/auth/csrf', { credentials: 'include' })
      .then((r) => r.json()).then((d) => d.csrf_token as string);
    for (const p of doomed) {
      await fetch(`/cover-designer/presets/${p.id}`, {
        method: 'DELETE', headers: { 'X-CSRFToken': csrf }, credentials: 'include',
      });
    }
  }, prefix);
}

/** Wait for the debounced render cycle to finish: the preview frame's
 *  aria-busy flips true on change and false when the new image lands. */
async function waitForRender(panel: ReturnType<typeof designerPanel>, previousSrc: string | null) {
  const preview = panel.getByRole('img', { name: 'Preview of the designed cover' });
  if (previousSrc) {
    // The outgoing render stays on screen until the new one lands, so a src
    // change is the completion signal.
    await expect(preview).not.toHaveAttribute('src', previousSrc, { timeout: RENDER });
  } else {
    // No prior image: the img only mounts once the first preview arrives.
    await expect(preview).toBeVisible({ timeout: FIRST_RENDER });
  }
  await expect(panel.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: RENDER });
  return preview.getAttribute('src');
}

/** A cold container renders every catalogue image with a fresh calibre-debug
 *  subprocess (fontconfig cache, plugin init) — the panel opening on 8+
 *  concurrent cold renders starves the preview pool. The images are cached on
 *  disk once drawn, so fetch them all up front, sequentially, and let the
 *  page's own <img> loads then hit the cache. */
async function warmCatalogueImages(page: Page, panel: ReturnType<typeof designerPanel>) {
  const srcs = await panel.locator('img').evaluateAll((imgs) =>
    imgs.map((i) => i.getAttribute('src')).filter((s): s is string => !!s && s.includes('cover-designer')));
  for (const src of srcs) {
    await page.evaluate(async (u) => {
      await fetch(u!, { credentials: 'include' }).then((r) => r.arrayBuffer()).catch(() => {});
    }, src);
  }
}

test.describe('cover designer v2 (live backend, no fixtures)', () => {
  test('real server font samples load as distinct, scrollable previews', async ({ page }) => {
    test.setTimeout(FIRST_RENDER);
    const id = await firstBookId(page);
    test.skip(!id, 'seed has no books');
    await page.setViewportSize({ width: 390, height: 844 });
    // Candidate providers are deliberately out of scope for a font-preview
    // proof and can make the test wait on an outside service.
    await page.route('**/cover/candidates*', (route) =>
      route.fulfill({ json: { candidates: [], providers: [], query: 'seed' } }));
    await page.goto(`/app/book/${id}/cover`);
    const panel = designerPanel(page);
    await panel.locator(':scope > summary').first().click();

    const lettering = panel.getByRole('radiogroup', { name: 'Lettering' });
    const cards = lettering.getByRole('radio');
    await expect(cards.first()).toBeVisible({ timeout: FIRST_RENDER });
    const samples = lettering.locator('img[data-font-sample]');
    await expect(samples.first()).toBeVisible({ timeout: FIRST_RENDER });
    // The strip is intentionally lazy: a phone should load the cards in view,
    // not download every installed font before it can be used. Two loaded
    // samples are enough to prove real distinct rendering here; the focused
    // Python regression checks every catalogue image byte-for-byte.
    const loadedSamples = async () => samples.evaluateAll((imgs) => imgs
      .filter((img): img is HTMLImageElement => img instanceof HTMLImageElement && img.complete && img.naturalWidth > 0)
      .map((img) => ({ id: img.dataset.fontSample ?? '', src: img.currentSrc })));
    await expect.poll(async () => (await loadedSamples()).length, { timeout: FIRST_RENDER }).toBeGreaterThanOrEqual(2);

    // Fetch the bytes from the logged-in live server. Distinct checksums prove
    // that the cards contain distinct server-rendered glyphs, rather than a
    // visual CSS stack or a repeated placeholder JPEG.
    const loaded = await loadedSamples();
    const signatures = await page.evaluate(async (items: { id: string; src: string }[]) => Promise.all(items.map(async ({ id, src }) => {
      const buffer = await fetch(src, { credentials: 'include' }).then((r) => r.arrayBuffer());
      const bytes = new Uint8Array(buffer as ArrayBuffer);
      let hash = 2166136261;
      for (const byte of bytes) hash = Math.imul(hash ^ byte, 16777619);
      return { id, signature: `${bytes.length}:${hash >>> 0}` };
    })), loaded);
    const signatureById = new Map(signatures.map(({ id, signature }) => [id, signature]));
    // Generic names can intentionally resolve to their installed counterpart
    // (for example `serif` → Liberation Serif). The useful promise is that
    // different font families render different glyph pixels.
    expect(new Set(['serif', 'sans', 'mono'].map((id) => signatureById.get(id))).size).toBe(3);

    const geometry = await lettering.evaluate((el) => ({
      clientWidth: el.clientWidth,
      scrollWidth: el.scrollWidth,
      overflowX: getComputedStyle(el).overflowX,
      snap: getComputedStyle(el).scrollSnapType,
    }));
    expect(geometry.scrollWidth).toBeGreaterThan(geometry.clientWidth);
    expect(geometry.overflowX).toBe('auto');
    expect(geometry.snap).toContain('x');

    await cards.first().focus();
    await page.keyboard.press('End');
    await expect(cards.last()).toBeFocused();
    await expect.poll(() => lettering.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0);
  });

  test('arrangements, custom colours, preset persistence, hide/restore, apply', async ({ page }) => {
    test.setTimeout(240_000);
    const id = await firstBookId(page);
    test.skip(!id, 'seed has no books');

    // Every DESIGNER surface is the real server — catalogue, previews, presets,
    // apply. The candidate fan-out is not a designer surface: it queries
    // external metadata providers, which is slow and rate-limited from inside
    // a container, so the page is kept quiet exactly like the fixture spec.
    await page.route('**/cover/candidates*', (route) =>
      route.fulfill({ json: { candidates: [], providers: [], query: 'seed' } }));

    const presetPrefix = `Live Check ${test.info().project.name} `;
    const presetName = `${presetPrefix}${Date.now() % 100000}`;
    await cleanupLivePresets(page, presetPrefix);

    await page.goto(`/app/book/${id}/cover`);
    const panel = designerPanel(page);
    await panel.locator(':scope > summary').first().click();

    // Warm the on-disk catalogue image cache before the timed assertions:
    // cold-calibre thumbnails would otherwise eat the preview's pool slots.
    await warmCatalogueImages(page, panel);

    // Real catalogue, real first render.
    const preview = panel.getByRole('img', { name: 'Preview of the designed cover' });
    await expect(preview).toBeVisible({ timeout: FIRST_RENDER });
    let src = await preview.getAttribute('src');
    expect(src).toMatch(/^data:image\//);
    await expect(panel.getByRole('combobox', { name: 'Preset' })).toContainText('Classic');

    // Every arrangement renders, and the picture changes each time. Blocks is
    // the default, so start elsewhere: each click must actually change the
    // style or there is nothing to wait for.
    for (const style of ['Banner', 'Ornamental', 'The Cross', 'Half and half', 'Blocks']) {
      const radio = panel.getByRole('radio', { name: style });
      await radio.scrollIntoViewIfNeeded();
      await radio.click();
      src = await waitForRender(panel, src);
      expect(src).toMatch(/^data:image\//);
    }

    // Custom colours: the "+" swatch opens the popover; a hex edit re-renders
    // and the caption switches to the custom state.
    await panel.getByRole('button', { name: 'Custom colours' }).click();
    const popover = page.getByRole('dialog', { name: 'Custom colours' });
    await popover.getByLabel('Background hex value').fill('#2b6a4f');
    src = await waitForRender(panel, src);
    await expect(panel.getByRole('group', { name: 'Colour scheme' })
      .getByText('Custom', { exact: true })).toBeVisible();
    await popover.getByRole('button', { name: 'Done' }).click();

    // Save the design as a preset, reload the page, and find it in the
    // dropdown — persistence is server-side, not a page-session trick.
    await panel.getByRole('button', { name: 'Save as preset' }).click();
    const saveDialog = page.getByRole('dialog', { name: 'Save as preset' });
    await saveDialog.getByLabel('Preset name').fill(presetName);
    await saveDialog.getByRole('button', { name: 'Save preset' }).click();
    await expect(panel.getByRole('combobox', { name: 'Preset' })).toHaveText(presetName);
    await expect(panel.getByText('Preset saved.')).toBeVisible();

    await page.reload();
    const panel2 = designerPanel(page);
    await panel2.locator(':scope > summary').first().click();
    await warmCatalogueImages(page, panel2);
    const presetSelect2 = panel2.getByRole('combobox', { name: 'Preset' });
    await presetSelect2.click();
    await expect(page.getByRole('option', { name: presetName })).toBeVisible();
    await page.getByRole('option', { name: presetName }).click();
    await expect(presetSelect2).toHaveText(presetName);
    // The reloaded preset renders its saved custom colours.
    src = await waitForRender(panel2, null);
    expect(src).toMatch(/^data:image\//);

    // Hide a shipped preset and restore it from the manager. The two projects
    // share one seed server, so each hides a different built-in rather than
    // racing on the same row.
    const builtinToHide = test.info().project.name === 'mobile' ? 'Noir' : 'Midnight';
    await panel2.getByRole('button', { name: 'Manage presets…' }).click();
    const manage = page.getByRole('dialog', { name: 'Manage presets' });
    const builtinRow = () => manage.locator('li').filter({ hasText: builtinToHide });
    await builtinRow().getByRole('button', { name: 'Hide' }).click();
    await builtinRow().getByRole('button', { name: 'Hide' }).click(); // inline confirm
    await expect(manage.getByText('Hidden built-ins')).toBeVisible();
    await builtinRow().getByRole('button', { name: 'Restore' }).click();
    await expect(manage.getByText('Hidden built-ins')).toBeHidden();
    await page.keyboard.press('Escape');
    await presetSelect2.click();
    await expect(page.getByRole('option', { name: builtinToHide })).toBeVisible();
    await page.keyboard.press('Escape');

    // Apply: the cover on the page changes to the freshly rendered design.
    const currentImg = page.locator('aside img').first();
    const before = await currentImg.getAttribute('src');
    await panel2.getByRole('button', { name: 'Use this design' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Cover updated.' })).toBeVisible();
    await expect(currentImg).not.toHaveAttribute('src', before ?? '');

    // Cleanup: remove the live preset so reruns start clean.
    await panel2.getByRole('button', { name: 'Manage presets…' }).click();
    const manage2 = page.getByRole('dialog', { name: 'Manage presets' });
    const liveRow = () => manage2.locator('li').filter({ hasText: presetName });
    await liveRow().getByRole('button', { name: 'Delete' }).click();
    await liveRow().getByRole('button', { name: 'Delete' }).click(); // inline confirm
    await expect(liveRow()).toHaveCount(0);
    await page.keyboard.press('Escape');
    await cleanupLivePresets(page, presetPrefix); // belt and braces if the UI delete raced
  });
});
