import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';
const browser = await chromium.launch({channel:'chrome', headless:true});
const page = await browser.newPage({viewport:{width:1000,height:800}});
const base=`http://127.0.0.1:${process.env.RESUME_WEB_PORT}`;
const errors=[]; page.on('pageerror', e=>{errors.push(e.message);console.error('PAGE ERROR:',e.stack);});
const json = async (path, options) => (await page.request.fetch(base+path,options)).json();
const progress = async()=>Number(await page.getByRole('progressbar').getAttribute('aria-valuenow'));
const waitPercent=async(p)=>{
  try { await page.waitForFunction(p=>{
    const bar=document.querySelector('[role=progressbar]');
    return bar && Math.abs(Number(bar.getAttribute('aria-valuenow'))-p)<=2;
  },p,{timeout:30000}); } catch (error) {
    console.log('Seek failed: expected', p, 'observed', await progress().catch(()=>null),
      'bookmark API', JSON.stringify(await json('/api/v1/books/42/bookmark')));
    throw error;
  }
};
try {
  const sync=await json('/kosync/syncs/progress',{method:'PUT',data:{document:'a'.repeat(32),progress:'/body/DocFragment[5]/body/p[5]',percentage:.375,device:'KOReader'}});
  assert.ok(sync.timestamp,JSON.stringify(sync));
  const wire=await json('/api/v1/books/42/bookmark');
  const opened=Date.now();
  await page.goto(base+'/e2e/reader-resume/index.html');
  await waitPercent(37.5);
  assert.equal(wire.resume.percentage,37.5);
  assert.equal(wire.resume.mode,'automatic');
  const range=await page.evaluate(()=>window.visiblePercentageRange());
  console.log('Visible percentage range:', range);
  assert.ok(range[0] <= 37.5 && range[1] >= 37.5, JSON.stringify(range));
  const generation=await page.evaluate(()=>window.locationGenerationMs);
  assert.equal(generation.length,1,'automatic resume must reuse its expensive locations index');
  console.log('locations.generate(1600) ms:', generation);
  console.log('KOReader PUT .375 -> API',JSON.stringify(wire),'-> rendered',await progress(),'% in',Date.now()-opened,'ms');
  assert.equal((await json('/api/v1/books/42/bookmark')).bookmark,null);
  // Read normally to establish an actual CFI; then make a newer device sync.
  await page.getByRole('button',{name:'Next page',exact:true}).first().click();
  await expect.poll(async()=> (await json('/api/v1/books/42/bookmark')).bookmark,{timeout:15000}).not.toBeNull();
  const local=await json('/api/v1/books/42/bookmark');
  assert.equal(local.resume,null);
  await json('/kosync/syncs/progress',{method:'PUT',data:{document:'a'.repeat(32),progress:'/body/DocFragment[9]/body/p[5]',percentage:.72,device:'KOReader'}});
  const offered=await json('/api/v1/books/42/bookmark');
  assert.equal(offered.resume.mode,'offer');
  assert.equal(offered.bookmark,local.bookmark);
  await page.reload();
  const button=page.getByRole('button',{name:'Resume at 72% from another device',exact:true});
  await button.waitFor();
  assert.ok((await progress())<45, 'local position must render before accepting');
  await button.focus(); await page.keyboard.press('Enter');
  await waitPercent(72);
  await page.waitForTimeout(1200); // Allow the real 800ms persistence debounce to expose an unwanted write.
  assert.equal((await json('/api/v1/books/42/bookmark')).bookmark,local.bookmark);
  console.log('Newer remote -> local retained -> keyboard resume at',await progress(),'% -> CFI unchanged');
  await page.setViewportSize({width:390,height:844});
  await page.reload(); await button.waitFor();
  const bounds=await button.boundingBox();
  assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 390);
  await page.getByRole('button',{name:'Dismiss',exact:true}).click();
  assert.equal(await button.count(),0);
  await page.getByRole('button',{name:'Next page',exact:true}).first().click();
  await expect.poll(async()=> (await json('/api/v1/books/42/bookmark')).resume,{timeout:15000}).toBeNull();
  await page.reload(); await page.getByRole('progressbar').waitFor();
  assert.equal(await button.count(),0);
  console.log('Dismiss + browser page turn -> newer local suppresses stale 72%');
  // An explicit chapter choice must also end preview suppression.
  const beforeToc=(await json('/api/v1/books/42/bookmark')).bookmark;
  await json('/kosync/syncs/progress',{method:'PUT',data:{document:'a'.repeat(32),progress:'/body/DocFragment[11]/body/p[5]',percentage:.86,device:'KOReader'}});
  await page.reload();
  await page.getByRole('button',{name:'Resume at 86% from another device',exact:true}).waitFor();
  await page.getByRole('button',{name:'Table of contents',exact:true}).click();
  await page.getByRole('navigation',{name:'Table of contents'}).locator('li button').last().click();
  await expect.poll(async()=> (await json('/api/v1/books/42/bookmark')).bookmark,{timeout:15000}).not.toBe(beforeToc);
  assert.equal(await page.getByRole('button',{name:/Resume at .* from another device/}).count(),0);
  console.log('390px notice fits; chapter selection persists and removes the remote offer');
  assert.deepEqual(errors,[]);
} finally { await browser.close(); }
