import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BUILTIN_READER_FONTS, readerFontFaceCss, readerFontFamily, type ReaderFont } from '../src/lib/readerFonts.ts';

const custom: ReaderFont = {
  id: 'custom:71ab1adc-b06d-44b7-8e85-0b5412876899',
  label: 'My serif', family: 'CWNGReaderFont_71ab1adcb06d44b78e850b5412876899',
  builtin: false, url: '/books/api/v1/reader/fonts/71ab1adc-b06d-44b7-8e85-0b5412876899/file', format: 'woff2',
};

test('a saved Literata choice keeps its bundled family when the optional catalog fails', () => {
  assert.equal(readerFontFamily(BUILTIN_READER_FONTS, 'Literata'), "'Literata', serif");
});

test('chapter font CSS preserves the app mount prefix and never uses the display name', () => {
  const css = readerFontFaceCss([{ ...custom, label: "Font');body{display:none}" }], 'https://library.example');
  assert.ok(css.includes('https://library.example/books/api/v1/reader/fonts/71ab1adc-b06d-44b7-8e85-0b5412876899/file'));
  assert.ok(css.includes(`font-family:${custom.family};`));
  assert.ok(!css.includes('display:none'));
});

test('chapter styles reject external font URLs, unsafe families and unrecognised font formats', () => {
  for (const font of [
    { ...custom, url: 'https://external.example/font.woff2' },
    { ...custom, url: 'javascript:alert(1)' },
    { ...custom, family: "Serif';}body{display:none}" },
    { ...custom, format: "woff2');}body{display:none}" },
  ]) assert.equal(readerFontFaceCss([font], 'https://library.example'), '');
});

test('saved custom IDs resolve through the current catalog and deleted entries fall back to the book', () => {
  const items = [{ id: 'default', label: 'Book default', family: '', builtin: true }, custom];
  assert.equal(readerFontFamily(items, custom.id), custom.family);
  assert.equal(readerFontFamily(items.filter(font => font.id !== custom.id), custom.id), 'initial');
  assert.equal(readerFontFamily(items, 'default'), 'initial');
});
