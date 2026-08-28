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

  const region = page.getByTestId('book-destructive-actions');
  await expect(region).toBeVisible();
  await expect(region).toHaveAccessibleName('Delete book');
  await expect(region.getByRole('heading', { name: 'Delete book' })).toBeVisible();
  await expect(region.getByRole('button', { name: 'Delete book' })).toBeVisible();
});

test('book-detail deletion remains absent without delete permission (#1862)', async ({ page }) => {
  await page.goto('/app');
  const book = await firstBook(page);
  test.skip(book == null, 'seed has no books');

  await setDeletePermission(page, false);
  await page.goto(`/app/book/${book.id}`, { waitUntil: 'domcontentloaded' });

  await expect(page.getByTestId('book-destructive-actions')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Delete book' })).toHaveCount(0);
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
    .getByRole('button', { name: 'Delete book' });
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
      const label = element.querySelector('h2');
      const button = element.querySelector('button');
      const pageTitle = document.querySelector('h1');
      if (!(label instanceof HTMLElement)
        || !(button instanceof HTMLElement)
        || !(pageTitle instanceof HTMLElement)) {
        throw new Error('destructive region and page title must retain their semantic structure');
      }

      const dangerProbe = document.createElement('span');
      dangerProbe.style.cssText = 'position:absolute;visibility:hidden;color:var(--danger)';
      element.append(dangerProbe);
      const dangerColor = getComputedStyle(dangerProbe).color;
      dangerProbe.remove();

      const labelStyle = getComputedStyle(label);
      const buttonStyle = getComputedStyle(button);
      const pageTitleStyle = getComputedStyle(pageTitle);
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
        labelColor: labelStyle.color,
        labelFontSize: labelStyle.fontSize,
        labelFontWeight: labelStyle.fontWeight,
        pageTitleFontSize: pageTitleStyle.fontSize,
        buttonColor: buttonStyle.color,
        buttonBorderColor: buttonStyle.borderColor,
        buttonFontWeight: buttonStyle.fontWeight,
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
      isPageTitleSized: Number.parseFloat(appearance.labelFontSize)
        >= Number.parseFloat(appearance.pageTitleFontSize),
      isAtLeastButtonWeight: Number.parseFloat(appearance.labelFontWeight)
        >= Number.parseFloat(appearance.buttonFontWeight),
      usesDangerColor: appearance.labelColor === appearance.dangerColor,
    }, `${theme} theme must keep the region label subdued`).toEqual({
      isPageTitleSized: false,
      isAtLeastButtonWeight: false,
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
