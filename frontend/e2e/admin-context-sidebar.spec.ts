import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type TestInfo } from '@playwright/test';

const FAIL_IMPACTS = new Set(['critical', 'serious']);

// Compare stable theme endpoints rather than sampling a color transition.
test.use({ contextOptions: { reducedMotion: 'reduce' } });

async function setTheme(page: Page, theme: 'dark' | 'light') {
  await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
}

async function assertThemeTokensApplied(nav: ReturnType<Page['locator']>) {
  const colors = await nav.evaluate((node) => {
    const probe = document.createElement('span');
    probe.style.backgroundColor = 'var(--surface-1)';
    document.body.append(probe);
    const expectedBackground = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return {
      actualBackground: getComputedStyle(node).backgroundColor,
      expectedBackground,
    };
  });
  expect(colors.actualBackground).toBe(colors.expectedBackground);
}

async function assertLabelsFitOneLine(nav: ReturnType<Page['locator']>) {
  const failures = await nav.locator('[data-context-sidebar-label]').evaluateAll((labels) =>
    labels.flatMap((label) => {
      const style = getComputedStyle(label);
      const lineHeight = Number.parseFloat(style.lineHeight);
      const rect = label.getBoundingClientRect();
      const wraps = rect.height > lineHeight + 1;
      const clips = label.scrollWidth > label.clientWidth + 1;
      return wraps || clips || style.whiteSpace !== 'nowrap'
        ? [{
            label: label.textContent,
            height: rect.height,
            lineHeight,
            clientWidth: label.clientWidth,
            scrollWidth: label.scrollWidth,
            whiteSpace: style.whiteSpace,
          }]
        : [];
    }),
  );
  expect(failures, 'every 240px admin-rail label must fit one untruncated line').toEqual([]);
}

async function expectFullyVisibleInScroller(
  item: ReturnType<Page['locator']>,
) {
  await expect.poll(async () => item.evaluate((node) => {
    const scrollerNode = node.closest('nav');
    if (!scrollerNode) return false;
    const itemRect = node.getBoundingClientRect();
    const scrollerRect = scrollerNode.getBoundingClientRect();
    return itemRect.top >= scrollerRect.top
      && itemRect.bottom <= Math.min(scrollerRect.bottom, window.innerHeight);
  })).toBeTruthy();
}

async function attachEvidence(page: Page, testInfo: TestInfo, name: string) {
  const path = testInfo.outputPath(`${name}.jpg`);
  await page.screenshot({ path, type: 'jpeg', quality: 68, fullPage: true });
  await testInfo.attach(name, { path, contentType: 'image/jpeg' });
}

async function assertNoSeriousAxeViolations(page: Page, label: string) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze();
  const failures = results.violations
    .filter((violation) => FAIL_IMPACTS.has(violation.impact || ''))
    .map((violation) => `${violation.id} [${violation.impact}] (${violation.nodes.length})`);
  expect(failures, `${label} has critical/serious axe violations`).toEqual([]);
}

test('desktop swaps the catalog rail for fixed admin navigation and restores it on exit', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop context-navigation contract');

  await page.goto('/app');
  const browseNav = page.getByRole('navigation', { name: 'Browse' });
  await expect(browseNav).toBeVisible();

  const account = page.getByRole('button', { name: /account:/i });
  await account.click();
  await account.locator('xpath=ancestor::div[1]').getByRole('link', { name: 'Admin', exact: true }).click();
  await expect(page).toHaveURL(/\/app\/admin\/?$/);

  const adminNav = page.getByRole('navigation', { name: 'Admin navigation' });
  await expect(adminNav).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Browse' })).toHaveCount(0);
  await expect(page.locator('main')).toHaveCount(1);
  await expect(adminNav.getByRole('link', { name: 'Users' })).toHaveAttribute('aria-current', 'page');
  await expect(adminNav.locator('[aria-current="page"]')).toHaveCount(1);
  await expect(adminNav.getByRole('list')).toHaveCount(3);
  await expect(adminNav.getByRole('button', { name: /pin sidebar/i })).toHaveCount(0);
  await expect(adminNav.getByRole('button', { name: /customize navigation/i })).toHaveCount(0);
  await expect(adminNav.getByRole('link', { name: 'Back to library' })).toBeVisible();
  expect(await page.locator('[data-context-sidebar="admin"]').evaluate((node) => getComputedStyle(node).width)).toBe('240px');

  await adminNav.getByRole('link', { name: 'Library', exact: true }).click();
  await expect(page).toHaveURL(/\/app\/admin#library-settings$/);
  await expect(adminNav.getByRole('link', { name: 'Library', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('#library-settings')).toBeVisible();

  // A fresh direct section URL must resolve after the async admin forms mount.
  await page.goto('/app/admin#email-settings');
  await expect(adminNav.getByRole('link', { name: 'Email server' })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('#email-settings')).toBeVisible();

  await page.goto('/app/admin');
  await expect(page.getByRole('heading', { name: 'User administration' })).toBeVisible();
  await expect(adminNav.getByRole('link', { name: 'Users' })).toHaveAttribute('aria-current', 'page');
  for (const theme of ['dark', 'light'] as const) {
    await setTheme(page, theme);
    await assertThemeTokensApplied(adminNav);
    await assertNoSeriousAxeViolations(page, `admin desktop/${theme}`);
    await attachEvidence(page, testInfo, `admin-desktop-${theme}`);
  }

  await adminNav.getByRole('link', { name: 'Devices' }).click();
  await expect(page).toHaveURL(/\/app\/admin\/devices$/);
  await expect(adminNav.getByRole('link', { name: 'Devices' })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('main')).toHaveCount(1);

  await adminNav.getByRole('link', { name: 'Back to library' }).click();
  await expect(page).toHaveURL(/\/app\/?$/);
  await expect(page.getByRole('navigation', { name: 'Admin navigation' })).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: 'Browse' })).toBeVisible();
});

test('desktop short viewport keeps labels single-line and the final destination reachable', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop context-navigation contract');

  await page.setViewportSize({ width: 1280, height: 700 });
  await page.goto('/app/admin');
  const adminNav = page.getByRole('navigation', { name: 'Admin navigation' });
  const logs = adminNav.getByRole('link', { name: 'Logs' });
  await expect(adminNav).toBeVisible();

  await expect.poll(() => adminNav.evaluate((node) => {
    const rect = node.getBoundingClientRect();
    return Math.round(rect.bottom) <= window.innerHeight;
  })).toBeTruthy();
  expect(await page.locator('[data-context-sidebar="admin"]').evaluate((node) => getComputedStyle(node).width)).toBe('240px');
  await assertLabelsFitOneLine(adminNav);

  // Keyboard: tab through the native links; focus scrolling must reveal Logs.
  const links = adminNav.getByRole('link');
  await links.first().focus();
  for (let index = 1; index < await links.count(); index += 1) {
    await page.keyboard.press('Tab');
  }
  await expect(logs).toBeFocused();
  await expectFullyVisibleInScroller(logs);

  // Wheel: the pointer remains over the contained rail while it reaches bottom.
  await adminNav.evaluate((node) => { node.scrollTop = 0; });
  await adminNav.hover();
  await page.mouse.wheel(0, 2000);
  await expect.poll(() => adminNav.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  await expectFullyVisibleInScroller(logs);

  // The regression contract itself: Playwright must be able to scroll the last
  // destination fully into the short viewport (not merely find it in the DOM).
  await adminNav.evaluate((node) => { node.scrollTop = 0; });
  await logs.scrollIntoViewIfNeeded();
  await expect.poll(() => adminNav.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  await expectFullyVisibleInScroller(logs);

  await attachEvidence(page, testInfo, 'admin-desktop-700px');
});

test('mobile admin drawer stays reachable, locked, inert, and keyboard dismissible', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'mobile context-navigation contract');

  await page.goto('/app/admin');
  const adminNav = page.locator('nav[aria-label="Admin navigation"]');
  await expect(adminNav).toHaveAttribute('inert', '');
  await expect(page.locator('nav[aria-label="Browse"]')).toHaveCount(0);

  const trigger = page.getByRole('banner').getByRole('button').first();
  await trigger.click();
  await expect(adminNav).not.toHaveAttribute('inert', '');
  await expect(adminNav).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe('hidden');
  await expect.poll(() => adminNav.evaluate((node) => node.contains(document.activeElement))).toBeTruthy();
  await expect(adminNav.getByRole('link', { name: 'Back to library' })).toBeVisible();
  await expect(adminNav.getByRole('link', { name: 'Users' })).toHaveAttribute('aria-current', 'page');
  await expect(adminNav.locator('[aria-current="page"]')).toHaveCount(1);
  await expect(adminNav.getByRole('list')).toHaveCount(3);
  await expect(adminNav.getByRole('button', { name: /pin sidebar/i })).toHaveCount(0);

  for (const theme of ['dark', 'light'] as const) {
    await setTheme(page, theme);
    await assertThemeTokensApplied(adminNav);
    await assertNoSeriousAxeViolations(page, `admin mobile/${theme}`);
    await attachEvidence(page, testInfo, `admin-mobile-${theme}`);
  }

  const logs = adminNav.getByRole('link', { name: /Logs/ });
  await logs.scrollIntoViewIfNeeded();
  await expect(logs).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(adminNav).toHaveAttribute('inert', '');
  await expect(trigger).toBeFocused();
  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe('');

  await trigger.click();
  await adminNav.getByRole('link', { name: 'Back to library' }).click();
  await expect(page).toHaveURL(/\/app\/?$/);
  await expect(page.locator('nav[aria-label="Admin navigation"]')).toHaveCount(0);
  await expect(page.locator('nav[aria-label="Browse"]')).toHaveAttribute('inert', '');
  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe('');
});
