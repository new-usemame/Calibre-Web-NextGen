import { test, expect } from '@playwright/test';
import { fileURLToPath } from 'node:url';

const policy = {
  enabled: true, min_length: 10, number: true, lower: true,
  upper: true, letter: false, special: true,
};

test('classic password controls preserve values, skip opted-out fields and enhance later inputs once', async ({ page }) => {
  await page.setContent(`<form>
    <label for="secret">SMTP password</label><input id="secret" name="secret" type="password" value="keep-this">
    <input id="reset-password" type="password" disabled><input type="password" readonly>
    <input type="password" data-pw-toggle="off">
    <button type="submit">Save</button></form>`);
  await page.evaluate(() => {
    document.querySelector('form')!.addEventListener('submit', (event) => {
      event.preventDefault();
      document.body.dataset.saved = String(new FormData(event.target as HTMLFormElement).get('secret'));
    });
  });
  await page.addScriptTag({ path: fileURLToPath(new URL('../../cps/static/js/password-toggle.js', import.meta.url)) });
  const input = page.getByLabel('SMTP password', { exact: true });
  const toggle = page.getByRole('button', { name: /^(Show|Hide) password$/ });
  await expect(toggle).toHaveCount(1);
  await toggle.focus();
  await page.keyboard.press('Space');
  await expect(input).toHaveAttribute('type', 'text');
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(toggle).toHaveAccessibleName('Hide password');
  await expect(input).toHaveValue('keep-this');
  await expect(page.locator('body')).not.toHaveAttribute('data-saved');
  await page.keyboard.press('Enter');
  await expect(input).toHaveAttribute('type', 'password');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.locator('body')).toHaveAttribute('data-saved', 'keep-this');
  await page.evaluate(() => {
    const input = document.createElement('input');
    input.type = 'password';
    document.querySelector('form')!.append(input);
  });
  await expect(page.getByRole('button', { name: 'Show password', exact: true })).toHaveCount(2);
  await expect(page.locator('.cwng-pw-wrap .cwng-pw-wrap')).toHaveCount(0);
  // Classic Content Server's Reset Password action enables an existing field.
  await page.locator('#reset-password').evaluate((input: HTMLInputElement) => { input.disabled = false; });
  await expect(page.getByRole('button', { name: 'Show password', exact: true })).toHaveCount(3);
});

test('new-user password keeps its label and hint while revealing, editing and submitting', async ({ page }) => {
  let submitted: Record<string, unknown> | undefined;
  // Keep the test independent of server-wide policy and never create a real account.
  await page.route('**/api/v1/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/auth/me')) return route.fulfill({ json: {
      id: 1, name: 'admin', locale: 'en', role: { admin: true },
    } });
    if (path.endsWith('/auth/csrf')) return route.fulfill({ json: { csrf_token: 'test' } });
    if (path.endsWith('/account')) return route.fulfill({ json: { password_policy: policy } });
    if (path.endsWith('/admin/users')) {
      if (route.request().method() === 'POST') {
        submitted = route.request().postDataJSON();
        return route.fulfill({ status: 201, json: { id: 2, name: submitted!.name } });
      }
      return route.fulfill({ json: { items: [] } });
    }
    return route.fulfill({ status: 404, json: { error: { code: 'unused_fixture' } } });
  });
  await page.goto('/app/admin');
  await page.getByRole('button', { name: 'New user', exact: true }).click();
  const password = page.getByLabel('Password', { exact: true });
  await expect(password).toHaveCount(1);
  await expect(password).toHaveAccessibleDescription(/at least 10 characters.*a number.*a lowercase letter.*an uppercase letter.*a special character/);
  await page.getByLabel('Username', { exact: true }).fill('password-probe');
  await password.fill('Original123!');
  const toggle = page.getByRole('button', { name: /^(Show|Hide) password$/ });
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(password).toHaveAttribute('type', 'text');
  await expect(toggle).toHaveAccessibleName('Hide password');
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  expect(submitted).toBeUndefined();
  await password.fill('Edited123!');
  await toggle.click();
  await expect(password).toHaveAttribute('type', 'password');
  await expect(password).toHaveValue('Edited123!');
  await page.getByRole('button', { name: 'Create user', exact: true }).click();
  await expect.poll(() => submitted).toMatchObject({ name: 'password-probe', password: 'Edited123!' });
});

for (const enabled of [true, false]) {
  test(`account password toggles preserve independent values with policy ${enabled ? 'enabled' : 'disabled'}`, async ({ page }) => {
    await page.route('**/api/v1/**', (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith('/auth/me')) return route.fulfill({ json: {
        id: 2, name: 'reader', locale: 'en', role: { passwd: true },
      } });
      if (path.endsWith('/account')) return route.fulfill({ json: {
        name: 'reader', email: '', kindle_mail: '', kindle_mail_subject: '', mail_body_text: null,
        locale: 'en', default_language: 'all', role: { passwd: true }, can_change_password: true,
        locales: [], languages: [], app_passwords: [], password_policy: { ...policy, enabled },
      } });
      return route.fulfill({ status: 404, json: { error: { code: 'unused_fixture' } } });
    });
    await page.goto('/app/account');
    const current = page.getByLabel('Current password', { exact: true });
    const next = page.getByLabel('New password', { exact: true });
    const confirm = page.getByLabel('Confirm new password', { exact: true });
    await current.fill('Current123!');
    await next.fill('Next123!');
    await confirm.fill('Next123!');
    if (enabled) {
      await expect(next).toHaveAccessibleDescription(/at least 10 characters.*a special character/);
    } else {
      await expect(next).not.toHaveAttribute('aria-describedby');
      await expect(page.getByText(/^Password requirements:/)).toHaveCount(0);
    }
    const form = page.locator('form').filter({ has: next });
    await form.getByRole('button', { name: 'Show password', exact: true }).nth(1).click();
    await expect(next).toHaveAttribute('type', 'text');
    await expect(current).toHaveAttribute('type', 'password');
    await expect(confirm).toHaveAttribute('type', 'password');
    await expect(next).toHaveValue('Next123!');
    await form.getByRole('button', { name: 'Hide password', exact: true }).click();
    await expect(next).toHaveAttribute('type', 'password');
  });
}

test('a shown classic password is hidden again when its form submits', async ({ page }) => {
  await page.setContent(`<form><label for="smtp">SMTP password</label>
    <input id="smtp" name="smtp" type="password" value="keep-this"><button type="submit">Save</button></form>`);
  await page.evaluate(() => {
    document.querySelector('form')!.addEventListener('submit', (event) => {
      event.preventDefault();
      const input = document.getElementById('smtp') as HTMLInputElement;
      document.body.dataset.typeAtSubmit = input.type;
      document.body.dataset.saved = String(new FormData(event.target as HTMLFormElement).get('smtp'));
    });
  });
  await page.addScriptTag({ path: fileURLToPath(new URL('../../cps/static/js/password-toggle.js', import.meta.url)) });
  const input = page.getByLabel('SMTP password', { exact: true });
  await page.getByRole('button', { name: 'Show password' }).click();
  await expect(input).toHaveAttribute('type', 'text');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('body')).toHaveAttribute('data-type-at-submit', 'password');
  await expect(page.locator('body')).toHaveAttribute('data-saved', 'keep-this');
  await expect(page.getByRole('button', { name: 'Show password' })).toHaveAttribute('aria-pressed', 'false');
});
