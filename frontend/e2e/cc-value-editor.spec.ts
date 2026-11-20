import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

async function editor(page: Page, edit = true) {
  const writes: { action: string; payload: unknown }[] = [];
  let fail = false;
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/auth/me')) {
      await route.fulfill({ json: { id: 1, name: 'Editor', locale: 'en', theme: 'light', role: { edit }, sidebar: {} } });
    } else if (url.pathname.endsWith('/auth/csrf')) {
      await route.fulfill({ json: { csrf_token: 'test' } });
    } else if (url.pathname.endsWith('/77/tree')) {
      await route.fulfill({ json: {
        column: { id: 77, name: 'Dewey', datatype: 'text', hierarchical: false },
        nodes: ['778.3', '778.72'].map((name, i) => ({ name, path: name, count: 0, total_count: 0, children: [],
          available: { can_delete: true, can_merge_source: true, can_move_up: i > 0, can_move_down: i === 0 } })),
      } });
    } else if (url.pathname.includes('/values/')) {
      writes.push({ action: url.pathname.split('/').pop()!, payload: route.request().postDataJSON() });
      await route.fulfill(fail ? { status: 400, json: { error: { code: 'invalid_request', message: 'Refused' } } }
        : { json: { books: 0 } });
    } else {
      await route.fulfill({ json: { items: [], nodes: [], total: 0, page: 1, per_page: 24, column: { id: 77, name: 'Dewey' }, path: '' } });
    }
  });
  await page.goto('/app/cc/77');
  await expect(page.getByRole('heading', { name: 'Dewey', exact: true })).toBeVisible();
  return { writes, failWrites: () => { fail = true; } };
}

for (const width of [1280, 390]) {
  test(`flat values keep dots when created beside a sibling (${width}px)`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await editor(page);
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    const accessibility = await new AxeBuilder({ page }).include('#main').analyze();
    expect(accessibility.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);
    await page.getByRole('button', { name: 'Create a value just below 778.3', exact: true }).click();
    await page.getByLabel('New value', { exact: true }).fill('823.92');
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect.poll(() => state.writes).toEqual([{ action: 'create', payload: { path: '823.92', after: '778.3' } }]);
  });
}

test('flat values expose reorder controls, no cut, and display refusals beside the rows', async ({ page }) => {
  const state = await editor(page);
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cut 778.3', exact: true })).toHaveCount(0);
  await page.getByRole('checkbox', { name: /778\.3/ }).check();
  state.failWrites();
  await page.getByRole('button', { name: 'Move 778.3 down one place', exact: true }).click();
  await expect.poll(() => state.writes).toEqual([{ action: 'reorder', payload: { path: '778.3', direction: 'down' } }]);
  await expect(page.getByRole('alert').filter({ hasText: 'That name cannot be used' })).toBeVisible();
});

test('readers without metadata permission cannot enter the value editor', async ({ page }) => {
  await editor(page, false);
  await expect(page.getByRole('button', { name: 'Edit', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Create a value/ })).toHaveCount(0);
});


test('Classic preserves numeric-looking value paths in requests', async ({ page }) => {
  // Exercise the real jQuery + editor script: jQuery.data() turns "778.3"
  // into a number, whereas the API requires the original string spelling.
  const writes: unknown[] = [];
  const strings = {
    editValues: 'Edit values', done: 'Done',
    createBesidePrompt: 'New value',
    moveDownValue: 'Move {name} down one place',
    cutValue: 'Cut {name}',
  };
  await page.route('**/classic-column-test', route => route.fulfill({ contentType: 'text/html', body: `
    <!doctype html><html lang="en"><body>
    <input name="csrf_token" value="classic-test-token">
    <div class="cc-values-toolbar"><span class="cc-values-toolbar-left"></span>
      <button class="cc-values-toggle btn-cc-values-edit">Edit values</button></div>
    <div class="cc-values-browse">Browse values</div>
    <section id="ccValuesEditor" data-col-id="77" data-hierarchical="0" data-strings='${JSON.stringify(strings)}' hidden>
      <div id="ccValuesError" role="alert" hidden></div><div id="ccValuesBanner" hidden></div>
      <ul><li class="cc-values-row" data-path="778.3" data-depth="0" data-can-delete="1"
        data-can-merge-source="1" data-can-move-up="0" data-can-move-down="1">
        <label><input type="checkbox" class="cc-values-tick"><span class="cc-values-name">778.3</span></label>
        <span class="cc-values-actions"></span>
        <button class="cc-values-create-under" data-path="778.3">Create beside</button>
      </li></ul>
    </section>
    <script src="/static/js/libs/jquery.min.js"></script>
    <script>window.getPath = function() { return ''; };</script>
    <script src="/static/js/cc_value_editor.js"></script>
    </body></html>` }));
  await page.route('**/api/v1/columns/77/values/*', async route => {
    writes.push({ action: route.request().url().split('/').pop(), payload: route.request().postDataJSON(),
      csrf: route.request().headers()['x-csrftoken'] });
    await route.fulfill({ status: 400, json: { error: { code: 'invalid_request', message: 'Refused' } } });
  });
  await page.goto('/classic-column-test');
  await page.getByRole('button', { name: 'Edit values', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cut 778.3', exact: true })).toHaveCount(0);
  page.once('dialog', dialog => dialog.accept('823.92'));
  await page.getByRole('button', { name: 'Create beside', exact: true }).click();
  await expect.poll(() => writes).toEqual([{ action: 'create', payload: { path: '823.92', after: '778.3' }, csrf: 'classic-test-token' }]);
  await page.getByRole('checkbox', { name: '778.3', exact: true }).check();
  await page.getByRole('button', { name: 'Move 778.3 down one place', exact: true }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1]).toEqual({ action: 'reorder', payload: { path: '778.3', direction: 'down' }, csrf: 'classic-test-token' });
});
