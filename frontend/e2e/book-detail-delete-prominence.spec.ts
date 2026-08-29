import { expect, Page, test } from '@playwright/test';

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

async function setDeletePermission(page: Page, allowed: boolean) {
  const response = await page.context().request.get(new URL('/api/v1/auth/me', page.url()).href);
  const status = response.status();
  const headers = response.headers();
  const me = await response.json();
  await response.dispose();
  me.role = { ...(me.role ?? {}), delete_books: allowed };

  await page.route('**/api/v1/auth/me', async (route) => {
    await route.fulfill({ status, headers, json: me });
  });
}

test('book-detail deletion remains visible and accessible with delete permission (#1862)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(book == null, 'seed has no books');

  await setDeletePermission(page, true);
  await page.goto(`/app/book/${book.id}`, { waitUntil: 'domcontentloaded' });

  // #1939's disambiguating wording remains load-bearing, but the redundant
  // heading row does not: the region and the control now carry the wording.
  const region = page.getByTestId('book-destructive-actions');
  await expect(region).toBeVisible();
  await expect(region).toHaveAccessibleName('Delete from the global library');
  await expect(region).toHaveAttribute('aria-label', 'Delete from the global library');
  await expect(region.getByRole('heading')).toHaveCount(0);
  const deleteButton = region.getByRole('button', { name: 'Delete from the global library' });
  await expect(deleteButton).toHaveCount(1);
  await expect(deleteButton).toBeVisible();
  await expect(deleteButton).toHaveText('Delete from the global library');
});

test('book-detail deletion remains absent without delete permission (#1862)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(book == null, 'seed has no books');

  await setDeletePermission(page, false);
  await page.goto(`/app/book/${book.id}`, { waitUntil: 'domcontentloaded' });

  await expect(page.getByTestId('book-destructive-actions')).toHaveCount(0);
  // #1939 renamed the book-detail destructive control's accessible name. This
  // absence assertion MUST track the rename: against the old name it would now
  // pass whether or not the control is hidden, i.e. prove nothing.
  await expect(page.getByRole('button', { name: 'Delete from the global library' })).toHaveCount(0);
});

test('dismissing book-detail deletion confirmation never calls the endpoint (#1862)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(book == null, 'seed has no books');

  await setDeletePermission(page, true);
  let deleteCalls = 0;
  await page.route(`**/api/v1/books/${book.id}/delete`, async (route) => {
    deleteCalls += 1;
    await route.fulfill({ status: 204, contentType: 'application/json', body: '' });
  });

  await page.goto(`/app/book/${book.id}`, { waitUntil: 'domcontentloaded' });
  const deleteButton = page.getByTestId('book-destructive-actions')
    .getByRole('button', { name: 'Delete from the global library' });
  await expect(deleteButton).toBeVisible();

  let declinedPrompt = '';
  page.once('dialog', (dialog) => {
    declinedPrompt = dialog.message();
    void dialog.dismiss();
  });
  await deleteButton.click();
  await page.waitForTimeout(500);

  expect(declinedPrompt).toContain(`"${book.title}"`);
  expect(declinedPrompt).toContain('cannot be undone');
  expect(deleteCalls, 'declining confirmation must not call the delete endpoint').toBe(0);
  await expect(page).toHaveURL(new RegExp(`/book/${book.id}\\b`));
});

test('book-detail deletion is a quiet region in light and dark themes (#1862)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(book == null, 'seed has no books');

  await setDeletePermission(page, true);
  await page.goto(`/app/book/${book.id}`, { waitUntil: 'domcontentloaded' });

  const region = page.getByTestId('book-destructive-actions');
  for (const theme of ['light', 'dark']) {
    await page.locator('html').evaluate((html, value) => html.setAttribute('data-theme', value), theme);
    const appearance = await region.evaluate((element) => {
      const style = getComputedStyle(element);
      const button = element.querySelector('button');
      if (!(button instanceof HTMLElement)) {
        throw new Error('destructive region must retain its delete control');
      }

      const dangerProbe = document.createElement('span');
      dangerProbe.style.cssText = 'position:absolute;visibility:hidden;color:var(--danger)';
      element.append(dangerProbe);
      const dangerColor = getComputedStyle(dangerProbe).color;
      dangerProbe.remove();

      const buttonStyle = getComputedStyle(button);
      return {
        backgroundColor: style.backgroundColor,
        borderLeftStyle: style.borderLeftStyle,
        borderRightStyle: style.borderRightStyle,
        borderBottomStyle: style.borderBottomStyle,
        borderTopStyle: style.borderTopStyle,
        borderTopWidth: style.borderTopWidth,
        borderTopColor: style.borderTopColor,
        outlineStyle: style.outlineStyle,
        boxShadow: style.boxShadow,
        dangerColor,
        buttonColor: buttonStyle.color,
        buttonBorderColor: buttonStyle.borderColor,
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
    expect.soft({
      foreground: appearance.buttonColor,
      border: appearance.buttonBorderColor,
    }, `${theme} theme must retain danger emphasis on the delete button`).toEqual({
      foreground: appearance.dangerColor,
      border: appearance.dangerColor,
    });
    expect.soft(appearance, `${theme} theme must not render a filled, boxed danger banner`).toMatchObject({
      backgroundColor: 'rgba(0, 0, 0, 0)',
      borderLeftStyle: 'none',
      borderRightStyle: 'none',
      borderBottomStyle: 'none',
      outlineStyle: 'none',
      boxShadow: 'none',
    });
  }
});
