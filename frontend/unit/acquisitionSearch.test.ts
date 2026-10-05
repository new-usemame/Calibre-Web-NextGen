import assert from 'node:assert/strict';
import { test } from 'node:test';
import { searchCatalogBatch, SEARCH_BATCH_SIZE } from '../src/lib/acquisitionSearch.ts';
import type { AcquisitionCatalog, AcquisitionConnection } from '../src/lib/acquisition';
const source = (id: string): AcquisitionConnection => ({ id, label: id, adapter: 'opds', enabled: true, revision: 1 });
const page = (search?: string): AcquisitionCatalog => ({ title: '', protocol: 'opds2', publications: [], navigation: [], pagination: [], searches: search ? [{ title: '', selection: search }] : [], groups: [], facets: [] });
test('mixed protocols use only advertised owner-bound selections; failure and browse-only are explicit', async () => {
    const calls: unknown[] = [], results: unknown[] = [];
    await searchCatalogBatch(['opds', 'indexer', 'browse', 'broken'].map(source), '  same title  ', async (id, options) => {
        calls.push([id, options?.selection, options?.query]);
        if (id === 'broken')
            throw new Error('https://private/?key=secret');
        return page(options?.selection || id === 'browse' ? undefined : `${id}-search`);
    }, new AbortController().signal, (r) => results.push(r));
    assert.deepEqual(calls.filter((c: any) => c[1]), [['opds', 'opds-search', 'same title'], ['indexer', 'indexer-search', 'same title']]);
    assert.deepEqual(results.map((r: any) => [r.connection.id, r.state]).sort(), [['broken', 'failed'], ['browse', 'browse-only'], ['indexer', 'ready'], ['opds', 'ready']]);
    assert.ok(!JSON.stringify(results).includes('secret'));
});
test('oversized or invalid batches fail before any remote request', async () => {
    let calls = 0;
    const read = async () => { calls++; return page(); };
    for (const [sources, query] of [[Array.from({ length: SEARCH_BATCH_SIZE + 1 }, (_, i) => source(String(i))), 'ok'], [[source('x')], ' '], [[source('x')], 'x'.repeat(501)], [[source('x'), source('x')], 'ok']] as [
        AcquisitionConnection[],
        string
    ][]) {
        await assert.rejects(searchCatalogBatch(sources, query, read, new AbortController().signal, () => { }));
    }
    assert.equal(calls, 0);
});
test('completed sources are delivered while a sibling is waiting; cancellation prevents second reads and late results', async () => {
    let release!: () => void;
    const held = new Promise<void>((r) => { release = r; });
    const controller = new AbortController(), results: string[] = [], calls: string[] = [];
    const work = searchCatalogBatch([source('slow'), source('fast')], 'query', async (id, options) => {
        calls.push(`${id}:${options?.selection ?? 'root'}`);
        if (id === 'slow')
            await held;
        return page(options?.selection ? undefined : 'search');
    }, controller.signal, (r) => results.push(r.connection.id));
    await new Promise((r) => setImmediate(r));
    assert.deepEqual(results, ['fast']);
    controller.abort();
    release();
    await work;
    assert.deepEqual(results, ['fast']);
    assert.ok(!calls.includes('slow:search'));
});
