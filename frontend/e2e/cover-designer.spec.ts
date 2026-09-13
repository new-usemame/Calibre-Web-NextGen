import { test, expect, type Page } from '@playwright/test';
import {
  CATALOGUE_V2, advancedDetails, dataUrlFor, designerPanel, firstBookId, installContractFixtures,
  lastPreview,
} from './fixtures/cover-designer-v2';
import type { CoverDesign } from '../src/features/coverDesigner/contract';

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
 */

/** A slot's Advanced fieldset, scoped by its own legend. Exact regex: a plain
 *  'Title' hasText would also match the 'Subtitle' legend. */
const slotFieldset = (page: Page, slot: 'Title' | 'Subtitle' | 'Author') =>
  advancedDetails(page).locator('fieldset')
    .filter({ has: page.locator(':scope > legend', { hasText: new RegExp(`^${slot}$`) }) });

const sectionFieldset = (page: Page, legend: string) =>
  advancedDetails(page).locator('fieldset')
    .filter({ has: page.locator(':scope > legend', { hasText: new RegExp(`^${legend}$`) }) });

test.describe('cover designer v2 (contract fixtures)', () => {
  test('presets: dropdown select, save-as-preset, rename, delete, hide and restore a built-in', async ({ page }) => {
    const id = await firstBookId(page);
    test.skip(!id, 'seed has no books');
    const c = await installContractFixtures(page, id!);
    await page.goto(`/app/book/${id}/cover`);

    const panel = designerPanel(page);
    await panel.locator(':scope > summary').first().click();

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
    await manage.locator('li').filter({ hasText: 'My Cover Look' })
      .getByRole('button', { name: 'Rename' }).click();
    // Rename replaces the row's name text with the input, so re-scope by label.
    await manage.getByLabel('Preset name').fill('Renamed Look');
    await manage.getByRole('button', { name: 'Save' }).click();
    await expect.poll(() => c.presetPuts.length).toBe(1);
    expect(c.presetPuts[0]).toEqual({ id: 'user-1', body: { name: 'Renamed Look' } });
    await expect(manage.locator('li').filter({ hasText: 'Renamed Look' })).toBeVisible();

    const meadowRow = () => manage.locator('li').filter({ hasText: 'Meadow' });
    await meadowRow().getByRole('button', { name: 'Hide' }).click();
    await meadowRow().getByRole('button', { name: 'Hide' }).click(); // inline confirm
    await expect.poll(() => c.presetDeletes).toContain('meadow');
    await expect(manage.getByText('Hidden built-ins')).toBeVisible();
    await expect(meadowRow().getByRole('button', { name: 'Restore' })).toBeVisible();
    await meadowRow().getByRole('button', { name: 'Restore' }).click();
    await expect.poll(() => c.presetRestores).toContain('meadow');
    await expect(meadowRow().getByRole('button', { name: 'Hide' })).toBeVisible();

    // Delete the user preset; the dropdown falls back to the plain custom state.
    const renamedRow = () => manage.locator('li').filter({ hasText: 'Renamed Look' });
    await renamedRow().getByRole('button', { name: 'Delete' }).click();
    await renamedRow().getByRole('button', { name: 'Delete' }).click(); // inline confirm
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
    await panel.locator(':scope > summary').first().click();
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
    await advancedDetails(page).locator(':scope > summary').first().click();
    await slotFieldset(page, 'Title').getByLabel('Size (pt)').fill('90');
    await expect.poll(() => lastPreview(c)?.fonts?.title?.size).toBe(90);
    await slotFieldset(page, 'Title').getByRole('radio', { name: 'Left' }).click();
    await expect.poll(() => lastPreview(c)?.align?.title).toBe('left');

    await sectionFieldset(page, 'Text templates')
      .getByLabel('Title', { exact: true }).fill('{title} — director’s cut');
    await expect.poll(() => lastPreview(c)?.text?.title).toBe('{title} — director’s cut');

    await sectionFieldset(page, 'Cover size').getByLabel('Width (px)').fill('1000');
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
    await panel.locator(':scope > summary').first().click();
    await expect(panel.getByRole('alert')
      .filter({ hasText: 'Could not design a cover for this book.' })).toBeVisible();
    // A failed render must not leave an apply button armed over nothing.
    await expect(panel.getByRole('button', { name: 'Use this design' })).toBeDisabled();
  });
});
