import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('book detail exposes private and library cover choices separately', () => {
  const detail = source('../src/pages/BookDetail.tsx');
  const api = source('../src/lib/api.ts');

  assert.match(api, /using_my_cover\?: boolean/);
  assert.match(api, /library_cover_url\?: string \| null/);
  assert.match(detail, /\/cover\?personal=1/);
  assert.match(detail, /book\.using_my_cover \? t\('Change my cover'\) : t\('Use my own cover'\)/);
  assert.match(detail, /t\('Use the library cover'\)/);
  assert.match(detail, /me\?\.role\?\.edit[\s\S]*t\('Change library cover'\)/);
});

test('personal picker writes only through the my-cover API', () => {
  const pickerApi = source('../src/lib/coverPicker.ts');
  const picker = source('../src/pages/CoverPicker.tsx');

  assert.match(pickerApi, /personalBase = .*\/api\/v1\/books\/\$\{id\}\/my-cover/);
  assert.match(pickerApi, /personal \? 'PUT' : 'POST'/);
  assert.match(pickerApi, /scope=personal/);
  assert.match(picker, /new URLSearchParams\(window\.location\.search\).*personal/);
  assert.match(picker, /only to you and on books delivered to your e-readers/);
  assert.match(picker, /!personal && <ApiKeysPanel/);
});
