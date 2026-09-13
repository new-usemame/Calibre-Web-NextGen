import { test, expect, type Page } from '@playwright/test';
import { CATALOGUE_V2 } from './fixtures/cover-designer-v2';
import type { CataloguePreset, CoverDesign } from '../src/features/coverDesigner/contract';

/*
 * "Design a cover" v2 — preset management, arrangement thumbnails, colour
 * swatches + custom-colour popover, lettering samples, and the Advanced
 * disclosure, all the way to the preview/apply request bodies.
 *
 * EVERY designer endpoint here is route-mocked with CONTRACT FIXTURES
 * (fixtures/cover-designer-v2.ts mirrors state/cover-designer/v2/CONTRACT.md):
 * the v2 backend is built in a parallel branch, so this spec must pass against
 * any server. What is asserted is the client's half of the contract: which
 * requests fire, what bodies they carry, and what the UI shows as a result.
 *
 * Each mocked render is a distinct data URL keyed by the design that produced
 * it, so "the preview changed" is a real observation rather than a spinner.
 */

interface Captured {
  previewBodies: { design: CoverDesign }[];
  applyBodies: Record<string, unknown>[];
  presetPosts: { name: string; design: CoverDesign; scope?: string }[];
  presetPuts: { id: string; body: { name?: string } }[];
  presetDeletes: string[];
  presetRestores: string[];
}

const dataUrlFor = (design: CoverDesign) => {
  const marker = [
    design.style ?? '-',
    design.scheme === null ? 'custom' : design.scheme ?? '-',
    design.colors?.background ?? '-',
    design.fonts?.title?.family ?? '-',
    design.fonts?.title?.size ?? '-',
    design.align?.title ?? '-',
    design.text?.title ?? '-',
    design.size?.width ?? '-',
  ].join('|');
  return `data:image/jpeg;base64,${Buffer.from(`rendered:${marker}`).toString('base64')}`;
};

/** A tiny stand-in for the server-rendered style thumbnails: an SVG that names
 *  the style, so a loaded thumbnail is distinguishable from the inline fallback
 *  glyph (which has no <img>). */
const thumbSvg = (id: string) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="#999"/><text x="100" y="150" font-size="20" text-anchor="middle" fill="#fff">${id}</text></svg>`;
const fontSvg = (id: string) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80"><rect width="240" height="80" fill="#eee"/><text x="120" y="52" font-size="36" text-anchor="middle" fill="#333">Aa ${id}</text></svg>`;

async function installContractFixtures(page: Page, bookId: number): Promise<Captured> {
  const captured: Captured = {
    previewBodies: [], applyBodies: [], presetPosts: [], presetPuts: [], presetDeletes: [], presetRestores: [],
  };
  // Preset storage lives in the fixture closure: POST/PUT/DELETE mutate it and
  // GET re-reads it, exactly like the contract server will.
  let presets: CataloguePreset[] = structuredClone(CATALOGUE_V2.presets);
  let userCounter = 0;

  await page.route(`**/book/${bookId}/cover/state*`, (route) => route.fulfill({
    json: {
      locked: false,
      ereader_enabled: false,
      ereader_defaults: { aspect: 'kobo_libra_color', fill_mode: 'edge_mirror', color: '' },
      designer: { available: true, renderer: 'pil', catalogue: CATALOGUE_V2 },
    },
  }));

  await page.route('**/cover-designer/presets**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const tail = url.pathname.split('/cover-designer/presets')[1] ?? '';
    const idMatch = tail.match(/^\/([^/]+)$/);
    const restoreMatch = tail.match(/^\/([^/]+)\/restore$/);

    if (restoreMatch && req.method() === 'POST') {
      const preset = CATALOGUE_V2.presets.find((p) => p.id === restoreMatch[1]);
      captured.presetRestores.push(restoreMatch[1]);
      if (preset && !presets.some((p) => p.id === preset.id)) presets = [...presets, preset];
      await route.fulfill({ json: { preset } });
      return;
    }
    if (idMatch && req.method() === 'PUT') {
      const body = (req.postDataJSON() ?? {}) as { name?: string };
      captured.presetPuts.push({ id: idMatch[1], body });
      const preset = presets.find((p) => p.id === idMatch[1]);
      if (preset && body.name) preset.name = body.name;
      await route.fulfill({ json: { preset } });
      return;
    }
    if (idMatch && req.method() === 'DELETE') {
      captured.presetDeletes.push(idMatch[1]);
      presets = presets.filter((p) => p.id !== idMatch[1]);
      await route.fulfill({ status: 204, body: '' });
      return;
    }
    if (req.method() === 'POST') {
      const body = (req.postDataJSON() ?? {}) as { name: string; design: CoverDesign; scope?: string };
      captured.presetPosts.push(body);
      const preset: CataloguePreset = {
        id: `user-${++userCounter}`, name: body.name, design: body.design,
        builtin: false, scope: body.scope === 'library' ? 'library' : 'user',
      };
      presets = [...presets, preset];
      await route.fulfill({ status: 201, json: { preset } });
      return;
    }
    await route.fulfill({ json: { presets } });
  });

  await page.route('**/cover-designer/style-thumb/*', (route) =>
    route.fulfill({ contentType: 'image/svg+xml', body: thumbSvg(route.request().url().split('/').pop() ?? '') }));
  await page.route('**/cover-designer/font-sample/*', (route) =>
    route.fulfill({ contentType: 'image/svg+xml', body: fontSvg(route.request().url().split('/').pop() ?? '') }));

  await page.route('**/cover/design-preview*', async (route) => {
    const body = (route.request().postDataJSON() ?? {}) as { design: CoverDesign };
    captured.previewBodies.push(body);
    await route.fulfill({
      json: { data_url: dataUrlFor(body.design ?? {}), renderer: 'pil', design: body.design ?? {} },
    });
  });

  await page.route('**/cover/apply', async (route) => {
    const body = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
    if (body.kind !== 'generated') { await route.fallback(); return; }
    captured.applyBodies.push(body);
    await route.fulfill({ json: { ok: true, cover_url: `/cover/${bookId}/og?ts=designed` } });
  });

  // Candidate fan-out is irrelevant here and slow; keep the page quiet.
  await page.route('**/cover/candidates*', (route) =>
    route.fulfill({ json: { candidates: [], providers: [], query: 'seed' } }));

  return captured;
}

async function firstBookId(page: Page): Promise<number | null> {
  await page.goto('/app/');
  return page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=1', { headers: { Accept: 'application/json' } })
      .then((x) => (x.ok ? x.json() : null)).catch(() => null);
    return r?.items?.[0]?.id ?? null;
  });
}

const designerPanel = (page: Page) => page.locator('details').filter({ hasText: 'Design a cover' }).first();
const lastPreview = (c: Captured) => c.previewBodies[c.previewBodies.length - 1]?.design;

test.describe('cover designer v2 (contract fixtures)', () => {
  test('presets: dropdown select, save-as-preset, delete, hide and restore a built-in', async ({ page }) => {
    const id = await firstBookId(page);
    test.skip(!id, 'seed has no books');
    const c = await installContractFixtures(page, id!);
    await page.goto(`/app/book/${id}/cover`);

    const panel = designerPanel(page);
    await panel.locator('summary').first().click();

    // The catalogue's default design resolves onto the Classic preset, and the
    // preview only renders once the panel is actually opened.
    const presetSelect = panel.getByRole('combobox', { name: 'Preset' });
    await expect(presetSelect).toContainText('Classic');
    await expect.poll(() => c.previewBodies.length).toBe(1);
    expect(lastPreview(c)).toMatchObject({ style: 'blocks', scheme: 'ink' });
    const preview = page.getByRole('img', { name: 'Preview of the designed cover' });
    await expect(preview).toBeVisible();

    // Selecting a preset loads its design; the next render carries it. The
    // dropdown is an APG listbox: trigger opens it, options are role=option.
    await presetSelect.click();
    await page.getByRole('option', { name: 'Ember' }).click();
    await expect.poll(() => lastPreview(c)?.scheme).toBe('ember');
    await expect(preview).toHaveAttribute('src', dataUrlFor(lastPreview(c)));

    // Diverging from Ember turns the dropdown into the honest custom state.
    await panel.getByRole('radio', { name: 'Ornamental' }).click();
    await expect.poll(() => lastPreview(c)?.style).toBe('ornamental');
    await expect(presetSelect).toHaveText('Custom (based on Ember)');

    // "Save as preset" beside "Use this design" posts the current design and
    // the new preset becomes the selected one.
    await panel.getByRole('button', { name: 'Save as preset' }).click();
    const saveDialog = page.getByRole('dialog', { name: 'Save as preset' });
    await saveDialog.getByLabel('Preset name').fill('My Cover Look');
    await saveDialog.getByRole('button', { name: 'Save preset' }).click();
    await expect.poll(() => c.presetPosts.length).toBe(1);
    expect(c.presetPosts[0].name).toBe('My Cover Look');
    expect(c.presetPosts[0].design).toMatchObject({ style: 'ornamental', scheme: 'ember' });
    await expect(panel.getByText('Preset saved.')).toBeVisible();
    await expect(presetSelect).toHaveText('My Cover Look');

    // Manage presets: rename the user preset, hide a built-in, restore it.
    await panel.getByRole('button', { name: 'Manage presets…' }).click();
    const manage = page.getByRole('dialog', { name: 'Manage presets' });
    const myRow = manage.locator('li').filter({ hasText: 'My Cover Look' });
    await myRow.getByRole('button', { name: 'Rename' }).click();
    await myRow.getByLabel('Preset name').fill('Renamed Look');
    await myRow.getByRole('button', { name: 'Save' }).click();
    await expect.poll(() => c.presetPuts.length).toBe(1);
    expect(c.presetPuts[0]).toEqual({ id: 'user-1', body: { name: 'Renamed Look' } });
    await expect(manage.locator('li').filter({ hasText: 'Renamed Look' })).toBeVisible();

    const meadowRow = manage.locator('li').filter({ hasText: 'Meadow' });
    await meadowRow.getByRole('button', { name: 'Hide' }).first().click();
    await meadowRow.getByRole('button', { name: 'Hide' }).last().click(); // inline confirm
    await expect.poll(() => c.presetDeletes).toContain('meadow');
    await expect(manage.locator('li').filter({ hasText: 'Meadow' })).toHaveCount(0);
    const hiddenRow = manage.locator('li').filter({ hasText: 'Meadow' });
    // The hidden built-ins section lists it again, restorable.
    await expect(manage.getByText('Hidden built-ins')).toBeVisible();
    await expect(hiddenRow).toHaveCount(1);
    await hiddenRow.getByRole('button', { name: 'Restore' }).click();
    await expect.poll(() => c.presetRestores).toContain('meadow');
    await expect(manage.locator('li').filter({ hasText: 'Meadow' }).getByRole('button', { name: 'Hide' })).toBeVisible();

    // Delete the user preset; the dropdown falls back to the plain custom state.
    const renamedRow = manage.locator('li').filter({ hasText: 'Renamed Look' });
    await renamedRow.getByRole('button', { name: 'Delete' }).first().click();
    await renamedRow.getByRole('button', { name: 'Delete' }).last().click();
    await expect.poll(() => c.presetDeletes).toContain('user-1');
    await page.keyboard.press('Escape');
    await expect(presetSelect).toHaveText('Custom');

    // Keyboard contract: the trigger opens the listbox and arrows move the
    // active option; Enter picks. Focus stays on the trigger throughout
    // (aria-activedescendant), which is what screen readers announce.
    await presetSelect.focus();
    await page.keyboard.press('ArrowDown');
    const listbox = page.getByRole('listbox', { name: 'Preset' });
    await expect(listbox).toBeVisible();
    await page.keyboard.press('ArrowDown');
    await expect(presetSelect).toHaveAttribute('aria-activedescendant', 'cd-preset-opt-1');
    await page.keyboard.press('Enter');
    await expect(presetSelect).toContainText('Classic');
    await expect.poll(() => lastPreview(c)?.style).toBe('blocks');
  });

  test('arrangement, custom colours, lettering and advanced fields all reach the preview body', async ({ page }) => {
    const id = await firstBookId(page);
    test.skip(!id, 'seed has no books');
    const c = await installContractFixtures(page, id!);
    await page.goto(`/app/book/${id}/cover`);

    const panel = designerPanel(page);
    await panel.locator('summary').first().click();
    await expect.poll(() => c.previewBodies.length).toBe(1);

    // Arrangement thumbnails: pick by thumbnail button, then drive the
    // radiogroup by keyboard (Arrows move + select, per APG).
    await panel.getByRole('radio', { name: 'Banner' }).click();
    await expect.poll(() => lastPreview(c)?.style).toBe('banner');
    await panel.getByRole('radio', { name: 'Banner' }).focus();
    await page.keyboard.press('ArrowRight');
    await expect.poll(() => lastPreview(c)?.style).toBe('ornamental');
    // The thumbnail strip shows server-rendered thumbs; the font card without a
    // sample_url renders its css_stack fallback instead of a broken image.
    await expect(panel.getByRole('radio', { name: 'Blocks' }).locator('img')).toBeVisible();
    await expect(panel.getByRole('radio', { name: 'Monospace' }).getByText('Aa')).toBeVisible();

    // Colour scheme swatches; the caption names the current scheme so the state
    // never depends on a hover tooltip.
    await panel.getByRole('radio', { name: 'Ember red' }).click();
    await expect.poll(() => lastPreview(c)?.scheme).toBe('ember');
    await expect(panel.getByText('Ember red', { exact: true })).toBeVisible();

    // The "+" swatch opens the custom-colour popover; a valid hex edit flips
    // the design to scheme:null and carries the colour into the preview body.
    await panel.getByRole('button', { name: 'Custom colours' }).click();
    const popover = page.getByRole('dialog', { name: 'Custom colours' });
    await popover.getByLabel('Background hex value').fill('#123456');
    await expect.poll(() => lastPreview(c)?.scheme).toBeNull();
    await expect.poll(() => lastPreview(c)?.colors?.background).toBe('#123456');
    await popover.getByRole('button', { name: 'Done' }).click();

    // Lettering cards set all three slots at once.
    await panel.getByRole('radio', { name: 'Sans-serif' }).click();
    await expect.poll(() => lastPreview(c)?.fonts?.title?.family).toBe('sans');
    expect(lastPreview(c)?.fonts?.author?.family).toBe('sans');

    // Advanced: per-slot size, alignment, text template and the 2:3-locked size.
    const advanced = panel.locator('details').filter({ hasText: 'Advanced' });
    await advanced.locator('summary').first().click();
    const titleSlot = advanced.locator('fieldset').filter({ has: page.locator(':scope > legend', { hasText: 'Title' }) });
    await titleSlot.getByLabel('Size (pt)').fill('90');
    await expect.poll(() => lastPreview(c)?.fonts?.title?.size).toBe(90);
    await titleSlot.getByRole('radio', { name: 'Left' }).click();
    await expect.poll(() => lastPreview(c)?.align?.title).toBe('left');

    const templates = advanced.locator('fieldset').filter({ has: page.locator(':scope > legend', { hasText: 'Text templates' }) });
    await templates.getByLabel('Title', { exact: true }).fill('{title} — director’s cut');
    await expect.poll(() => lastPreview(c)?.text?.title).toBe('{title} — director’s cut');

    const sizeBox = advanced.locator('fieldset').filter({ has: page.locator(':scope > legend', { hasText: 'Cover size' }) });
    await sizeBox.getByLabel('Width (px)').fill('1000');
    await expect.poll(() => lastPreview(c)?.size?.width).toBe(1000);
    expect(lastPreview(c)?.size?.height).toBe(1500); // 2:3 lock derived it

    // Apply sends the design object and nothing that could be pixels.
    await panel.getByRole('button', { name: 'Use this design' }).click();
    await expect.poll(() => c.applyBodies.length).toBe(1);
    const applied = c.applyBodies[0];
    expect(applied.kind).toBe('generated');
    const appliedDesign = applied.design as CoverDesign;
    expect(appliedDesign).toMatchObject({
      style: 'ornamental',
      scheme: null,
      align: { title: 'left' },
      size: { width: 1000, height: 1500 },
    });
    for (const value of Object.values(applied)) {
      expect(String(value)).not.toContain('data:');
    }
    await expect(page.getByRole('status').filter({ hasText: 'Cover updated.' })).toBeVisible();
  });

  test('a render failure is reported instead of leaving an empty frame', async ({ page }) => {
    const id = await firstBookId(page);
    test.skip(!id, 'seed has no books');
    await page.route('**/cover/candidates*', (route) =>
      route.fulfill({ json: { candidates: [], providers: [], query: 'seed' } }));
    await page.route(`**/book/${id}/cover/state*`, (route) => route.fulfill({
      json: {
        locked: false,
        ereader_enabled: false,
        ereader_defaults: { aspect: 'kobo_libra_color', fill_mode: 'edge_mirror', color: '' },
        designer: { available: true, renderer: 'pil', catalogue: CATALOGUE_V2 },
      },
    }));
    await page.route('**/cover/design-preview*', (route) => route.fulfill({
      status: 502,
      json: { error: 'render_failed', message: 'Could not design a cover for this book.' },
    }));

    await page.goto(`/app/book/${id}/cover`);
    const panel = designerPanel(page);
    await panel.locator('summary').first().click();
    await expect(panel.getByRole('alert')
      .filter({ hasText: 'Could not design a cover for this book.' })).toBeVisible();
    // A failed render must not leave an apply button armed over nothing.
    await expect(panel.getByRole('button', { name: 'Use this design' })).toBeDisabled();
  });
});
