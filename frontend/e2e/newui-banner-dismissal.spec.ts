import { test, expect, type BrowserContext, type Page } from '@playwright/test';

async function preferenceCookies(context: BrowserContext) {
  return Object.fromEntries(
    (await context.cookies())
      .filter((cookie) => cookie.name.startsWith('cwng_prefer_'))
      .map((cookie) => [cookie.name, cookie.value]),
  );
}

async function openClassicNav(page: Page, isMobile: boolean) {
  if (isMobile) await page.locator('.navbar-toggle').click();
  return page.locator('.cwng-switch-ui');
}

function isRootClassicFallback(url: URL) {
  const feedback = url.pathname === '/'
    && url.searchParams.get('cwng_feedback') === 'newui'
    && [...url.searchParams].length === 1;
  const login = url.pathname === '/login'
    && url.searchParams.get('next') === '/?cwng_feedback=newui'
    && [...url.searchParams].length === 1;
  return feedback || login;
}

test('a cookie-less browser always opens the SPA', async ({
  page, context,
}) => {
  await context.clearCookies({ name: /^cwng_prefer_(spa|classic)$/ });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/app\/?$/);

  const cookies = await preferenceCookies(context);
  expect(cookies.cwng_prefer_classic).toBeUndefined();
  expect(cookies.cwng_prefer_spa).toBe('1');
});

test('a JavaScript-disabled browser self-heals to Classic', async ({ browser, baseURL }) => {
  const context = await browser.newContext({
    baseURL,
    javaScriptEnabled: false,
    storageState: { cookies: [], origins: [] },
  });
  try {
    const page = await context.newPage();
    await page.goto('/app');
    await page.waitForURL((url) => isRootClassicFallback(url));
    const terminal = new URL(page.url());
    if (terminal.pathname === '/login') {
      // Classic's own login field. `input[autocomplete="username"]` belongs to
      // the SPA's React form, which renders nothing with JavaScript disabled --
      // asserting it here could never pass, and "not found" would look the same
      // whether the fallback worked or served an inert SPA shell.
      await expect(page.locator('input#username[name="username"]')).toBeVisible();
    } else {
      await expect(page.locator('#books').first()).toBeVisible();
    }
    const cookies = await preferenceCookies(context);
    expect(cookies.cwng_prefer_classic).toBeUndefined();
    expect(cookies.cwng_prefer_spa).toBeUndefined();
  } finally {
    await context.close();
  }
});

test('the session Classic escape hatch round-trips through Back to New UI', async ({
  page, context, isMobile,
}) => {
  await page.goto('/?cwng_feedback=newui', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/$/);
  expect((await preferenceCookies(context)).cwng_prefer_classic).toBeUndefined();
  await expect(page.locator('#cwng-newui-banner')).toHaveCount(0);

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/$/);

  const returnToSpa = await openClassicNav(page, isMobile);
  await expect(returnToSpa).toContainText('Back to New UI');
  await returnToSpa.click();
  await expect(page).toHaveURL(/\/app\/?$/);
  expect((await preferenceCookies(context)).cwng_prefer_classic).toBeUndefined();

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/app\/?$/);
});
