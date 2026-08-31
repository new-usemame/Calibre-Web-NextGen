import { expect, test } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const TXT_FIXTURE = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../tests/fixtures/sample_books/alice_in_wonderland.txt',
);

test('WebKit tabs into the native TXT scroller and scrolls it from the keyboard', async ({ page }, testInfo) => {
  expect(testInfo.project.name).toBe('webkit-reader');

  await page.route('**/show/1842/txt', (route) => route.fulfill({
    status: 200,
    contentType: 'text/plain; charset=utf-8',
    path: TXT_FIXTURE,
  }));
  await page.goto('/app/view/1842/txt');

  const closeReader = page.getByRole('link', { name: 'Close reader' });
  const content = page.getByRole('region', { name: 'Book content' });
  await expect(content.locator('pre')).toContainText("Alice's Adventures in Wonderland");
  await expect.poll(() => content.evaluate((element) => element.scrollHeight > element.clientHeight), {
    message: 'the TXT fixture does not overflow the native reader scroll container',
  }).toBe(true);
  await content.evaluate((element) => { element.scrollTop = 0; });

  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  });
  await page.keyboard.press('Tab');
  await expect(closeReader).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(content).toBeFocused();

  const before = await content.evaluate((element) => element.scrollTop);
  await page.keyboard.press('PageDown');
  await expect.poll(() => content.evaluate((element) => element.scrollTop), {
    message: 'PageDown did not scroll the focused TXT content region in WebKit',
  }).toBeGreaterThan(before);
  await expect(content).toBeFocused();

  const focusStyle = await content.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      outlineStyle: style.outlineStyle,
      outlineWidth: Number.parseFloat(style.outlineWidth),
      outlineOffset: Number.parseFloat(style.outlineOffset),
    };
  });
  expect(focusStyle.outlineStyle).not.toBe('none');
  expect(focusStyle.outlineWidth).toBeGreaterThanOrEqual(2);
  expect(focusStyle.outlineOffset).toBeLessThan(0);
});
