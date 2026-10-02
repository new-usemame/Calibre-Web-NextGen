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
    const box=page.getByRole('group',{name:'Export this book list'});
    await box.getByRole('button',{name:'Export TXT'}).click();
    await expect.poll(()=>started).toBe(true);
    const failed=page.waitForEvent('requestfailed',{predicate:r=>r.url().endsWith('/api/v1/books/export'),timeout:8000});
    await page.getByRole('button',{name:'Read',exact:true}).click();
    await failed;
    release?.();
    await expect(box.getByRole('button',{name:'Export TXT'})).toBeEnabled();
    expect(downloads).toEqual([]);
  } finally { release?.(); }
});
