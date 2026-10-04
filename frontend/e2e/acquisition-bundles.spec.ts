import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// Presentation fixtures stay account-local and never alter a provider/client.
// Real HTTP admission and full client/processor behavior have separate owning
// API and candidate-image probes; this spec checks users can reach the choices.
const path = '/api/v1/acquisition/jobs/bundle/books';
const longName = 'A very long legal book filename '.repeat(7) + '.epub';
const parent = { id:'bundle', connection_id:'source', title:'Owned download', state:'awaiting_selection', bundle_parent_id:'bundle', bundle_selectable:true, cancel_requested:false };
const candidates = () => [
  { id:'1'.repeat(64), name:longName, format:'EPUB', size:12345 },
  { id:'2'.repeat(64), name:'Second.pdf', format:'PDF', size:23456 },
];
async function fixture(page: Page, options: { paused?:boolean; readError?:boolean; stale?:boolean; selectedCancelled?:boolean; jobsErrorAfterSelection?:boolean } = {}) {
  let reads=0, selected=false; const posts:unknown[]=[];
  await page.route('**/api/v1/auth/me', async route => {
    const response=await route.fetch();await route.fulfill({response,json:{...await response.json(),locale:'en',acquisition_access:true}});
  });
  await page.route('**/api/v1/acquisition**', async route => {
    const u=new URL(route.request().url()); const method=route.request().method();
    if (u.pathname===path && method==='GET') {
      reads++;
      if (options.readError && reads===1) return route.fulfill({status:502,json:{error:{code:'source_unavailable'}}});
      return route.fulfill({json:{generation:reads>1?'refreshed':'initial',candidates:candidates().map((c,i)=> (selected || options.selectedCancelled) && i===0 ? {...c,job_id:'bundle',state:options.selectedCancelled?'cancelled':'imported'} : c)}});
    }
    if (u.pathname===path && method==='POST') {
      posts.push(route.request().postDataJSON());
      if (options.stale && posts.length===1) return route.fulfill({status:409,json:{error:{code:'conflict'}}});
      selected=true; return route.fulfill({status:202,json:{...parent,state:'queued'}});
    }
    if (u.pathname.endsWith('/jobs') && selected && options.jobsErrorAfterSelection) return route.fulfill({status:502,json:{error:{code:'source_unavailable'}}});
    if (u.pathname.endsWith('/jobs')) return route.fulfill({json:{jobs:[{...parent,...(options.selectedCancelled?{state:'cancelled'}:{}),...(selected?{state:'imported',result:{book_ids:[1],disposition:'imported'}}:{})}]}});
    if (u.pathname.endsWith('/acquisition')) return route.fulfill({json:{connections:[],can_acquire:true,runtime:{available:!options.paused,reasons:options.paused?['ingest_service_unavailable']:[]}}});
    return route.fallback();
  });
  await page.goto('/app/find-books');
  return { posts, reads:()=>reads };
}

test('keyboard choice, receipt link, and a later choice from the same bundle',async({page})=>{
  const f=await fixture(page);
  const first=page.getByRole('button',{name:'Import this book: '+longName,exact:true});
  await expect(first).toBeVisible();await expect(page.getByRole('link',{name:'Open book'})).toHaveCount(0);
  await first.focus();await page.keyboard.press('Enter');
  await expect(page.getByRole('link',{name:'Open book'})).toHaveAttribute('href','/app/book/1');
  expect(f.posts).toEqual([{generation:'initial',candidate_id:'1'.repeat(64)}]);
  const disclosure=page.getByRole('button',{name:'Choose another book'});await expect(disclosure).toBeFocused();await disclosure.focus();await page.keyboard.press('Enter');
  await expect(page.getByRole('button',{name:'Hide available books'})).toHaveAttribute('aria-expanded','true');
  await expect(page.getByText('Requested',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Import this book: '+longName,exact:true})).toHaveCount(0);
  const second=page.getByRole('button',{name:'Import this book: Second.pdf',exact:true});await second.focus();await page.keyboard.press('Enter');
  await expect.poll(()=>f.posts.length).toBe(2);
  expect(f.posts[1]).toEqual({generation:'refreshed',candidate_id:'2'.repeat(64)});
});

test('a stale choice refreshes without silently importing another candidate',async({page})=>{
  const f=await fixture(page,{stale:true});await page.getByRole('button',{name:'Import this book: Second.pdf',exact:true}).click();
  await expect(page.getByText('That book choice was out of date. The list was refreshed; choose again.',{exact:true})).toBeVisible();
  expect(f.posts).toHaveLength(1);expect(f.reads()).toBe(2);
  await page.getByRole('button',{name:'Import this book: Second.pdf',exact:true}).click();
  await expect.poll(()=>f.posts.length).toBe(2);expect(f.posts[1]).toEqual({generation:'refreshed',candidate_id:'2'.repeat(64)});
});

test('failed list stays an error, retry recovers, paused requests still allow reading',async({page})=>{
  const f=await fixture(page,{readError:true,paused:true});
  const error = 'The available books could not be loaded. Try again.';
  // The visible paragraph and delayed screen-reader alert intentionally carry
  // the same text. Check their distinct contracts instead of racing the alert.
  await expect(page.getByRole('region',{name:'Your requests'}).getByText(error,{exact:true})).toBeVisible();
  await expect(page.getByRole('alert')).toHaveText(error);
  await expect(page.getByText('No books are available in this download.')).toHaveCount(0);
  await page.getByRole('button',{name:'Try again',exact:true}).click();
  await expect(page.getByText('Second.pdf',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Import this book: Second.pdf',exact:true})).toBeDisabled();expect(f.posts).toEqual([]);
});

test('cancelling the first selected artifact leaves the other choices reachable',async({page})=>{
  await fixture(page,{selectedCancelled:true});const disclosure=page.getByRole('button',{name:'Choose another book'});await expect(disclosure).toBeVisible();await disclosure.click();
  await expect(page.getByText('Requested',{exact:true}).locator('..')).toContainText('Request cancelled');
  await expect(page.getByRole('button',{name:'Import this book: '+longName,exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Import this book: Second.pdf',exact:true})).toBeEnabled();
});

test('long names fit the phone and desktop in both palettes, serious axe gate',async({page})=>{
  await page.emulateMedia({reducedMotion:'reduce'});await fixture(page);
  await expect(page.getByRole('button',{name:'Import this book: Second.pdf',exact:true})).toBeVisible();
  for(const width of [1280,375,320])for(const theme of ['light','dark']){
    await page.setViewportSize({width,height:width>600?800:667});
    await page.evaluate(async t=>{document.documentElement.dataset.theme=t;await new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())));},theme);
    await page.mouse.move(0,0);expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    const scan=await new AxeBuilder({page}).include('main').withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
    expect(scan.violations.filter(v=>['critical','serious'].includes(v.impact??'')).map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)})),`${width}/${theme}`).toEqual([]);
  }
});

 test('successful choice keeps focus and the known queued state when activity refresh fails',async({page})=>{
  const f=await fixture(page,{jobsErrorAfterSelection:true});
  const first=page.getByRole('button',{name:'Import this book: '+longName,exact:true});
  await first.focus();await page.keyboard.press('Enter');
  await expect(page.getByRole('button',{name:'Choose another book'})).toBeFocused();
  await expect(page.getByText('Queued',{exact:true})).toBeVisible();
  await expect(page.getByRole('link',{name:'Open book'})).toHaveCount(0);
  expect(f.posts).toHaveLength(1);
});
