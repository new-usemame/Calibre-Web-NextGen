/*
 * The native reader's scroll container must be reachable by keyboard.
 *
 * MEASURED DEFECT (F-f8fdc9, 2026-08-23). `NativeReader.module.css` pins the
 * reader shell with `position: fixed; inset: 0`, so the DOCUMENT does not
 * scroll -- the inner `.body` div does. That div had no `tabindex` and, in the
 * TXT branch, no focusable descendants either: the content is a plain <pre>.
 * A scrollable div with neither is keyboard-unreachable in engines that do not
 * implement keyboard-focusable scrollers. Firefox does; Chrome added it in 127;
 * Safari does not -- i.e. every browser on iPadOS, which is the platform the
 * PDF path was specifically fixed for in #1584. There, a keyboard user could
 * not scroll a long text file at all. Classic has no such hole: it binds arrow
 * keys explicitly in `cps/static/js/reading/txt_reader.js`.
 *
 * Only the TXT branch needs this. PDF renders an <iframe>, audio renders
 * <audio controls>, and the comic viewer renders prev/next buttons -- each is
 * already focusable, so making the container a tab stop there would add a
 * pointless stop rather than fix anything.
 *
 * WHAT THIS DOES NOT CATCH, stated plainly so the green is not read as more
 * than it is:
 *   - It is a TEXT SCAN, not a renderer and not a browser. The behavioral
 *     focus-and-scroll claim is pinned separately by
 *     e2e/native-reader-keyboard-scroll.spec.ts in the WebKit-only project.
 *   - It pins the fix's shape, so a refactor that keeps the behaviour by other
 *     means (say, moving the scroll to the document) fails here spuriously.
 *     That is the trade for having any executable guard at all; treat a failure
 *     as "re-check the keyboard path", not as "you broke it".
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(path.join(HERE, '../../src/pages/NativeReader.tsx'), 'utf8');
const CSS = readFileSync(path.join(HERE, '../../src/pages/NativeReader.module.css'), 'utf8');
const PLAYWRIGHT = readFileSync(path.join(HERE, '../../playwright.config.ts'), 'utf8');
const WORKFLOW = readFileSync(path.join(HERE, '../../../.github/workflows/tests.yml'), 'utf8');

test('the reader shell still relies on an inner scroll container', () => {
  // The premise of the whole finding. If either of these stops being true the
  // document scrolls again and the tab stop below is no longer the right fix.
  assert.match(CSS, /\.shell\s*\{[^}]*position:\s*fixed/, 'reader shell is no longer position:fixed');
  assert.match(CSS, /\.body\s*\{[^}]*overflow:\s*auto/, '.body is no longer the scroll container');
});

test('the TXT branch makes the scroll container a focusable, named region', () => {
  const container = SRC.match(/<div\s+className=\{styles\.body\}[\s\S]{0,240}?>/);
  assert.ok(container, 'could not find the styles.body container element');
  const el = container[0];
  assert.match(el, /fmt === 'txt'/, 'the tab stop is not conditioned on the TXT branch');
  assert.match(el, /tabIndex:\s*0/, 'TXT scroll container is not focusable');
  assert.match(el, /'aria-label':/, 'focusable scroll container has no accessible name');
  assert.match(el, /role:\s*'region'/, 'focusable scroll container is not announced as a region');
});

test('focus on the scroll container is visible, not clipped by the fixed shell', () => {
  // The global :focus-visible ring uses outline-offset: 2px, which on a
  // container filling a position:fixed shell draws at the viewport edge and
  // gets clipped. A focus ring nobody can see is a half-fix.
  const rule = CSS.match(/\.body:focus-visible\s*\{[^}]*\}/);
  assert.ok(rule, '.body has no focus-visible style');
  assert.match(rule[0], /outline:/, '.body:focus-visible does not draw an outline');
  assert.match(rule[0], /outline-offset:\s*-/, 'focus ring is not inset, so it clips at the shell edge');
});

test('the behavioral regression stays routed through WebKit in CI', () => {
  assert.match(PLAYWRIGHT, /name:\s*'webkit-reader'/, 'the focused WebKit project is missing');
  assert.match(PLAYWRIGHT, /testMatch:\s*WEBKIT_READER_SPEC/, 'the reader spec is not scoped to WebKit');
  assert.match(PLAYWRIGHT, /devices\['Desktop Safari'\]/, 'the reader project is not using WebKit');
  assert.match(
    WORKFLOW,
    /playwright install --with-deps chromium webkit/,
    'CI does not install the WebKit browser required by the reader project',
  );
});
