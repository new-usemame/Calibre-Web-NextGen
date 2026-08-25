import { test, expect } from '@playwright/test';

test('desktop sidebar releases hover retained across navigation', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'desktop fine-pointer rail only');

  await page.goto('/app/');
  const firstBook = page.locator('main a[href*="/book/"]').first();
  await expect(firstBook).toBeVisible({ timeout: 20_000 });
  await firstBook.click();
  await expect(page).toHaveURL(/\/book\/\d+(?:\?.*)?$/);
  const bookUrl = page.url();

  // Enter the expandable rail as a user does, then leave the pointer parked on
  // the clicked item while navigation changes the page beneath it.
  const nav = page.getByRole('navigation', { name: 'Browse' });
  const tagsLink = nav.getByRole('link', { name: 'Tags', exact: true });
  await tagsLink.hover();
  await expect.poll(() => nav.evaluate((element) => getComputedStyle(element).width))
    .toBe('220px');
  const navBox = await nav.boundingBox();
  const tagsBox = await tagsLink.boundingBox();
  expect(navBox).not.toBeNull();
  expect(tagsBox).not.toBeNull();
  const parkedX = navBox!.x + 48;
  await tagsLink.click({
    position: { x: parkedX - tagsBox!.x, y: tagsBox!.height / 2 },
  });
  await expect(page).toHaveURL(/\/tags\/?(?:\?.*)?$/);
  await expect.poll(() => nav.evaluate((element) =>
    element.getAnimations().filter((animation) => animation.playState === 'running').length,
  )).toBe(0);

  // Suppressing stale hover must never defeat keyboard expansion. Route focus
  // starts at main; Shift+Tab enters the preceding sidebar control.
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('main');
  await page.keyboard.press('Shift+Tab');
  expect(await nav.evaluate((element) => element.contains(document.activeElement)))
    .toBe(true);
  await expect.poll(() => nav.evaluate((element) => getComputedStyle(element).width))
    .toBe('220px');

  // Returning to the book does not move the pointer. The newly landed page's
  // back link is inside the 156px overlay strip formerly retained by :hover.
  await page.goBack();
  await expect(page).toHaveURL(bookUrl);
  const backLink = page.getByRole('link', { name: '← Library', exact: true });
  await expect(backLink).toBeVisible();
  await expect.poll(() => nav.evaluate((element) =>
    element.getAnimations().filter((animation) => animation.playState === 'running').length,
  )).toBe(0);

  const box = await backLink.boundingBox();
  expect(box, 'book back link has a rendered box').not.toBeNull();
  const centre = { x: box!.x + box!.width / 2, y: box!.y + box!.height / 2 };
  expect(centre.x, 'regression control sits inside the rail\'s 156px overlay strip')
    .toBeLessThan(156);

  const hitAtCentre = () => backLink.evaluate((target, point) => {
    const element = document.elementFromPoint(point.x, point.y);
    return {
      targetOwnsHit: element === target || target.contains(element),
      hitTag: element?.tagName ?? null,
      hitText: element?.textContent?.trim() ?? null,
      hitHref: element instanceof HTMLAnchorElement ? element.getAttribute('href') : null,
      hitInsideNav: !!element?.closest('nav[aria-label="Browse"]'),
    };
  }, centre);

  // Follow a human mouse path from the icon-side click point to the page
  // control. Every intermediate move must keep stale hover suppressed; if the
  // rail expands under the travelling pointer, it captures the destination.
  const parkedY = tagsBox!.y + tagsBox!.height / 2;
  for (let step = 1; step <= 12; step += 1) {
    const point = {
      x: parkedX + ((centre.x - parkedX) * step) / 12,
      y: parkedY + ((centre.y - parkedY) * step) / 12,
    };
    await page.mouse.move(point.x, point.y);
    await page.waitForTimeout(50);
    expect(await nav.evaluate((element) => getComputedStyle(element).width),
      `rail width after traversal step ${step}`).toBe('64px');
    const hit = await hitAtCentre();
    expect(hit.targetOwnsHit,
      `step ${step}: back-link centre ${JSON.stringify(centre)} hit ${JSON.stringify(hit)}`)
      .toBe(true);
  }

  await page.mouse.down();
  await page.mouse.up();
  await expect(page).toHaveURL(/\/app\/?(?:\?.*)?$/);

  // Once the pointer has completed its journey out, deliberately returning to
  // the collapsed rail is new hover intent and must expand it again.
  await page.mouse.move(navBox!.x + 32, navBox!.y + 32, { steps: 12 });
  await expect.poll(() => nav.evaluate((element) => getComputedStyle(element).width))
    .toBe('220px');
});
