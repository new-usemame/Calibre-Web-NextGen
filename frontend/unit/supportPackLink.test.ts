import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import type { Me } from '../src/lib/api.ts';
import { canDownloadSupportPack, SUPPORT_PACK_URL } from '../src/lib/permissions.ts';

function account(role: Record<string, boolean>): Me {
  return { id: 1, name: 'support-probe', locale: 'en', theme: 'light', role };
}

function source(path: string): string {
  return readFileSync(new URL(path, import.meta.url), 'utf8');
}

test('only an administrator is offered the support pack', () => {
  assert.equal(canDownloadSupportPack(account({ admin: true })), true);
  assert.equal(canDownloadSupportPack(account({ admin: false, download: true, viewer: true })), false);
  assert.equal(canDownloadSupportPack(account({})), false);
  assert.equal(canDownloadSupportPack(undefined), false);
  assert.equal(canDownloadSupportPack(null), false);
});

test('the link targets the sanitised support pack endpoint, not the unredacted debug pack', () => {
  assert.equal(SUPPORT_PACK_URL, '/admin/debug/support-pack');
  assert.doesNotMatch(SUPPORT_PACK_URL, /^\/admin\/debug$/);
});

test('the endpoint the link points at exists on the server and is admin-only', () => {
  const admin = source('../../cps/admin.py');
  const route = /@admi\w*\.route\("\/admin\/debug\/support-pack"[^\n]*\)\s*\n\s*@user_login_required\s*\n\s*@admin_required/;
  assert.match(admin, route);
});

test('Help menu shows the item only behind the predicate, as a download with no new tab', () => {
  const topBar = source('../src/components/TopBar.tsx');
  const item = topBar.match(/canDownloadSupportPack\(me\) && <MenuItem[\s\S]*?onSelect=\{close\} \/>\}/);
  assert.ok(item, 'Help menu entry gated by canDownloadSupportPack(me)');
  assert.match(item[0], /SUPPORT_PACK_URL/);
  assert.match(item[0], /\bdownload\b/);
  assert.match(item[0], /t\('Get Support Pack'\)/);

  // The download variant must not open a tab or route through the SPA.
  const variant = topBar.match(/if \(href && download\) \{[\s\S]*?\n  \}/);
  assert.ok(variant, 'MenuItem download variant');
  assert.doesNotMatch(variant[0], /target=/);
  assert.match(variant[0], /<a className=\{cls\} href=\{href\} download/);
});
