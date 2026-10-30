import {test, expect, type Page} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// Actual Admin form with isolated mail transport (#2771). Never changes a real
// SMTP destination or password, and never sends a message.
async function install(page: Page, start: Record<string, unknown>) {
  const writes: Record<string, unknown>[] = [];
  const tests: number[] = [];
  let cfg: Record<string, unknown> = {
    mail_server: 'smtp.example.invalid', mail_port: 587, mail_use_ssl: 1, mail_login: 'me@example.invalid',
    mail_from: 'me@example.invalid', mail_size_mb: 25, mail_filename_template: '',
    mail_server_type: 1, has_password: true, mail_configured: false, gmail_authorised: false, ...start,
  };
  await page.route('**/api/v1/admin/mailsettings', route => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      writes.push(body);
      cfg = {...cfg, ...body, mail_configured: Number(body.mail_server_type) === 0 || cfg.gmail_authorised === true};
    }
    return route.fulfill({json: cfg});
  });
  await page.route('**/api/v1/admin/mailsettings/test', route => {
    tests.push(writes.length);
    return route.fulfill({status: 202, json: {ok: true, email: 'admin@example.invalid', message: 'queued'}});
  });
  return {writes, tests};
}

test('Gmail OAuth with no token says mail is off and offers standard SMTP (#2771)', async ({page}) => {
  const rig = await install(page, {});
  await page.goto('/app/admin#email-settings');
  const form = page.locator('#email-settings');
  await expect(form.getByTestId('mail-status')).toContainText('not authorised');
  await expect(form.getByTestId('mail-gmail-notice')).toBeVisible();
  await form.getByRole('button', {name: 'Use standard SMTP instead'}).click();
  await expect(form.getByTestId('mail-gmail-notice')).toHaveCount(0);
  await expect(form.getByTestId('mail-smtp-pending')).toBeVisible();
  await form.getByRole('button', {name: 'Save email settings'}).click();
  await expect.poll(() => rig.writes.map(w => w.mail_server_type)).toEqual([0]);
  await expect(form.getByTestId('mail-status')).toHaveText('Mail is configured and ready to send.');
  await expect(form.getByTestId('mail-smtp-pending')).toHaveCount(0);
  const axe = await new AxeBuilder({page}).include('#email-settings').analyze();
  expect(axe.violations).toEqual([]);
});

test('saving other settings does not switch a working Gmail OAuth setup to SMTP (#2771)', async ({page}) => {
  const rig = await install(page, {gmail_authorised: true, mail_configured: true});
  await page.goto('/app/admin#email-settings');
  const form = page.locator('#email-settings');
  await expect(form.getByTestId('mail-status')).toHaveText('Mail is configured and ready to send.');
  await form.getByRole('button', {name: 'Save email settings'}).click();
  await expect.poll(() => rig.writes.map(w => w.mail_server_type)).toEqual([1]);
});

test('Save and send test email saves first, then queues the test (#2771)', async ({page}) => {
  const rig = await install(page, {mail_server_type: 0, mail_configured: true});
  await page.goto('/app/admin#email-settings');
  const form = page.locator('#email-settings');
  await form.getByRole('button', {name: 'Save and send test email'}).click();
  await expect(form.getByRole('status').filter({hasText: 'admin@example.invalid'}))
    .toContainText('Test email queued for admin@example.invalid');
  expect(rig.writes).toHaveLength(1);
  expect(rig.tests).toEqual([1]);
});

test('a failed test email shows the server reason and does not claim success (#2771)', async ({page}) => {
  await install(page, {mail_server_type: 0, mail_configured: true});
  await page.route('**/api/v1/admin/mailsettings/test', route => route.fulfill({status: 409,
    json: {error: {code: 'email_required', message: 'Set your own email address on the Account page first'}}}));
  await page.goto('/app/admin#email-settings');
  const form = page.locator('#email-settings');
  await form.getByRole('button', {name: 'Save and send test email'}).click();
  await expect(form).toContainText('Set your own email address on the Account page first');
  await expect(form).not.toContainText('Test email queued');
});
