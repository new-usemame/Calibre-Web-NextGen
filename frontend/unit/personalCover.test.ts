import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('book detail routes cover changes through the cover editor only', () => {
  const detail = source('../src/pages/BookDetail.tsx');
  const api = source('../src/lib/api.ts');

  assert.match(api, /using_my_cover\?: boolean/);
  assert.match(api, /library_cover_url\?: string \| null/);
  // The direct action and gear-menu item both route to the same editor.
  assert.match(detail, /href=\{`\/book\/\$\{book\.id\}\/cover`\}[\s\S]*data-testid="edit-cover-action"/);
  assert.match(detail, /id: 'edit-cover',[\s\S]*to: `\/book\/\$\{book\.id\}\/cover`/);
  // The old per-scope links and the private-cover hint moved into the editor.
  assert.doesNotMatch(detail, /cover\?personal=1/);
  assert.doesNotMatch(detail, /Use my own cover|Change my cover|Use the library cover|Change library cover/);
  assert.doesNotMatch(detail, /book-cover-preferences/);
});

test('cover editor owns both scopes and the personal flow', () => {
  const picker = source('../src/pages/CoverPicker.tsx');

  // Scope switch near the top; initial scope still honours ?personal=1 links.
  assert.match(picker, /new URLSearchParams\(window\.location\.search\).*personal/);
  assert.match(picker, /data-testid="cover-scope-switch"/);
  assert.match(picker, /aria-pressed=\{!personal\}[\s\S]*switchScope\('library'\)/);
  assert.match(picker, /aria-pressed=\{personal\}[\s\S]*switchScope\('personal'\)/);
  // Library scope is edit-role only; everyone else is forced to personal.
  assert.match(picker, /canEditLibrary = !!\(me\?\.role\?\.edit \|\| me\?\.role\?\.admin\)/);
  assert.match(picker, /personal = scope === 'personal' \|\| !canEditLibrary/);
  // The personal flow that used to live on the book page retains the
  // "back to the library cover" reset and private-scope API state.
  assert.match(picker, /book\.using_my_cover[\s\S]*t\('Use the library cover'\)/);
  assert.match(picker, /useClearMyCover/);
  assert.match(picker, /canEditLibrary && <ApiKeysPanel/);
});

test('personal picker writes only through the my-cover API', () => {
  const pickerApi = source('../src/lib/coverPicker.ts');

  assert.match(pickerApi, /personalBase = .*\/api\/v1\/books\/\$\{id\}\/my-cover/);
  assert.match(pickerApi, /personal \? 'PUT' : 'POST'/);
  assert.match(pickerApi, /scope=personal/);
});

test('edit metadata offers no cover-change surface of its own', () => {
  const edit = source('../src/pages/EditBook.tsx');

  assert.match(edit, /t\('Open cover editor'\)/);
  assert.match(edit, /\/cover\?origin=edit/);
  assert.doesNotMatch(edit, /Upload image|paste an image URL|More cover options/);
  assert.doesNotMatch(edit, /type="file"/);
});
