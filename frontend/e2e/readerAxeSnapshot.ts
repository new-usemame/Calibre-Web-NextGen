import { expect, type Locator } from '@playwright/test';

/**
 * Axe's asynchronous frame runner cannot run inside a script-disabled EPUB
 * sandbox: Chromium discards its promise and AxeBuilder swallows that child
 * failure. Scan an inert snapshot of the rendered document in the same frame.
 *
 * Call ONLY after real reader interaction checks. Preserve the actual DOM,
 * inline/theme styles, frame viewport and scroll offset; strip author scripts
 * and event handlers before enabling execution for axe. Product code and its
 * sandbox policy are unchanged. This is a static accessibility snapshot, not
 * an oracle for reader event behavior after the replacement.
 */
export async function snapshotReaderFrameForAxe(frame: Locator) {
  const content = frame.contentFrame();
  await expect(content.locator('body')).toBeVisible();
  const measure = () => frame.evaluate(node => {
    const iframe = node as HTMLIFrameElement;
    const body = iframe.contentDocument!.body;
    const style = iframe.contentWindow!.getComputedStyle(body);
    return {
      frame: iframe.getBoundingClientRect().toJSON(),
      body: body.getBoundingClientRect().toJSON(),
      text: body.innerText,
      color: style.color,
      background: style.backgroundColor,
      font: style.fontSize,
    };
  });
  const before = await measure();
  const scroll = await frame.evaluate(node => {
    const iframe = node as HTMLIFrameElement;
    if (!iframe.sandbox.contains('allow-same-origin') || iframe.sandbox.contains('allow-scripts')) {
      throw new Error('Expected the real reader script-disabled same-origin sandbox');
    }
    const doc = iframe.contentDocument!;
    const snapshot = doc.documentElement.cloneNode(true) as HTMLElement;
    snapshot.querySelectorAll('script').forEach(script => script.remove());
    [snapshot, ...snapshot.querySelectorAll('*')].forEach(element => {
      for (const attr of Array.from(element.attributes)) {
        if (attr.name.toLowerCase().startsWith('on')) element.removeAttribute(attr.name);
      }
    });
    snapshot.dataset.a11ySnapshot = 'ready';
    const offset = { x: iframe.contentWindow!.scrollX, y: iframe.contentWindow!.scrollY };
    iframe.sandbox.add('allow-scripts');
    iframe.srcdoc = snapshot.outerHTML;
    return offset;
  });
  await expect(content.locator('html')).toHaveAttribute('data-a11y-snapshot', 'ready');
  await content.locator('body').evaluate(async (_, offset) => {
    await document.fonts.ready;
    await Promise.all(Array.from(document.images).map(image => image.decode().catch(() => {})));
    window.scrollTo(offset.x, offset.y);
  }, scroll);
  expect(await measure(), 'rendered reader geometry, text and theme survive the inert snapshot').toEqual(before);
}
