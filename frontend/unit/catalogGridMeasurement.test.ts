import assert from 'node:assert/strict';
import test from 'node:test';
import { measureCatalogColumnCount } from '../src/lib/catalogGridMeasurement.ts';

test('rejects Safari transient one-track serialization at the live catalog width', () => {
  assert.equal(measureCatalogColumnCount({
    gridTemplateColumns: '142px',
    gridWidth: 1248,
    minColumnWidth: 140,
    columnGap: 16,
  }), null);
});

test('zero-width and below-minimum one-track reads are unmeasured', () => {
  const measurements = [0, 60].map((gridWidth) => measureCatalogColumnCount({
    gridTemplateColumns: '140px',
    gridWidth,
    minColumnWidth: 140,
    columnGap: 16,
  }));

  assert.deepEqual(measurements, [null, null]);
});

test('a laid-out follow-up self-heals an initially unmeasured grid', () => {
  const nextFrame = measureCatalogColumnCount({
    gridTemplateColumns: '167.2px 167.2px 167.2px 167.2px 167.2px',
    gridWidth: 900,
    minColumnWidth: 140,
    columnGap: 16,
  });

  assert.equal(nextFrame, 5);
});

test('resolved healthy templates preserve five and eight track layouts', () => {
  assert.equal(measureCatalogColumnCount({
    gridTemplateColumns: '180.8px 180.8px 180.8px 180.8px 180.8px',
    gridWidth: 1000,
    minColumnWidth: 180,
    columnGap: 24,
  }), 5);
  assert.equal(measureCatalogColumnCount({
    gridTemplateColumns: '142px 142px 142px 142px 142px 142px 142px 142px',
    gridWidth: 1248,
    minColumnWidth: 140,
    columnGap: 16,
  }), 8);
});

test('a real one-column grid at 150px remains valid', () => {
  assert.equal(measureCatalogColumnCount({
    gridTemplateColumns: '150px',
    gridWidth: 150,
    minColumnWidth: 140,
    columnGap: 16,
  }), 1);
});

test('fixed mobile tracks use their zero CSS minimum after the breakpoint override', () => {
  assert.equal(measureCatalogColumnCount({
    gridTemplateColumns: '67px 67px',
    gridWidth: 150,
    minColumnWidth: 0,
    columnGap: 16,
  }), 2);
});

test('fixed tracks remain measurable when the CSS minimum is absent', () => {
  assert.equal(measureCatalogColumnCount({
    gridTemplateColumns: '67px 67px',
    gridWidth: 150,
    columnGap: 16,
  }), 2);
});

test('genuine WebKit layouts stay measurable on both sides of a track threshold', () => {
  assert.equal(measureCatalogColumnCount({
    gridTemplateColumns: '162.25px 162.25px 162.25px 162.25px 162.25px 162.25px 162.25px',
    gridWidth: 1231.75,
    minColumnWidth: 140,
    columnGap: 16,
  }), 7);
  assert.equal(measureCatalogColumnCount({
    gridTemplateColumns: '154.1875px 154.1875px 154.1875px 154.1875px 154.1875px 154.1875px 154.1875px 154.1875px',
    gridWidth: 1233.5,
    minColumnWidth: 140,
    columnGap: 16,
  }), 8);
});

test('rejects a one-track undercount at and above the eight-track threshold', () => {
  const transientCounts = [
    { gridWidth: 1232, trackWidth: '162.285714px' },
    { gridWidth: 1233.5, trackWidth: '162.5px' },
  ].map(({ gridWidth, trackWidth }) => measureCatalogColumnCount({
    gridTemplateColumns: Array(7).fill(trackWidth).join(' '),
    gridWidth,
    minColumnWidth: 140,
    columnGap: 16,
  }));

  assert.deepEqual(transientCounts, [null, null]);
});
