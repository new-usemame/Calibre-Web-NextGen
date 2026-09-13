import assert from 'node:assert/strict';
import test from 'node:test';
import {
  clampDimension, designsEqual, effectiveColors, mergeDesign, normalizeHex, resolvePreset,
  type DesignerCatalogue,
} from '../src/features/coverDesigner/contract.ts';

const catalogue: DesignerCatalogue = {
  styles: [],
  schemes: [
    { id: 'ink', label: 'Ink', builtin: true,
      colors: { background: '#f4efe3', band: '#1f3a5f', title: '#1f3a5f', author: '#f4efe3' } },
    { id: 'ember', label: 'Ember', builtin: true,
      colors: { background: '#fff3e6', band: '#c0392b', title: '#7a2d12', author: '#fff3e6' } },
  ],
  fonts: [],
  presets: [],
  defaults: { style: 'blocks', scheme: 'ink' },
  limits: { min_width: 200, max_width: 2400, min_height: 200, max_height: 2400, font_size_min: 8, font_size_max: 200 },
};

test('mergeDesign: patch wins per field, nested maps merge per slot, scheme:null is a real value', () => {
  const base = { style: 'blocks', scheme: 'ink', fonts: { title: { family: 'serif', size: 64 } } };
  const merged = mergeDesign(base, { fonts: { title: { size: 80 } }, align: { author: 'left' } });
  assert.deepEqual(merged.fonts?.title, { family: 'serif', size: 80 });
  assert.deepEqual(merged.align, { author: 'left' });
  assert.equal(merged.scheme, 'ink');
  const custom = mergeDesign(merged, { scheme: null });
  assert.equal(custom.scheme, null);
  assert.equal(merged.scheme, 'ink'); // inputs are not mutated
});

test('designsEqual ignores key order and undefined keys but not values', () => {
  assert.ok(designsEqual(
    { style: 'blocks', colors: { background: '#fff' } },
    { colors: { background: '#fff' }, style: 'blocks', fonts: undefined },
  ));
  assert.ok(!designsEqual({ scheme: null }, { scheme: 'ink' }));
  assert.ok(designsEqual({}, undefined)); // an absent design IS an empty design
});

test('resolvePreset layers the preset over the catalogue defaults', () => {
  const preset = { id: 'p1', name: 'P1', builtin: true, scope: 'library' as const,
    design: { scheme: 'ember' } };
  assert.deepEqual(resolvePreset(preset, catalogue.defaults), { style: 'blocks', scheme: 'ember' });
});

test('effectiveColors: scheme colours, custom overrides on top, custom-only when scheme is null', () => {
  assert.deepEqual(effectiveColors({ scheme: 'ember' }, catalogue), catalogue.schemes[1].colors);
  assert.deepEqual(
    effectiveColors({ scheme: 'ember', colors: { band: '#000000' } }, catalogue),
    { ...catalogue.schemes[1].colors, band: '#000000' },
  );
  // scheme null: custom colours over the DEFAULT scheme (server rule mirrored)
  assert.deepEqual(
    effectiveColors({ scheme: null, colors: { background: '#123456' } }, catalogue),
    { ...catalogue.schemes[0].colors, background: '#123456' },
  );
});

test('normalizeHex accepts #abc and #aabbcc case-insensitively, rejects the rest', () => {
  assert.equal(normalizeHex('#ABC'), '#aabbcc');
  assert.equal(normalizeHex(' #1F3A5F '), '#1f3a5f');
  assert.equal(normalizeHex('red'), null);
  assert.equal(normalizeHex('#12345'), null);
  assert.equal(normalizeHex(''), null);
});

test('clampDimension rounds and clamps to the catalogue limits', () => {
  assert.equal(clampDimension(1200.4, 200, 2400), 1200);
  assert.equal(clampDimension(10, 200, 2400), 200);
  assert.equal(clampDimension(99999, 200, 2400), 2400);
});
