import { expect, test, type Page } from '@playwright/test';

const DISMISSAL_KEYS = [
  'cwng_help_banner_dismissed_v1',
  'cwng_kofi_banner_dismissed_v1',
  'cwng_banner_dismissed:help-announcement-v1',
  'cwng_banner_dismissed:kofi-support-v1',
  'cwng_banner_dismissed:kofi-support-v0',
];

const EXPECTED_BODY = {
  dark: 'rgb(20, 28, 36)',
  light: 'rgb(244, 241, 234)',
} as const;

async function selectRealTheme(page: Page, theme: keyof typeof EXPECTED_BODY) {
  await page.goto('/app/account');
  const picker = page.locator('#acc-theme');
  await expect(picker).toBeVisible();

  if (await picker.inputValue() !== theme) {
    const saved = page.waitForResponse((response) =>
      response.url().includes('/api/v1/account/profile')
        && response.request().method() === 'POST');
    await picker.selectOption(theme);
    expect((await saved).ok()).toBeTruthy();
  }

  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.body).backgroundColor))
    .toBe(EXPECTED_BODY[theme]);
}

async function showHelpAnnouncement(page: Page) {
  await page.goto('/app/');
  await page.evaluate((keys) => keys.forEach((key) => localStorage.removeItem(key)), DISMISSAL_KEYS);
  await page.reload();
  const banner = page.locator('[data-announcement-id="help-announcement-v1"]');
  await expect(banner).toBeVisible();
  return banner;
}

async function readThemeAndBanner(page: Page) {
  const banner = await showHelpAnnouncement(page);
  return banner.evaluate((element) => {
    const tokenProbe = document.createElement('span');
    tokenProbe.style.backgroundColor = 'var(--hb-bg-start)';
    document.body.append(tokenProbe);
    const tokenStart = getComputedStyle(tokenProbe).backgroundColor;
    tokenProbe.remove();

    return {
      body: getComputedStyle(document.body).backgroundColor,
      background: getComputedStyle(element).backgroundImage,
      tokenStart,
    };
  });
}

test('HelpBanner resolves its background from the active theme tokens', async ({ page }) => {
  try {
    await selectRealTheme(page, 'dark');
    const dark = await readThemeAndBanner(page);
    expect(dark.body).toBe(EXPECTED_BODY.dark);

    await selectRealTheme(page, 'light');
    const lightAccountBody = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    expect(lightAccountBody).toBe(EXPECTED_BODY.light);
    expect(lightAccountBody, 'the real account control must change the rendered theme')
      .not.toBe(dark.body);

    const light = await readThemeAndBanner(page);
    console.log(`dark: body ${dark.body}; banner ${dark.background}; token ${dark.tokenStart}`);
    console.log(`light: body ${light.body}; banner ${light.background}; token ${light.tokenStart}`);

    expect(light.body).toBe(EXPECTED_BODY.light);
    expect(light.background, 'the banner must not render identically across themes')
      .not.toBe(dark.background);
    expect(light.tokenStart, 'the light HelpBanner start token must resolve to a colour')
      .not.toBe('rgba(0, 0, 0, 0)');
    expect(light.background, 'the light banner must use --hb-bg-start')
      .toContain(light.tokenStart);
    expect(light.background, 'the light banner must not retain the dark literal')
      .not.toContain('rgb(23, 56, 66)');
  } finally {
    // The preference is server-side state shared by the seeded e2e user.
    await selectRealTheme(page, 'dark');
  }
});
