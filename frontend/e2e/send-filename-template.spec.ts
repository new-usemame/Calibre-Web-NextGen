import {test, expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import {assertNoHorizontalOverflow,assertNoPageErrors} from './utils';

// Actual Admin form with isolated mail-setting transport. Never changes a real
// SMTP destination, password or household setting, and never sends a message.
for(const theme of ['light','dark']) {
  test(`attachment filename template saves, rejects and resets (${theme})`,async({page})=>{
    const writes:Record<string,unknown>[]=[];
    let cfg={mail_server:'smtp.example.invalid',mail_port:25,mail_use_ssl:0,
      mail_login:'',mail_from:'library@example.invalid',mail_size_mb:25,
      mail_server_type:0,has_password:true,mail_filename_template:''};
    await page.route('**/api/v1/admin/mailsettings',async route=>{
      if(route.request().method()==='POST') {
        const body=route.request().postDataJSON();writes.push(body);
        if(body.mail_filename_template==='{title.__class__}')return route.fulfill({status:400,
          json:{error:{code:'invalid_request',message:'Use a valid attachment filename template with supported metadata fields.'}}});
        cfg={...cfg,...body};
      }
      return route.fulfill({json:cfg});
    });
    const errors:string[]=[];const rejectedConsole:{text:string,url:string}[]=[];
    page.on('pageerror',error=>errors.push('pageerror: '+error.message));
    page.on('console',message=>{if(message.type()==='error') {
      const text=message.text(),url=message.location().url;
      if(text.includes('400 (Bad Request)') && url.endsWith('/api/v1/admin/mailsettings'))
        rejectedConsole.push({text,url});
      else errors.push('console.error: '+text);
    }});
    await page.goto('/app/admin#email-settings');
    await page.evaluate(value=>document.documentElement.setAttribute('data-theme',value),theme);
    const form=page.locator('#email-settings');
    const template=form.getByRole('textbox',{name:'eReader attachment filename template',exact:true});
    await expect(template).toBeVisible();await expect(template).toHaveValue('');
    await expect(template).toHaveAttribute('aria-describedby','mail-filename-help mail-filename-conditional');
    await template.fill('{series} #{series_index} - {title}');await template.press('Enter');
    await expect(form.getByRole('status')).toHaveText('Email settings saved.');
    await expect.poll(()=>writes.length).toBe(1);
    expect(writes[0].mail_filename_template).toBe('{series} #{series_index} - {title}');
    expect(writes[0]).not.toHaveProperty('mail_password');
    await page.reload();await expect(template).toHaveValue('{series} #{series_index} - {title}');
    await page.evaluate(value=>document.documentElement.setAttribute('data-theme',value),theme);
    await template.fill('{title.__class__}');
    const rejection=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/admin/mailsettings'
      && response.request().method()==='POST'
      && response.request().postDataJSON().mail_filename_template==='{title.__class__}');
    await template.press('Enter');expect((await rejection).status()).toBe(400);
    await expect(form.getByRole('status')).toHaveText('Use a valid attachment filename template with supported metadata fields.');
    await expect(template).toHaveValue('{title.__class__}');
    expect(cfg.mail_filename_template).toBe('{series} #{series_index} - {title}');
    await assertNoHorizontalOverflow(page);
    const axe=await new AxeBuilder({page}).include('#email-settings').analyze();
    expect(axe.violations.filter(item=>['critical','serious'].includes(item.impact??''))).toEqual([]);
    await page.screenshot({path:test.info().outputPath(`mail-template-${theme}.jpg`),type:'jpeg',quality:75});
    await template.fill('');await template.press('Tab');
    await expect(form.getByRole('button',{name:'Save email settings',exact:true})).toBeFocused();
    await page.keyboard.press('Enter');await expect(form.getByRole('status')).toHaveText('Email settings saved.');
    await expect.poll(()=>cfg.mail_filename_template).toBe('');
    await page.reload();await expect(template).toHaveValue('');
    // Chromium reports the intentionally asserted failed fetch in the console;
    // keep all other page/console errors and reject duplicate mail rejections.
    expect(writes.filter(write=>write.mail_filename_template==='{title.__class__}')).toHaveLength(1);
    expect(rejectedConsole.length).toBeLessThanOrEqual(1);
    assertNoPageErrors(errors);
  });
}
