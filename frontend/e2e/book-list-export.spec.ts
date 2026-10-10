import { test, expect } from './fixtures';
import AxeBuilder from '@axe-core/playwright';

// Real catalog widgets and authenticated download helper; only export bytes
// are mocked here. Backend query/filter parity has its own behavioral tests.
test('book-list export sends current filters, retries failure and downloads CSV bytes', async ({ secondaryUser }, info) => {
  const { page } = secondaryUser;
  await page.setViewportSize(info.project.use.viewport || {width:1280,height:800});
  let payload: { source: string; params: Record<string, unknown>; format: string } | undefined;
  let fail = true;
  await page.route('**/api/v1/books/export', async route => {
    payload = route.request().postDataJSON();
    if (fail) return route.fulfill({status:413,json:{error:{code:'export_too_large',message:'too many'}}});
    await route.fulfill({headers:{'Content-Type':'text/csv; charset=utf-8','Content-Disposition':'attachment; filename="calibre-web-books.csv"'},body:'Title,Authors\r\n"A, title",An Author\r\n'});
  });
  await page.goto('/app?q=A%2C%20title');
  await page.getByRole('button',{name:'Unread',exact:true}).click();
  // #2475: on the library landing, Export lives in View settings.
  await page.getByTestId('catalog-view-settings').click();
  const box=page.getByRole('group',{name:'Export this book list'});
  await expect(box.getByRole('button',{name:'Export CSV'})).toBeEnabled();
  await box.getByRole('button',{name:'Export CSV'}).click();
  await expect(box.getByRole('status')).toContainText('Too many books to export.');
  expect(payload).toMatchObject({format:'csv',source:'catalog',params:{search:'A, title',filter:'unread'}});
  fail=false;
  const downloaded=page.waitForEvent('download');
  await box.getByRole('button',{name:'Export CSV'}).click();
  const file=await downloaded;expect(file.suggestedFilename()).toBe('calibre-web-books.csv');
  const stream=await file.createReadStream();expect(stream).not.toBeNull();
  const chunks: Buffer[]=[];for await(const chunk of stream!)chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks).toString()).toBe('Title,Authors\r\n"A, title",An Author\r\n');
  await expect(box.getByRole('status')).toContainText('Book list download started.');
  const axe=await new AxeBuilder({page}).include('[aria-label="Export this book list"]').analyze();expect(axe.violations.filter(v=>['serious','critical'].includes(v.impact||''))).toEqual([]);
});

test('changing list scope cancels an export and suppresses a stale download', async ({ secondaryUser }, info) => {
  const { page }=secondaryUser;
  await page.setViewportSize(info.project.use.viewport || {width:1280,height:800});
  let release: (()=>void)|undefined;
  const held=new Promise<void>(resolve=>{release=resolve;});
  let started=false;
  const downloads: string[]=[];
  page.on('download',d=>downloads.push(d.suggestedFilename()));
  await page.route('**/api/v1/books/export',async route=>{
    started=true;await held;
    await route.fulfill({headers:{'Content-Type':'text/plain','Content-Disposition':'attachment; filename="calibre-web-books.txt"'},body:'Old scope\n'}).catch(()=>{});
  });
  try {
    await page.goto('/app');
    await page.getByTestId('catalog-view-settings').click();
    const box=page.getByRole('group',{name:'Export this book list'});
    await box.getByRole('button',{name:'Export TXT'}).click();
    await expect.poll(()=>started).toBe(true);
    const failed=page.waitForEvent('requestfailed',{predicate:r=>r.url().endsWith('/api/v1/books/export'),timeout:8000});
    await page.getByRole('button',{name:'Read',exact:true}).click();
    await failed;
    release?.();
    // The menu stayed open through the pending export, so the cancel is visible.
    await expect(box.getByRole('button',{name:'Export TXT'})).toBeEnabled();
    expect(downloads).toEqual([]);
  } finally { release?.(); }
});


// Classic actions are progressive enhancement: an unbound button must not
// promise an action. Hold the real script to exercise the loading window.
test('Classic export remains disabled until its action script binds', async ({ secondaryUser }, info) => {
  const { page } = secondaryUser;
  await page.setViewportSize(info.project.use.viewport || { width: 1280, height: 800 });
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/static/js/book-list-export.js*', async route => {
    await held;
    await route.continue();
  });
  try {
    await page.goto('/search?query=cwng-export-empty-' + secondaryUser.username, { waitUntil: 'commit' });
    const box = page.locator('.book-list-export');
    await expect(box).toBeVisible();
    await expect(box.getByRole('button', { name: 'Export CSV', exact: true })).toBeDisabled();
    await expect(box.getByRole('button', { name: 'Export TXT', exact: true })).toBeDisabled();
    release();
    await page.waitForLoadState('load');
    await expect(box.getByRole('button', { name: 'Export CSV', exact: true })).toBeEnabled();
    const downloaded = page.waitForEvent('download');
    await box.getByRole('button', { name: 'Export CSV', exact: true }).click();
    const file = await downloaded;
    expect(file.suggestedFilename()).toBe('calibre-web-books.csv');
    const stream = await file.createReadStream();
    expect(stream).not.toBeNull();
    const chunks: Buffer[] = [];
    for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
    expect(Buffer.concat(chunks).toString()).toBe('Title,Authors,Series,Tags,Rating,Read,Formats,Date added\r\n');
  } finally { release(); }
});

test('Classic advanced exports retain the first tab criteria after another search', async ({ secondaryUser }, info) => {
  const { page } = secondaryUser;
  await page.setViewportSize(info.project.use.viewport || { width: 1280, height: 800 });
  async function search(tab: typeof page, title: string) {
    await tab.goto('/advsearch');
    await tab.locator('#title').fill(title);
    await tab.locator('form').filter({ has: tab.locator('#title') })
      .getByRole('button', { name: /Search/ }).click();
    await tab.waitForLoadState('load');
    await expect(tab.locator('.book-list-export')).toBeVisible();
  }
  async function csv() {
    const downloaded = page.waitForEvent('download');
    await page.locator('.book-list-export').getByRole('button', { name: 'Export CSV', exact: true }).click();
    const stream = await (await downloaded).createReadStream();
    expect(stream).not.toBeNull();
    const chunks: Buffer[] = [];
    for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
    return Buffer.concat(chunks).toString('utf8');
  }
  await search(page, 'the');
  const original = await csv();
  expect(original).toContain('Title,Authors,Series,Tags,Rating,Read,Formats,Date added');
  const other = await page.context().newPage();
  try {
    await search(other, 'no matching second tab export phrase');
    expect(await csv()).toBe(original);
  } finally { await other.close(); }
});
