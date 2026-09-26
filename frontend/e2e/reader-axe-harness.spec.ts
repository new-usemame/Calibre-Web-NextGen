import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { snapshotReaderFrameForAxe } from './readerAxeSnapshot';

test('reader axe snapshot detects a child defect without executing book scripts', async ({ page }) => {
  await page.setContent('<html lang="en"><head><title>Axe harness</title></head><body><main></main></body></html>');
  await page.evaluate(() => {
    const frame = document.createElement('iframe');
    frame.title = 'Book content';
    frame.sandbox.add('allow-same-origin');
    frame.style.cssText = 'width:400px;height:300px';
    frame.srcdoc = '<html lang="en"><head><title>Book</title></head>'
      + '<body style="background:white;color:black" onload="parent.document.body.dataset.authorScript=1">'
      + '<script>parent.document.body.dataset.authorScript=1</script>'
      + '<button id="unnamed" style="width:100px;height:100px"></button></body></html>';
    document.querySelector('main')!.append(frame);
  });
  await snapshotReaderFrameForAxe(page.locator('iframe'));
  await expect(page.locator('body')).not.toHaveAttribute('data-author-script');
  const result = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze();
  const defect = result.violations.find(v => v.id === 'button-name');
  expect(defect?.nodes.some(node => JSON.stringify(node.target).includes('#unnamed')),
    'the full axe runner must report the defect inside the reader frame').toBe(true);
});
