import { expect, test, type Page, type Locator } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

async function openFixture(page: Page) {
  const builder = fileURLToPath(new URL('../../tests/fixtures/reader_archive.py', import.meta.url));
  const epub = execFileSync('python3', [builder], { input: JSON.stringify([
    '<p id="existing">Existing web passage remains.</p>',
  ]) });
  const list = await (await page.request.get('/api/v1/books?per_page=1')).json();
  const id = list.items[0].id;
  let rows = [
    { annotation_id: 'native-unresolved', position_type: 'kobo', cfi_range: null,
      start_kobospan: 'missing', end_kobospan: 'missing', highlighted_text: 'Unresolved native passage',
      note_text: 'Attached note', highlight_color: 'yellow', source: 'kobo' },
    { annotation_id: 'standalone', position_type: 'unanchored', cfi_range: null,
      highlighted_text: null, note_text: 'A note about this book', highlight_color: null, source: 'webreader' },
  ];
  const writes: Array<{ method: string; annotationId: string; body: unknown }> = [];
  await page.route(`**/api/v1/books/${id}`, async route => {
    const response = await route.fetch();
    const detail = await response.json();
    detail.formats = [{ format: 'EPUB', size_bytes: epub.length, content_url: `/show/${id}/epub` }];
    await route.fulfill({ response, json: detail });
  });
  await page.route(`**/show/${id}/epub`, route => route.fulfill({ contentType: 'application/epub+zip', body: epub }));
  await page.route(`**/api/v1/books/${id}/bookmark*`, route => route.fulfill({ json: { bookmark: null } }));
  await page.route('**/api/v1/reader/settings', route => route.fulfill({ json: { reader: {
    theme: 'lightTheme', font: 'Arial', fontSize: 100, margin: 16, lineHeight: 150, spread: 'nonespread',
  } } }));
  await page.route(`**/annotations/${id}/*`, async route => {
    const annotationId = new URL(route.request().url()).pathname.split('/').pop()!;
    if (annotationId === 'data.json') return route.fulfill({ json: { annotations: rows, devices: {} } });
    const method = route.request().method();
    expect(['PATCH', 'DELETE']).toContain(method);
    const body = method === 'PATCH' ? route.request().postDataJSON() : null;
    writes.push({ method, annotationId, body });
    rows = method === 'DELETE' ? rows.filter(row => row.annotation_id !== annotationId)
      : rows.map(row => row.annotation_id === annotationId ? { ...row, ...body } : row);
    await route.fulfill({ json: { annotation_id: annotationId } });
  });
  await page.goto(`/app/read/${id}`);
  await expect(page.locator('iframe[title="Book content"]')).toBeVisible();
  const trigger = page.getByRole('button', { name: 'Highlights and notes', exact: true });
  await trigger.click();
  const drawer = page.getByRole('navigation', { name: 'Highlights and notes', exact: true });
  await expect(drawer.getByRole('listitem')).toHaveCount(2);
  return { trigger, drawer, writes };
}

test('drawer edits unresolved highlights with keyboard focus, accessible targets and anchored-note preservation', async ({ page }) => {
  const { trigger, drawer, writes } = await openFixture(page);
  const row = drawer.getByRole('listitem').filter({ hasText: 'Unresolved native passage' });
  await expect(row.getByRole('button').first()).toBeDisabled();
  const edit = row.getByRole('button', { name: 'Edit', exact: true });
  await expect(edit).toBeVisible();
  await expect(edit).toHaveAccessibleDescription(/Unresolved native passage/);
  const bounds = await edit.boundingBox();
  expect(bounds!.width).toBeGreaterThanOrEqual(44);
  expect(bounds!.height).toBeGreaterThanOrEqual(44);
  const audit = await new AxeBuilder({ page }).include('nav[aria-label="Highlights and notes"]').analyze();
  expect(audit.violations.filter(v => ['serious', 'critical'].includes(v.impact ?? ''))).toEqual([]);
  await edit.focus();
  await page.keyboard.press('Enter');
  const palette = page.getByRole('dialog', { name: 'Highlight color', exact: true });
  await expect(palette).toBeVisible();
  await expect(drawer).toHaveCount(0);
  expect(await palette.evaluate(el => el.contains(document.activeElement))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(palette).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  await edit.click();
  await palette.getByRole('button', { name: 'Green', exact: true }).click();
  await expect.poll(() => writes).toEqual([{ method: 'PATCH', annotationId: 'native-unresolved', body: { highlight_color: 'green' } }]);
  await trigger.click();
  await edit.click();
  await palette.getByRole('button', { name: 'Edit note', exact: true }).click();
  const composer = page.getByRole('dialog', { name: 'Edit note', exact: true });
  await expect(composer.getByRole('textbox', { name: 'Note', exact: true })).toHaveValue('Attached note');
  await composer.getByRole('button', { name: 'Remove note', exact: true }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1]).toEqual({ method: 'PATCH', annotationId: 'native-unresolved', body: { note_text: '' } });
  await trigger.click();
  await expect(row).toBeVisible();
  await edit.click();
  await palette.getByRole('button', { name: 'Remove highlight', exact: true }).click();
  await expect.poll(() => writes.length).toBe(3);
  expect(writes[2]).toEqual({ method: 'DELETE', annotationId: 'native-unresolved', body: null });
});

test('drawer edits standalone notes and removes their rows without leaving empty annotations', async ({ page, isMobile }) => {
  const activate = (button: Locator) => isMobile ? button.tap() : button.click();
  const { trigger, drawer, writes } = await openFixture(page);
  const row = drawer.getByRole('listitem').filter({ hasText: 'A note about this book' });
  await activate(row.getByRole('button', { name: 'Edit', exact: true }));
  const composer = page.getByRole('dialog', { name: 'Edit note', exact: true });
  const note = composer.getByRole('textbox', { name: 'Note', exact: true });
  await expect(note).toBeFocused();
  await expect(note).toHaveValue('A note about this book');
  await note.fill('Revised standalone note');
  await activate(composer.getByRole('button', { name: 'Save note', exact: true }));
  await expect.poll(() => writes).toEqual([{ method: 'PATCH', annotationId: 'standalone', body: { note_text: 'Revised standalone note' } }]);
  await trigger.click();
  await activate(drawer.getByRole('listitem').filter({ hasText: 'Revised standalone note' }).getByRole('button', { name: 'Edit', exact: true }));
  await activate(composer.getByRole('button', { name: 'Remove note', exact: true }));
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1]).toEqual({ method: 'DELETE', annotationId: 'standalone', body: null });
  await trigger.click();
  await expect(drawer.getByRole('listitem')).toHaveCount(1);
  await expect(drawer).not.toContainText('Revised standalone note');
});
