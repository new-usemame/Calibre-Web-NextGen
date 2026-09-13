import { expect, Page, test } from '@playwright/test';

/*
 * #1862/#1828 — where whole-book deletion lives on the book page.
 *
 * The action row carries only its four visible controls; every other action is
 * in the "More actions" gear menu, with deletion at the foot in a separated,
 * danger-styled section labelled "Admin only". The menu is the same DOM at
 * every viewport width — the old desktop-only bordered region and the narrow
 * layout's icon-level control are both gone — so every test here runs against
 * both the desktop and the mobile project with no layout branching.
 *
 * Seed-resilient: each test probes the API for a book and skips when absent.
 * Role gating is made deterministic by a fetch-then-modify stub of /auth/me.
 */

interface SeedBook {
  id: number;
  title: string;
}

async function firstBook(page: Page): Promise<SeedBook | null> {
  return page.evaluate(async () => {
    const response = await fetch('/api/v1/books?per_page=1', {
      headers: { Accept: 'application/json' },
    }).catch(() => null);
    if (!response?.ok) return null;
    const book = (await response.json())?.items?.[0];
    return book ? { id: book.id, title: book.title } : null;
  });
}

// The book-page delete section is gated on the ADMIN role (operator
// instruction); the server keeps its own delete+edit check.
async function setAdminPermission(page: Page, allowed: boolean) {
  const response = await page.context().request.get(new URL('/api/v1/auth/me', page.url()).href);
  const status = response.status();
  const headers = response.headers();
  const me = await response.json();
  await response.dispose();
  me.role = { ...(me.role ?? {}), admin: allowed };

  await page.route('**/api/v1/auth/me', async (route) => {
    await route.fulfill({ status, headers, json: me });
  });
}

async function openActionsMenu(page: Page) {
  const trigger = page.getByTestId('book-actions-menu');
  await expect(trigger).toBeVisible({ timeout: 10_000 });
  await trigger.click();
  const menu = page.getByTestId('book-actions-menu-list');
  await expect(menu).toBeVisible();
  return menu;
}

test('book-detail deletion is a menuitem in an admin-only menu section, at every width (#1862)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  if (book == null) {
    test.skip(true, 'seed has no books');
    return;
  }

  await setAdminPermission(page, true);
  await page.goto(`/app/book/${book.id}`, { waitUntil: 'domcontentloaded' });

  // Deletion is never one of the visible row controls — only the gear menu
  // carries it, on desktop and mobile alike.
  await expect(
    page.getByTestId('book-actions').getByRole('button', { name: 'Delete from the global library' }),
  ).toHaveCount(0);

  const menu = await openActionsMenu(page);
  // #1939's disambiguating wording remains load-bearing. The section label
  // names who the control is for, and the item is the section's only member.
  const adminSection = menu.getByRole('group', { name: 'Admin only' });
  await expect(adminSection).toBeVisible();
  const deleteItem = adminSection.getByRole('menuitem', { name: 'Delete from the global library' });
  await expect(deleteItem).toHaveCount(1);
  await expect(deleteItem).toBeVisible();
  await expect(deleteItem).toHaveText('Delete from the global library');

  // No control in the open menu may render with an empty accessible name.
  for (const item of await menu.getByRole('menuitem').all()) {
    await expect(item).toHaveAccessibleName(/\S/);
  }
});

test('book-detail deletion remains absent for a non-admin (#1862)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  if (book == null) {
    test.skip(true, 'seed has no books');
    return;
  }

  await setAdminPermission(page, false);
  await page.goto(`/app/book/${book.id}`, { waitUntil: 'domcontentloaded' });

  const menu = await openActionsMenu(page);
  await expect(menu.getByRole('menuitem', { name: /Mark as (read|unread)/ })).toBeVisible();
  // #1939 renamed the book-detail destructive control's accessible name. This
  // absence assertion MUST track the rename: against the old name it would now
  // pass whether or not the control is hidden, i.e. prove nothing.
  await expect(menu.getByRole('menuitem', { name: 'Delete from the global library' })).toHaveCount(0);
  await expect(menu.getByText('Admin only')).toHaveCount(0);
});

test('dismissing book-detail deletion confirmation never calls the endpoint (#1862)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  if (book == null) {
    test.skip(true, 'seed has no books');
    return;
  }

  await setAdminPermission(page, true);
  let deleteCalls = 0;
  await page.route(`**/api/v1/books/${book.id}/delete`, async (route) => {
    deleteCalls += 1;
    await route.fulfill({ status: 204, contentType: 'application/json', body: '' });
  });

  await page.goto(`/app/book/${book.id}`, { waitUntil: 'domcontentloaded' });
  const menu = await openActionsMenu(page);
  const deleteItem = menu.getByRole('menuitem', { name: 'Delete from the global library' });
  await expect(deleteItem).toBeVisible();

  let declinedPrompt = '';
  page.once('dialog', (dialog) => {
    declinedPrompt = dialog.message();
    void dialog.dismiss();
  });
  await deleteItem.click();
  await page.waitForTimeout(500);

  expect(declinedPrompt).toContain(`"${book.title}"`);
  expect(declinedPrompt).toContain('cannot be undone');
  expect(deleteCalls, 'declining confirmation must not call the delete endpoint').toBe(0);
  await expect(page).toHaveURL(new RegExp(`/book/${book.id}\\b`));
});

test('the admin-only section stays a quiet divider with a danger menuitem in light and dark themes (#1862)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  if (book == null) {
    test.skip(true, 'seed has no books');
    return;
  }

  await setAdminPermission(page, true);
  await page.goto(`/app/book/${book.id}`, { waitUntil: 'domcontentloaded' });
  const menu = await openActionsMenu(page);
  const adminSection = menu.getByRole('group', { name: 'Admin only' });

  for (const theme of ['light', 'dark']) {
    await page.locator('html').evaluate((html, value) => html.setAttribute('data-theme', value), theme);
    const appearance = await adminSection.evaluate((element) => {
      const style = getComputedStyle(element);
      const item = element.querySelector('[role="menuitem"]');
      if (!(item instanceof HTMLElement)) {
        throw new Error('admin-only section must retain its delete menuitem');
      }

      const dangerProbe = document.createElement('span');
      dangerProbe.style.cssText = 'position:absolute;visibility:hidden;color:var(--danger)';
      element.append(dangerProbe);
      const dangerColor = getComputedStyle(dangerProbe).color;
      dangerProbe.remove();

      const itemStyle = getComputedStyle(item);
      return {
        backgroundColor: style.backgroundColor,
        borderTopStyle: style.borderTopStyle,
        borderTopWidth: style.borderTopWidth,
        borderTopColor: style.borderTopColor,
        outlineStyle: style.outlineStyle,
        boxShadow: style.boxShadow,
        dangerColor,
        itemColor: itemStyle.color,
      };
    });

    expect.soft({
      hasTopDivider: appearance.borderTopStyle !== 'none'
        && Number.parseFloat(appearance.borderTopWidth) > 0,
      usesDangerColor: appearance.borderTopColor === appearance.dangerColor,
    }, `${theme} theme must retain a neutral top divider`).toEqual({
      hasTopDivider: true,
      usesDangerColor: false,
    });
    expect.soft(
      appearance.itemColor,
      `${theme} theme must retain danger emphasis on the delete menuitem`,
    ).toBe(appearance.dangerColor);
    expect.soft(appearance, `${theme} theme must not render a filled, boxed danger banner`).toMatchObject({
      backgroundColor: 'rgba(0, 0, 0, 0)',
      outlineStyle: 'none',
      boxShadow: 'none',
    });
  }
});
