import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import { resumeCfi } from '../src/lib/readerResume.ts';

test('portable percentages reach epub.js as fractions, including zero and completion', () => {
  const fractions: number[] = [];
  const locations = { cfiFromPercentage: (fraction: number) => { fractions.push(fraction); return 'cfi'; } };
  for (const percentage of [0, 0.5, 37.5, 100]) {
    assert.equal(resumeCfi(locations, { percentage, mode: 'automatic', synced_at: '' }), 'cfi');
  }
  assert.deepEqual(fractions, [0, .005, .375, 1]);
  for (const percentage of [-1, 101, NaN, Infinity, true, '37']) {
    assert.equal(resumeCfi(locations, { percentage: percentage as number, mode: 'offer', synced_at: '' }), undefined);
  }
  assert.equal(fractions.length, 4);
});

test('an exact point wins over a materially different percentage in automatic and offer modes', () => {
  const exact = 'epubcfi(/6/2!/4/2/2[kobo.1.1]/1:0)';
  for (const mode of ['automatic', 'offer'] as const) {
    let approximated = false;
    const target = resumeCfi({ cfiFromPercentage: () => { approximated = true; return 'approximate'; } },
      { percentage: 95, synced_at: '', mode, cfi: exact });
    assert.equal(target, exact);
    assert.equal(approximated, false);
  }
});

test('exact resume belongs to the archive actually opened, including concurrent replacement', async () => {
  const { resumeForArchive } = await import('../src/lib/readerResume.ts');
  const archive = new TextEncoder().encode('same EPUB bytes').buffer;
  const digest = await crypto.subtle.digest('SHA-256', archive);
  const epub_sha256 = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
  const hint = { cfi: 'epubcfi(/6/2!/4/2/1:0)', epub_sha256, percentage: 95, mode: 'automatic' as const, synced_at: '' };
  assert.equal((await resumeForArchive(hint, archive))?.cfi, hint.cfi);
  const changed = await resumeForArchive(hint, new TextEncoder().encode('replacement EPUB').buffer);
  assert.equal(resumeCfi({ cfiFromPercentage: p => `percentage:${p}` }, changed), 'percentage:0.95');
  const fallback = { percentage: 37.5, mode: 'offer' as const, synced_at: '' };
  assert.equal(await resumeForArchive(fallback, archive), fallback);
});

for (const failure of ['null', 'undefined', 'throw'] as const) {
  test(`an exact CFI resolving to ${failure} retains percentage resume in both modes`, async () => {
    const { resumeForArchive } = await import('../src/lib/readerResume.ts');
    const archive = new TextEncoder().encode('same EPUB bytes').buffer;
    const digest = await crypto.subtle.digest('SHA-256', archive);
    const epub_sha256 = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
    for (const mode of ['automatic', 'offer'] as const) {
      const hint = { cfi: 'epubcfi(/6/2!/4/2/999/1:0)', epub_sha256, percentage: 95, mode, synced_at: '' };
      const resolved: string[] = [];
      const result = await resumeForArchive(hint, archive, async cfi => {
        resolved.push(cfi);
        if (failure === 'throw') throw new Error('CFI cannot resolve');
        return failure === 'null' ? null : undefined;
      });
      assert.equal(resumeCfi({ cfiFromPercentage: p => `percentage:${p}` }, result), 'percentage:0.95');
      assert.deepEqual(resolved, [hint.cfi]);
      assert.equal(result?.mode, mode);
      assert.equal(hint.cfi, 'epubcfi(/6/2!/4/2/999/1:0)', 'do not mutate the saved response');
      const valid = await resumeForArchive(hint, archive, async () => ({} as Range));
      assert.equal(valid, hint, 'a resolved range retains exactness');
    }
  });
}

for (const mode of ['automatic', 'offer'] as const) {
  test(`a never-settling range resolver falls back without changing the ${mode} bookmark`, async () => {
    const { resumeForArchive } = await import('../src/lib/readerResume.ts');
    const archive = new TextEncoder().encode('same EPUB bytes').buffer;
    const digest = await crypto.subtle.digest('SHA-256', archive);
    const epub_sha256 = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
    const hint = Object.freeze({ cfi: 'epubcfi(/6/2!/4/2/999/1:0)', epub_sha256,
      percentage: 95, mode, synced_at: '2026-09-01T00:00:00Z' });
    const saved = { ...hint };
    const resolved: string[] = [];
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    try {
      // Real timers: the watchdog rejects instead of supplying a fallback, so
      // the pre-fix await fails this test rather than hanging the test process.
      const result = await Promise.race([
        resumeForArchive(hint, archive, cfi => {
          resolved.push(cfi);
          return new Promise<Range>(() => {});
        }),
        new Promise<never>((_, reject) => {
          watchdog = setTimeout(() => reject(new Error('resume still blocked after 1500 ms')), 1500);
        }),
      ]);
      assert.deepEqual(resolved, [hint.cfi]);
      assert.deepEqual(result, { ...saved, cfi: undefined });
      assert.equal(resumeCfi({ cfiFromPercentage: p => `percentage:${p}` }, result), 'percentage:0.95');
      assert.deepEqual(hint, saved, 'do not mutate the saved response');
    } finally {
      clearTimeout(watchdog);
    }
  });
}

test('a slow working resolver retains exactness and a late rejection cannot change fallback', async () => {
  const { resumeForArchive } = await import('../src/lib/readerResume.ts');
  const archive = new TextEncoder().encode('same EPUB bytes').buffer;
  const digest = await crypto.subtle.digest('SHA-256', archive);
  const epub_sha256 = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
  const hint = Object.freeze({ cfi: 'epubcfi(/6/2!/4/2/1:0)', epub_sha256,
    percentage: 95, mode: 'automatic' as const, synced_at: '' });
  assert.equal(await resumeForArchive(hint, archive, async () => {
    await delay(200);
    return {} as Range;
  }), hint);
  let rejectRange!: (error: Error) => void;
  const result = await resumeForArchive(hint, archive, () => new Promise<Range>((_, reject) => {
    rejectRange = reject;
  }));
  assert.deepEqual(result, { ...hint, cfi: undefined });
  rejectRange(new Error('archive eventually failed'));
  await delay(0); // Node's test runner also fails on an unhandled rejection.
  assert.deepEqual(result, { ...hint, cfi: undefined });
});

test('a stalled archive digest also releases exact resume to its percentage', { timeout: 1500 }, async t => {
  const { resumeForArchive } = await import('../src/lib/readerResume.ts');
  t.mock.method(crypto.subtle, 'digest', () => new Promise<ArrayBuffer>(() => {}));
  const hint = Object.freeze({ cfi: 'epubcfi(/6/2!/4/2/1:0)', epub_sha256: 'unused',
    percentage: 95, mode: 'automatic' as const, synced_at: '' });
  const result = await resumeForArchive(hint, new ArrayBuffer(0), async () => {
    assert.fail('a pending digest cannot validate the archive');
  });
  assert.deepEqual(result, { ...hint, cfi: undefined });
});
