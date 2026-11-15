import { test, expect } from './fixtures';

type Restriction = { Element: string; type: 'Allow' | 'Deny'; id: string };

test('classic Boolean restriction widget renders labels and writes canonical states', async ({ page }) => {
  const allowed: string[] = [];
  const denied: string[] = [];
  const writeLog: Array<{ action: string; value: string }> = [];
  const rows = (): Restriction[] => [
    ...denied.map((Element, index) => ({ Element, type: 'Deny' as const, id: `d${index}` })),
    ...allowed.map((Element, index) => ({ Element, type: 'Allow' as const, id: `a${index}` })),
  ];

  // Keep the real classic page and its Bootstrap/X-editable/table.js widgets,
  // while making this UI-only fixture independent of the test database's
  // configured custom columns. All restriction writes stay in this mock.
  await page.route('**/admin/viewconfig', async (route) => {
    const response = await route.fetch();
    const html = (await response.text()).replace(
      /data-bool-mode="(?:true|false)"/,
      'data-bool-mode="true"',
    );
    await route.fulfill({ response, body: html });
  });
  await page.route(/\/ajax\/(?:list|add|edit|delete)restriction\//, async (route) => {
    const url = new URL(route.request().url());
    const [, action, type] = url.pathname.match(/\/ajax\/(list|add|edit|delete)restriction\/(\d+)/) || [];
    const body = new URLSearchParams(route.request().postData() || '');
    if (action === 'list') {
      return route.fulfill({ json: Number(type) === 1 ? rows() : [] });
    }
    if (Number(type) !== 1) return route.fulfill({ json: [] });

    if (action === 'add') {
      const value = body.get('add_element') || '';
      const target = body.has('submit_allow') ? allowed : denied;
      const kind = target === allowed ? 'Allow' : 'Deny';
      if (value) target.push(value);
      writeLog.push({ action: `add-${kind}`, value });
    } else if (action === 'edit') {
      const id = body.get('id') || '';
      const value = body.get('Element') || '';
      const target = id.startsWith('a') ? allowed : denied;
      const index = Number(id.slice(1));
      if (Number.isInteger(index) && index >= 0 && index < target.length) target[index] = value;
      writeLog.push({ action: 'edit', value });
    } else if (action === 'delete') {
      const id = body.get('id') || '';
      const target = id.startsWith('a') ? allowed : denied;
      const index = Number(id.slice(1));
      if (Number.isInteger(index) && index >= 0 && index < target.length) target.splice(index, 1);
      writeLog.push({ action: 'delete', value: body.get('Element') || '' });
    }
    return route.fulfill({ status: 200, body: '' });
  });

  await page.goto('/admin/viewconfig');
  await page.locator('#get_column_values').click();
  await expect(page.locator('#add_element_bool')).toBeVisible();
  await expect(page.locator('#add_element')).toBeHidden();
  expect((await page.locator('#add_element_bool option').allTextContents()).map((value) => value.trim()))
    .toEqual(['Yes', 'No', 'Undefined']);

  for (const [state, label, button] of [
    ['true', 'Yes', '#submit_allow'],
    ['false', 'No', '#submit_restrict'],
    ['undefined', 'Undefined', '#submit_allow'],
  ] as const) {
    await page.locator('#add_element_bool').selectOption(state);
    await page.locator(button).click();
    await expect(page.locator('#restrict-elements-table')).toContainText(label);
    await expect.poll(() => writeLog[writeLog.length - 1]?.value).toBe(state);
    const anchor = page.locator(`#restrict-elements-table a[data-value="bool:${state}"]`);
    await expect(anchor).toContainText(label);
  }
  expect(allowed).toEqual(['true', 'undefined']);
  expect(denied).toEqual(['false']);

  // Edit false to true and back: the visible select uses widget-only string
  // keys, while requests and stored state must remain canonical tokens.
  await page.locator('#restrict-elements-table a[data-pk="d0"]').click();
  const editor = page.locator('.editable-container select');
  await expect(editor).toBeVisible();
  await editor.selectOption('bool:true');
  await page.locator('.editable-submit').click();
  await expect.poll(() => denied[0]).toBe('true');
  await expect.poll(() => writeLog[writeLog.length - 1]?.value).toBe('true');
  await expect(page.locator('#restrict-elements-table')).toContainText('Yes');

  // Delete Undefined, then re-open the tag picker and the bool picker. The
  // shared table must swap editable modes without retaining stale jQuery data.
  await page.locator('#restrict-elements-table [data-restriction-id="a1"]').click();
  await expect.poll(() => allowed).toEqual(['true']);
  expect(writeLog[writeLog.length - 1]).toEqual({ action: 'delete', value: 'undefined' });
  await page.locator('#restrict_close').click();
  await page.locator('#get_tags').click();
  await expect(page.locator('#add_element')).toBeVisible();
  await expect(page.locator('#add_element_bool')).toBeHidden();
  await page.locator('#restrict_close').click();
  await page.locator('#get_column_values').click();
  await expect(page.locator('#add_element_bool')).toBeVisible();
  await page.locator('#add_element_bool').selectOption('undefined');
  await page.locator('#submit_allow').click();
  await expect(page.locator('#restrict-elements-table')).toContainText('Undefined');
  await expect.poll(() => allowed).toEqual(['true', 'undefined']);
  expect(writeLog[writeLog.length - 1]).toEqual({ action: 'add-Allow', value: 'undefined' });
});
