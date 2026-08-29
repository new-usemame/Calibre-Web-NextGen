import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  getPrimaryReadTarget, getReaderContentUrl, isReadableFormat, SERVER_READABLE_FORMATS,
} from '../../src/lib/readerTarget.ts';

const SPA_FORMATS = ['epub', 'kepub'];

function requireCapture(source: string, pattern: RegExp, description: string): string {
  const match = source.match(pattern);
  assert.ok(match?.[1], `Could not find ${description} in the server reader implementation`);
  return match[1];
}

function stringLiterals(source: string): string[] {
  return [...source.matchAll(/['"]([^'"]+)['"]/g)].map((match) => match[1]);
}

function serverReaderFormats(): string[] {
  const constantsSource = readFileSync(new URL('../../../cps/constants.py', import.meta.url), 'utf8');
  const webSource = readFileSync(new URL('../../../cps/web.py', import.meta.url), 'utf8');
  const readBookStart = webSource.indexOf('def read_book(book_id, book_format):');
  const readBookEnd = webSource.indexOf('\n@web.route("/book/<int:book_id>")', readBookStart);
  assert.notEqual(readBookStart, -1, 'Could not find read_book in cps/web.py');
  assert.notEqual(readBookEnd, -1, 'Could not find the end of read_book in cps/web.py');
  const readBookSource = webSource.slice(readBookStart, readBookEnd);

  const epubFamily = stringLiterals(requireCapture(
    readBookSource,
    /if book_format\.lower\(\) in \(([^)]+)\):\n\s+log\.debug\("Start epub reader/,
    'the EPUB-family reader branch',
  ));
  const singleFormatReaders = [...readBookSource.matchAll(
    /elif book_format\.lower\(\) == "([^"]+)":\n\s+log\.debug\("Start (?:pdf|txt) reader/g,
  )].map((match) => match[1]);
  assert.deepEqual(singleFormatReaders.sort(), ['pdf', 'txt']);
  const djvuFamily = stringLiterals(requireCapture(
    readBookSource,
    /elif book_format\.lower\(\) in \[([^\]]+)\]:\n\s+log\.debug\("Start djvu reader/,
    'the DJVU reader branch',
  ));
  const audioFormats = stringLiterals(requireCapture(
    constantsSource,
    /^EXTENSIONS_AUDIO\s*=\s*\{([^}]+)\}/m,
    'EXTENSIONS_AUDIO',
  ));
  assert.match(
    readBookSource,
    /for fileExt in constants\.EXTENSIONS_AUDIO:[\s\S]*?render_title_template\('listenmp3\.html'/,
    'read_book must render every EXTENSIONS_AUDIO entry',
  );
  const comicFormats = stringLiterals(requireCapture(
    readBookSource,
    /for fileExt in \[([^\]]+)\]:\n\s+if book_format\.lower\(\) == fileExt:/,
    'the comic reader branch',
  ));

  return [...new Set([
    ...epubFamily, ...singleFormatReaders, ...djvuFamily, ...audioFormats, ...comicFormats,
  ])];
}

const SERVER_FORMATS = serverReaderFormats().filter((format) => !SPA_FORMATS.includes(format));

describe('reader targets honor the viewer role across every supported format', () => {
  test('server-backed formats stay aligned with the Classic read_book capability', () => {
    assert.deepEqual([...SERVER_READABLE_FORMATS].sort(), [...SERVER_FORMATS].sort());
  });

  test('viewer role opens SPA and server-backed formats', () => {
    for (const format of SPA_FORMATS) {
      assert.equal(getPrimaryReadTarget(197, [format], true), '/read/197');
      assert.equal(isReadableFormat(format), true);
    }
    for (const format of SERVER_FORMATS) {
      assert.equal(getPrimaryReadTarget(197, [format], true), `/view/197/${format}`);
      assert.equal(isReadableFormat(format), true);
    }
  });

  test('without viewer role no readable format produces a target', () => {
    for (const format of [...SPA_FORMATS, ...SERVER_FORMATS]) {
      assert.equal(getPrimaryReadTarget(197, [format], false), null);
    }
  });

  test('unsupported download-only formats never become read targets', () => {
    for (const format of ['mobi', 'azw3', 'cb7', 'aac']) {
      assert.equal(getPrimaryReadTarget(197, [format], true), null);
      assert.equal(isReadableFormat(format), false);
    }
  });
});

describe('EPUB archive targets remain viewer-gated across rolling deployments', () => {
  test('uses the content URL supplied by a current API', () => {
    assert.equal(getReaderContentUrl(197, 'EPUB', '/show/197/epub'), '/show/197/epub');
  });

  test('derives the same viewer route when an older API omits content_url', () => {
    assert.equal(getReaderContentUrl(197, 'EPUB'), '/show/197/epub');
    assert.equal(getReaderContentUrl(198, 'KEPUB', ''), '/show/198/kepub');
  });
});
