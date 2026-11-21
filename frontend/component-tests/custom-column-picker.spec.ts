import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const repo = path.resolve(import.meta.dirname, '../..');
const values = ['Alpha', 'Beta', 'QA. 76..', 'Smith, Jr.', '__proto__', 'constructor',
  'Quote" autofocus onfocus="window.pickerInjected=true'];

async function mount(page: Page, classic: boolean, multiple: boolean, value = '', failFirst = false) {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  let attempts = 0;
  await page.route('**/ajax/get_cc_tree/*', route => route.fulfill({ status: failFirst && attempts++ === 0 ? 503 : 200, json: {
    hierarchical: false, is_multiple: multiple, entries: values.map(value => ({ value, depth: 0 })),
  } }));
  // Personal browse visibility must not hide a column's editor choices.
  await page.route('**/api/v1/columns/*/tree', route => route.fulfill({ status: 404, json: {} }));
  let body: string;
  if (classic) {
    const modal = fs.readFileSync(path.join(repo, 'cps/templates/custom_column_modal.html'), 'utf8')
      .replace(/\{#[\s\S]*?#\}/g, '').replace(/\{\{ _\('([^']*)'\) \}\}/g, '$1');
    body = `<button class="btn-cc-picker" data-column-id="7" data-column-name="Shelf"
      data-is-multiple="${multiple ? 'true' : 'false'}">Open</button>
      <input id="custom_column_7"><input id="result">${modal}`;
  } else {
    body = `<div id="root"></div><script type="module">
      import RefreshRuntime from '/static/app/@react-refresh';
      RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type;
      window.__vite_plugin_react_preamble_installed__ = true;
      window.pickerTest = ${JSON.stringify({value, multiple})};
      await import('/static/app/component-tests/picker-harness.tsx');
    </script>`;
  }
  await page.route('**/picker-harness', route => route.fulfill({ contentType: 'text/html',
    body: `<html><body><script>window.__CWNG_PREFIX__='';</script>${body}</body></html>` }));
  await page.goto('/picker-harness');
  if (classic) {
    await page.addStyleTag({ content: '.modal {display:none} .modal.in {display:block}' });
    await page.addScriptTag({ path: path.join(repo, 'cps/static/js/libs/jquery.min.js') });
    await page.addScriptTag({ path: path.join(repo, 'cps/static/js/libs/bootstrap.min.js') });
    await page.evaluate(() => { (window as any).getPath = () => ''; });
    await page.locator('#custom_column_7').fill(value);
    await page.addScriptTag({ path: path.join(repo, 'cps/static/js/get_cc_tree.js') });
    await page.getByRole('button', { name: 'Open', exact: true }).click();
  }
  if (failFirst) await expect(page.getByRole('button', { name: 'Try again', exact: true })).toBeVisible();
  else await expect(page.getByLabel('Alpha', { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
}

for (const classic of [false, true]) {
  test.describe(classic ? 'Classic picker' : 'New UI picker', () => {
    const result = (page: Page) => page.locator(classic ? '#custom_column_7' : '#result');
    test('filtering preserves selections and literal prototype/quote values stay data', async ({ page }) => {
      await mount(page, classic, true);
      await expect(page.getByLabel('constructor', { exact: true })).not.toBeChecked();
      await page.getByLabel('Alpha', { exact: true }).check();
      await page.getByLabel('Search or filter values', { exact: true }).fill('Beta');
      await page.getByLabel('Beta', { exact: true }).check();
      await page.getByLabel('Search or filter values', { exact: true }).fill('');
      await expect(page.getByLabel('Alpha', { exact: true })).toBeChecked();
      await page.getByLabel('__proto__', { exact: true }).check();
      const quoted = page.getByLabel(values[6], { exact: true });
      if (classic) await expect(quoted).toHaveValue(values[6]);
      await quoted.check();
      await page.getByRole('button', { name: 'Apply selection', exact: true }).click();
      expect((await result(page).inputValue()).split(', ')).toEqual(['Alpha', 'Beta', '__proto__', values[6]]);
      expect(await page.evaluate(() => (window as any).pickerInjected)).toBeUndefined();
    });

    test('a single-value column replaces its value and preserves commas and dots', async ({ page }) => {
      await mount(page, classic, false, 'Smith, Jr.');
      await expect(page.getByLabel('Smith, Jr.', { exact: true })).toBeChecked();
      await page.getByLabel('QA. 76..', { exact: true }).check();
      await expect(page.getByLabel('Smith, Jr.', { exact: true })).not.toBeChecked();
      await page.getByRole('button', { name: 'Apply selection', exact: true }).click();
      await expect(result(page)).toHaveValue('QA. 76..');
    });

    test('adding a flat value preserves spelling and cancel leaves the field alone', async ({ page }) => {
      await mount(page, classic, false, 'Alpha');
      await page.getByLabel('New value', { exact: true }).fill('QA. 76..');
      await page.getByRole('button', { name: 'Add', exact: true }).click();
      await expect(page.getByLabel('QA. 76..', { exact: true })).toBeChecked();
      await page.getByRole('button', { name: 'Cancel', exact: true }).click();
      await expect(result(page)).toHaveValue('Alpha');
    });

    test('failed choices cannot be applied and retry restores the picker', async ({ page }) => {
      await mount(page, classic, false, 'Alpha', true);
      await expect(page.getByRole('button', { name: 'Apply selection', exact: true })).toBeDisabled();
      await expect(result(page)).toHaveValue('Alpha');
      await page.getByRole('button', { name: 'Try again', exact: true }).click();
      await expect(page.getByLabel('Alpha', { exact: true })).toBeChecked();
      await page.getByLabel('Alpha', { exact: true }).uncheck();
      await page.getByRole('button', { name: 'Apply selection', exact: true }).click();
      await expect(result(page)).toHaveValue('');
    });
  });
}
