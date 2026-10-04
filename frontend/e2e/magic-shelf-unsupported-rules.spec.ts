import { test, expect, type Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

const unknown = { id: 'custom_column_999999', operator: 'contains', value: '<missing & field>' };
const obsolete = { id: 'title', operator: 'retired_operator', value: ['first', 'second'] };
const incompatible = { id: 'title', operator: 'greater', value: 'Book' };
const nested = { condition: 'AND', rules: [
  { id: 'title', operator: 'contains', value: 'Book' },
  { condition: 'OR', rules: [unknown, obsolete, incompatible] },
  { condition: 'AND', rules: [8, 0, false, null, [1, 2], {future: ['value', null]}].map(value => ({...unknown, value})) },
] };

async function withShelf(page: Page, rules: unknown, run: (id: number) => Promise<void>) {
  const schema = await (await page.request.get('/api/v1/magicshelves/rule-schema')).json();
  expect(schema.fields.some((field: {id: string}) => field.id === unknown.id)).toBe(false);
  const headers = { 'X-CSRFToken': (await (await page.request.get('/api/v1/auth/csrf')).json()).csrf_token };
  const created = await page.request.post('/magicshelf', { headers,
    data: { name: `e2e-1617-${Date.now()}-${test.info().project.name}`, rules } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const id = (await created.json()).shelf_id;
  expect(id).toBeTruthy();
  try { await run(id); } finally {
    const removed = await page.request.post(`/magicshelf/${id}/delete`, { headers });
    expect(removed.ok(), await removed.text()).toBeTruthy();
  }
}

async function save(page: Page, id: number) {
  const response = page.waitForResponse(r => r.url().includes(`/magicshelf/${id}/edit`) && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  expect((await response).ok()).toBeTruthy();
  await expect(page).toHaveURL(new RegExp(`/app/magic/${id}$`));
  return (await (await page.request.get(`/api/v1/magicshelf/${id}?page=1`)).json()).rules;
}

test('#1617 name-only saves preserve every stored unsupported rule and nested condition', async ({page}) => {
  await withShelf(page, nested, async id => {
    await page.goto(`/app/magic/${id}/edit`);
    await page.getByRole('textbox', {name: 'Name', exact: true}).fill('Renamed with retained rules');
    expect(await save(page, id)).toEqual(nested);
  });
});

test('#1617 unavailable fields and unsupported operators stay visible and individually removable', async ({page}) => {
  await withShelf(page, nested, async id => {
    const errors = collectPageErrors(page);
    await page.goto(`/app/magic/${id}/edit`);
    const rows = page.getByRole('group', {name: 'Rule group', exact: true}).first().getByRole('group', {name: 'Unsupported rule', exact: true});
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toContainText(unknown.id);
    await expect(rows.nth(0)).toContainText(unknown.value);
    const rowBox = (await rows.nth(0).boundingBox())!;
    const removeBox = (await rows.nth(0).getByRole('button', {name: 'Remove rule', exact: true}).boundingBox())!;
    expect(removeBox.y - rowBox.y, 'removal stays beside the field heading even when details wrap').toBeLessThanOrEqual(24);
    await expect(rows.nth(1)).toContainText(obsolete.operator);
    await expect(rows.nth(1)).toContainText('first');
    await expect(rows.nth(1)).toContainText('second');
    await expect(rows.nth(2)).toContainText(incompatible.operator);
    await expect(rows.getByRole('combobox')).toHaveCount(0);
    await rows.nth(1).getByRole('button', {name: 'Remove rule', exact: true}).click();
    await expect(rows).toHaveCount(2);
    expect(await save(page, id)).toEqual({condition: 'AND', rules: [nested.rules[0], {condition: 'OR', rules: [unknown, incompatible]}, nested.rules[2]]});
    assertNoPageErrors(errors);
  });
});

test('#1617 the last unsupported rule can be removed and replaced with an editable rule', async ({page}) => {
  await withShelf(page, {condition: 'OR', rules: [{condition: 'AND', rules: [unknown]}]}, async id => {
    await page.goto(`/app/magic/${id}/edit`);
    const row = page.getByRole('group', {name: 'Unsupported rule', exact: true});
    await expect(row).toBeVisible();
    const remove = row.getByRole('button', {name: 'Remove rule', exact: true});
    await expect(remove).toBeEnabled();
    await remove.focus();
    await page.keyboard.press('Enter');
    await expect(row).toHaveCount(0);
    const field = page.getByRole('combobox', {name: 'Rule field', exact: true});
    await expect(field).toHaveCount(1);
    await expect(field).toHaveValue('title');
    await expect(page.getByRole('button', {name: 'Add rule', exact: true})).toBeFocused();
    await page.getByRole('textbox', {name: 'Title value', exact: true}).fill('Book');
    expect(await save(page, id)).toEqual({condition: 'OR', rules: [{id: 'title', operator: 'contains', value: 'Book'}]});
  });
});


test('#1617 a sole unsupported JSON object opens safely and survives a name-only save', async ({page}) => {
  const stored = {condition: 'AND', rules: [{...unknown, value: {toString: null}}]};
  await withShelf(page, stored, async id => {
    const errors = collectPageErrors(page);
    await page.goto(`/app/magic/${id}/edit`);
    const row = page.getByRole('group', {name: 'Unsupported rule', exact: true});
    await expect(row).toBeVisible();
    await expect(row).toContainText('{"toString":null}');
    await page.getByRole('textbox', {name: 'Name', exact: true}).fill('Retained JSON rule');
    expect(await save(page, id)).toEqual(stored);
    assertNoPageErrors(errors);
  });
});
