import { test } from 'node:test';
import assert from 'node:assert/strict';
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
