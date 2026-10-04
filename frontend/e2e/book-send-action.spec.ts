import { test, expect, type Page, type Route } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { assertNoHorizontalOverflow, collectPageErrors, assertNoPageErrors } from './utils';

// The real book page and send form use isolated identity/account/send transport.
// A button press must open the form; only its explicit Send queues a request.
async function install(page: Page) {
  const bookResponse = await page.request.get('/api/v1/books?per_page=100');
  expect(bookResponse.status()).toBe(200);
  const items = (await bookResponse.json()).items as {id: number; formats: string[]}[];
  const first = items.find(book => book.formats.length > 0);
  expect(first?.id, 'the harness must contain a book with a deliverable format').toBeDefined();
  const id = first!.id;
  const other = items.find(book => book.id !== id);
  expect(other?.id, 'the mounted transition needs a second fixture book').toBeDefined();
  const detailResponse = await page.request.get(`/api/v1/books/${id}`);
  expect(detailResponse.status()).toBe(200);
  const detail = await detailResponse.json();
  expect(detail.formats.length).toBeGreaterThan(0);
  const originalMe = await (await page.request.get('/api/v1/auth/me')).json();
  const state = {
    locale: 'en', theme: 'dark', configured: true, download: true, anonymous: false,
    member: true, hasFormats: true, email: 'reader@example.invalid', admin: true, holdSend: false,
  };
  const writes: Record<string, unknown>[] = [];
  const targets: string[] = [];
  const pending: Route[] = [];
  const otherId = other!.id;
  const otherTitle = detail.title + ' (another book)';
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: {
    ...originalMe, locale: state.locale, theme: state.theme, library_mode: 'personal_library',
    role: { ...originalMe.role, edit: true, admin: state.admin, download: state.download, anonymous: state.anonymous },
    features: { ...originalMe.features, mail_configured: state.configured },
  } }));
  await page.route('**/api/v1/account', async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), kindle_mail: state.email } });
  });
  await page.route(`**/api/v1/books/${id}`, route => route.fulfill({ json: {
    ...detail, in_my_library: state.member, formats: state.hasFormats ? detail.formats : [],
  } }));
  await page.route(`**/api/v1/books/${otherId}`, route => route.fulfill({ json: {
    ...detail, id: otherId, title: otherTitle, in_my_library: true,
  } }));
  await page.route('**/api/v1/send-recipients', route => route.fulfill({ json: { others: [
    {id: 999, name: 'Another reader', emails: ['another-reader@example.invalid']},
  ] } }));
  await page.route('**/api/v1/account/sidebar', route => {
    const body = route.request().postDataJSON();
    return route.fulfill({ json: {sidebar: body.visibility, sidebar_order: body.order} });
  });
  await page.route('**/api/v1/books/*/send', async route => {
    targets.push(new URL(route.request().url()).pathname);
    writes.push(route.request().postDataJSON());
    if (state.holdSend) { pending.push(route); return; }
    await route.fulfill({ json: { ok: true, message: 'Book queued for sending.' } });
  });
  return { state, writes, targets, id, title: detail.title, format: detail.formats[0].format, otherId, otherTitle,
    completePending: async () => {
      for (const route of pending.splice(0)) await route.fulfill({json: {ok: true, message: 'The previous book was queued.'}});
    },
  };
}

async function refreshIdentityWithoutLeavingBook(page: Page) {
  // Saving isolated navigation transport exercises the app's real /me cache
  // invalidation while BookDetail and its open panel remain mounted.
  const opener = page.getByRole('button', {name: 'Open navigation', exact: true});
  if (await opener.isVisible()) await opener.click();
  else await page.locator('nav').first().hover();
  await page.getByRole('button', {name: 'Customize navigation', exact: true}).click();
  const refreshed = page.waitForResponse(response => response.url().endsWith('/api/v1/auth/me'));
  await page.getByRole('button', {name: 'Done', exact: true}).click();
  expect((await refreshed).ok()).toBe(true);
  const closer = page.getByRole('button', {name: 'Close menu', exact: true});
  if (await closer.isVisible()) await closer.click();
}

async function navigateMountedBook(page: Page, id: number, title: string) {
  await page.evaluate(path => { history.pushState(null, '', path); window.dispatchEvent(new PopStateEvent('popstate')); }, `/app/book/${id}`);
  await expect(page.getByRole('heading', {name: title, exact: true})).toBeVisible();
}

for (const theme of ['light', 'dark']) {
  test(`visible send action opens the existing delivery form (${theme})`, async ({ page }, testInfo) => {
    const errors = collectPageErrors(page);
    const rig = await install(page);
    rig.state.theme = theme;
    await page.goto(`/app/book/${rig.id}`);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.getByRole('heading', { name: rig.title, exact: true })).toBeVisible();
    await expect(page.getByTestId('book-actions-menu')).toBeVisible();
    const action = page.getByTestId('book-actions').getByRole('button', { name: 'Send to e-reader', exact: true });
    await expect(action).toBeVisible();
    await expect(action).toHaveAttribute('aria-expanded', 'false');
    await expect(action).toHaveAttribute('aria-controls', 'book-send-ereader');
    await action.focus();
    await page.keyboard.press('Enter');
    await expect(action).toHaveAttribute('aria-expanded', 'true');
    const panel = page.locator('#book-send-ereader');
    await expect(panel).toBeVisible();
    await expect(panel.getByRole('textbox', { name: /^Recipient\(s\)(?:\s|$)/ })).toHaveValue(rig.state.email);
    expect(rig.writes).toEqual([]);
    await panel.getByLabel('Convert before sending').check();
    await panel.getByRole('button', { name: 'Send', exact: true }).click();
    await expect.poll(() => rig.writes).toEqual([{ format: rig.format, convert: true, emails: rig.state.email }]);
    await expect(panel.getByText('Book queued for sending.', { exact: true })).toBeVisible();
    await action.click();
    await expect(action).toHaveAttribute('aria-expanded', 'false');
    await expect(panel).toHaveCount(0);
    await page.getByTestId('book-actions-menu').click();
    await page.getByRole('menuitem', { name: 'Send to e-reader', exact: true }).click();
    await expect(action).toHaveAttribute('aria-expanded', 'true');
    await expect(panel).toBeVisible();
    expect(rig.writes).toHaveLength(1);
    expect(rig.targets).toEqual([`/api/v1/books/${rig.id}/send`]);
    await panel.getByRole('textbox', { name: /^Recipient\(s\)(?:\s|$)/ }).focus();
    await page.keyboard.press('Escape');
    await expect(panel).toHaveCount(0);
    await expect(action).toBeFocused();
    await action.click();
    await assertNoHorizontalOverflow(page);
    const axe = await new AxeBuilder({ page }).include('[data-testid="book-actions"]').include('#book-send-ereader').analyze();
    expect(axe.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`book-send-${theme}.jpg`), type: 'jpeg', quality: 75 });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    assertNoPageErrors(errors);
  });
}

test('send action follows delivery eligibility and permits an explicit recipient', async ({ page }) => {
  const rig = await install(page);
  const action = page.getByTestId('book-actions').getByRole('button', { name: 'Send to e-reader', exact: true });
  for (const key of ['configured', 'download', 'member', 'hasFormats', 'anonymous'] as const) {
    rig.state[key] = key === 'anonymous';
    await page.goto(`/app/book/${rig.id}`);
    await page.reload();
    await expect(page.getByRole('heading', { name: rig.title, exact: true })).toBeVisible();
    await expect(page.getByTestId('book-actions-menu')).toBeVisible();
    await expect(action).toHaveCount(0);
    await page.getByTestId('book-actions-menu').click();
    await expect(page.getByRole('menuitem', { name: 'Send to e-reader', exact: true })).toHaveCount(0);
    await page.keyboard.press('Escape');
    rig.state[key] = key !== 'anonymous';
  }
  rig.state.email = '';
  await page.reload();
  await expect(action).toBeVisible();
  await action.click();
  const panel = page.locator('#book-send-ereader');
  await panel.getByRole('textbox', { name: /^Recipient\(s\)(?:\s|$)/ }).fill('manual-reader@example.invalid');
  await panel.getByRole('button', { name: 'Send', exact: true }).click();
  await expect.poll(() => rig.writes).toEqual([{ format: rig.format, convert: false, emails: 'manual-reader@example.invalid' }]);
});

test('mounted send form drops revoked eligibility and cached administrator recipients', async ({page}) => {
  const rig = await install(page);
  await page.goto(`/app/book/${rig.id}`);
  await page.getByTestId('book-actions-menu').click();
  await page.getByRole('menuitem', {name:'Send to e-reader', exact:true}).click();
  await expect(page.getByTestId('send-other-ereaders')).toBeVisible();
  rig.state.admin = false;
  await refreshIdentityWithoutLeavingBook(page);
  await expect(page.getByTestId('send-other-ereaders')).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: /^Recipient\(s\)(?:\s|$)/ })).toBeVisible();
  rig.state.download = false;
  await refreshIdentityWithoutLeavingBook(page);
  await expect(page.getByRole('textbox', { name: /^Recipient\(s\)(?:\s|$)/ })).toHaveCount(0);
  rig.state.download = true;
  await refreshIdentityWithoutLeavingBook(page);
  const action = page.getByTestId('book-actions').getByRole('button', {name:'Send to e-reader', exact:true});
  await expect(action).toHaveAttribute('aria-expanded','false');
  await expect(page.getByRole('textbox', { name: /^Recipient\(s\)(?:\s|$)/ })).toHaveCount(0);
  expect(rig.writes).toEqual([]);
});

test('mounted send draft and pending response stay with their original book', async ({page}) => {
  const rig = await install(page);
  // Cache both detail responses, then open the original form through its
  // existing menu entry. Navigation preserves the BookDetail component.
  await page.goto(`/app/book/${rig.id}`);
  await navigateMountedBook(page, rig.otherId, rig.otherTitle);
  await navigateMountedBook(page, rig.id, rig.title);
  await page.getByTestId('book-actions-menu').click();
  await page.getByRole('menuitem', {name:'Send to e-reader', exact:true}).click();
  await page.getByRole('textbox', { name: /^Recipient\(s\)(?:\s|$)/ }).fill('draft-for-first-book@example.invalid');
  rig.state.holdSend = true;
  await page.getByRole('button', {name:'Send', exact:true}).click();
  await expect.poll(() => rig.writes).toHaveLength(1);
  await navigateMountedBook(page, rig.otherId, rig.otherTitle);
  await expect(page.getByRole('textbox', { name: /^Recipient\(s\)(?:\s|$)/ })).toHaveCount(0);
  const action = page.getByTestId('book-actions').getByRole('button', {name:'Send to e-reader', exact:true});
  await action.click();
  await expect(page.getByRole('textbox', { name: /^Recipient\(s\)(?:\s|$)/ })).toHaveValue(rig.state.email);
  await rig.completePending();
  await expect(page.getByRole('button', {name:'Send', exact:true})).toBeEnabled();
  await expect(page.getByText('The previous book was queued.', {exact:true})).toHaveCount(0);
  expect(rig.writes).toEqual([{format:rig.format,convert:false,emails:'draft-for-first-book@example.invalid'}]);
  expect(rig.targets).toEqual([`/api/v1/books/${rig.id}/send`]);
});
