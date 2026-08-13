import { describe, test } from 'node:test';
import assert from 'node:assert/strict';

import { chapterLabelForHref, splitSearchExcerpt } from '../../src/lib/reader/searchUi.ts';

describe('splitSearchExcerpt', () => {
  test('marks every match case-insensitively while preserving the original text', () => {
    const parts = splitSearchExcerpt('Whale, whale, WHALE.', 'whale');
    assert.equal(parts.map((part) => part.text).join(''), 'Whale, whale, WHALE.');
    assert.deepEqual(parts.filter((part) => part.matched).map((part) => part.text),
      ['Whale', 'whale', 'WHALE']);
  });

  test('treats regex and HTML characters as plain book text', () => {
    assert.deepEqual(splitSearchExcerpt('Use <b>a+b</b>, not ab.', 'a+b'), [
      { text: 'Use <b>', matched: false },
      { text: 'a+b', matched: true },
      { text: '</b>, not ab.', matched: false },
    ]);
  });

  test('returns one unmarked part when the query is blank or absent', () => {
    assert.deepEqual(splitSearchExcerpt('A passage', '  '),
      [{ text: 'A passage', matched: false }]);
    assert.deepEqual(splitSearchExcerpt('A passage', 'missing'),
      [{ text: 'A passage', matched: false }]);
  });
});

describe('chapterLabelForHref', () => {
  const toc = [
    { label: 'Chapter One', href: 'Text/chapter-1.xhtml#opening' },
    { label: '  Chapter Two  ', href: './Text/chapter-2.xhtml' },
    { label: '', href: 'Text/untitled.xhtml' },
  ];

  test('matches a section href to a TOC href while ignoring fragments and base paths', () => {
    assert.equal(chapterLabelForHref('OPS/Text/chapter-1.xhtml', toc), 'Chapter One');
    assert.equal(chapterLabelForHref('Text/chapter-2.xhtml#p3', toc), 'Chapter Two');
  });

  test('falls back to the raw href for unknown or untitled sections', () => {
    assert.equal(chapterLabelForHref('Text/missing.xhtml', toc), 'Text/missing.xhtml');
    assert.equal(chapterLabelForHref('Text/untitled.xhtml', toc), 'Text/untitled.xhtml');
  });
});
