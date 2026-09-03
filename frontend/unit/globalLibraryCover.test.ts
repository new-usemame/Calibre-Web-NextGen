import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('global cards pass the serialized cover through regardless of membership', () => {
  const globalLibrary = source('../src/pages/GlobalLibrary.tsx');
  const card = source('../src/components/BookCard.tsx');

  assert.match(globalLibrary, /book=\{book\}[\s\S]*membership=\{owned \? 'owned' : 'unowned'\}/);
  assert.match(card, /<BookCover coverUrl=\{book\.cover_url\}/);

  const coverDeclaration = card.slice(
    card.indexOf('const cover = ('),
    card.indexOf('// dir="auto"'),
  );
  assert.doesNotMatch(
    coverDeclaration,
    /membership[^\n]*(cover_url|null|undefined)/,
    'membership must never erase or replace a global metadata cover',
  );
});
