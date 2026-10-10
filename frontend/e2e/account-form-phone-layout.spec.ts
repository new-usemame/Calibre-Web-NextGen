import { test, expect } from '@playwright/test';

// Paired Account fields shared one row even on phones. The font dropdowns then
// cut off their selected value in every engine, and in WebKit the narrowed
// selects' text also widened the page (403px of content in a 390px viewport),
// so Safari scrolled the whole Account page sideways.
test('account form fits phone widths and shows each dropdown value in full', async ({ page }) => {
  for (const width of [375, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto('/app/account');
    const form = page.locator('form').filter({ has: page.locator('#acc-font-body') });
    await expect(form.locator('#acc-font-body')).toBeVisible();
    await expect(form.locator('#acc-font-display')).toBeVisible();
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    });

    const layout = await form.evaluate((element) => {
      const canvas = document.createElement('canvas');
      const context = canvas.getContext('2d')!;
      const selects = Array.from(element.querySelectorAll('select')).map((select) => {
        const style = getComputedStyle(select);
        context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        const value = select.options[select.selectedIndex]?.text ?? '';
        // Room for the value once the control's padding and its arrow are drawn.
        const room = select.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 20;
        return { id: select.id, value, needed: Math.ceil(context.measureText(value).width), room: Math.floor(room) };
      });
      return { overflow: document.documentElement.scrollWidth - innerWidth, selects };
    });

    expect(layout.selects.length, 'the profile form renders its dropdowns').toBeGreaterThanOrEqual(4);
    expect(layout.overflow, `the Account page must not scroll sideways at ${width}px`).toBeLessThanOrEqual(1);
    for (const select of layout.selects) {
      expect(select.needed, `#${select.id} shows "${select.value}" in full at ${width}px`)
        .toBeLessThanOrEqual(select.room);
    }
  }
});
