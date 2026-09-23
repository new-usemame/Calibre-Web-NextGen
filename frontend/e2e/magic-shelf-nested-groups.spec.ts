import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * #2257 — a smart shelf whose rules contain a nested group (the classic
 * builder's "Add group") crashed the New UI editor with
 * `can't access property "includes", G.operator is undefined`: the editor
 * flattened the rule set and read the group node as a rule. The server has
 * always evaluated groups recursively, so the editor must load, edit and save
 * them unchanged. The payload below is exactly what the classic form posts.
 */

async function csrfToken(page: Page): Promise<string> {
  const res = await page.request.get('/api/v1/auth/csrf');
  return ((await res.json()) as { csrf_token: string }).csrf_token;
}

const classicRules = {
  condition: 'AND',
  rules: [
    { id: 'tag', field: 'tag', type: 'string', input: 'text', operator: 'not_contains', value: 'e2e-2257-never' },
    {
      condition: 'OR',
      rules: [
        { id: 'tag', field: 'tag', type: 'string', input: 'text', operator: 'contains', value: 'e2e-2257-a' },
        { id: 'tag', field: 'tag', type: 'string', input: 'text', operator: 'contains', value: 'e2e-2257-b' },
      ],
    },
  ],
  valid: true,
};

test('#2257 a smart shelf with a nested rule group opens, edits and saves in the New UI', async ({ page }) => {
  const headers = { 'X-CSRFToken': await csrfToken(page) };
  const created = await page.request.post('/magicshelf', {
    headers,
    data: { name: `e2e-2257-${Date.now()}`, icon: '🪄', rules: classicRules },
  });
  expect(created.ok(), 'magic shelf create should succeed').toBeTruthy();
  const shelfId = ((await created.json()) as { shelf_id?: number }).shelf_id;
  expect(shelfId, 'magic shelf create should return an id').toBeTruthy();

  try {
    const errors = collectPageErrors(page);
    await page.goto(`/app/magic/${shelfId}/edit`);

    const group = page.getByRole('group', { name: 'Rule group' });
    await expect(group).toBeVisible();
    await expect(group.getByRole('combobox', { name: 'Match condition' })).toHaveValue('OR');
    const nestedValue = group.getByRole('textbox').nth(1);
    await expect(nestedValue).toHaveValue('e2e-2257-b');
    await nestedValue.fill('e2e-2257-c');

    const saved = page.waitForResponse((response) =>
      response.url().includes(`/magicshelf/${shelfId}/edit`) && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Save changes' }).click();
    expect((await saved).ok()).toBeTruthy();
    await expect(page).toHaveURL(new RegExp(`/app/magic/${shelfId}$`));

    const stored = await page.request.get(`/api/v1/magicshelf/${shelfId}?page=1`);
    const rules = ((await stored.json()) as { rules: unknown }).rules;
    expect(rules).toEqual({
      condition: 'AND',
      rules: [
        { id: 'tag', operator: 'not_contains', value: 'e2e-2257-never' },
        {
          condition: 'OR',
          rules: [
            { id: 'tag', operator: 'contains', value: 'e2e-2257-a' },
            { id: 'tag', operator: 'contains', value: 'e2e-2257-c' },
          ],
        },
      ],
    });
    assertNoPageErrors(errors);
  } finally {
    await page.request.post(`/magicshelf/${shelfId}/delete`, { headers }).catch(() => {});
  }
});
