import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('every Global Library card links to the shared book detail page', () => {
  const globalLibrary = source('../src/pages/GlobalLibrary.tsx');
  const card = source('../src/components/BookCard.tsx');

  assert.match(globalLibrary, /<BookCard[\s\S]*detailsEnabled[\s\S]*canRead=\{owned/);
  assert.doesNotMatch(globalLibrary, /detailsEnabled=\{owned\}/);
  assert.match(card, /detailsEnabled \? \([\s\S]*href=\{`\/book\/\$\{book\.id\}`\}/);
});

test('non-member detail keeps global editing and hides member-only controls', () => {
  const detail = source('../src/pages/BookDetail.tsx');
  const queries = source('../src/lib/queries.ts');

  assert.match(detail, /const inLibrary = !!book && \(!selectionMode \|\| book\.in_my_library !== false\)/);
  assert.match(detail, /!inLibrary && selectionMode && me\?\.role\?\.browse_global[\s\S]*Add to my library/);
  assert.match(detail, /\{inLibrary && book\.in_progress && \(/);
  assert.match(detail, /\{inLibrary && \([\s\S]*<AddToShelf/);
  assert.match(detail, /\{inLibrary && <Link href=\{`\/book\/\$\{book\.id\}\/annotations`\}/);
  assert.match(detail, /\{me\?\.role\?\.edit && \([\s\S]*href=\{`\/book\/\$\{book\.id\}\/edit`\}/);
  assert.match(detail, /const canDelete = canDeleteBooks\(me\)/);

  assert.match(detail, /useBookShelves\(id, \{ enabled: inLibrary \}\)/);
  assert.match(detail, /useShelves\(\{ enabled: inLibrary \}\)/);
  assert.match(queries, /export function useShelves\(options\?: \{ enabled\?: boolean \}\)/);
  assert.match(queries, /export function useBookShelves\(bookId: string \| number, options\?: \{ enabled\?: boolean \}\)/);
});
