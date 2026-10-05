import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
const source = (id: string) => ({ id, label: `Catalog ${id}`, adapter: id === 'b' ? 'newznab' : 'opds', enabled: true, revision: 1 });
const catalog = (id: string, query?: string) => ({ title: id, protocol: 'fixture', publications: query ? [{ title: `${query} edition`, identity: id, authors: ['Author'], languages: [id === 'b' ? 'de' : 'en'], description: null, offers: [{ format: id === 'b' ? 'NZB' : 'EPUB', label: null, identity: id, relation: 'download', offer_id: `${id}-offer` }], navigation: [] }] : [], navigation: [], pagination: query ? [{ title: 'Next page', relations: ['next'], selection: `${id}-next` }] : [], searches: [{ title: '', selection: `${id}-search` }], groups: [], facets: [] });
async function fixture(page: Page, ids = ['a', 'b', 'browse', 'broken', 'empty', 'six'], labels: Record<string, string> = {}) {
    const calls: {
        id: string;
        selection: string | null;
        query: string | null;
    }[] = [], jobs: Record<string, unknown>[] = [];
    const configuredSources = () => ids.map((id) => ({ ...source(id), label: labels[id] ?? source(id).label }));
    let broken = true, sources = configuredSources();
    await page.route('**/api/v1/auth/me', async (route) => { const response = await route.fetch(); await route.fulfill({ response, json: { ...await response.json(), acquisition_access: true } }); });
    await page.route('**/api/v1/acquisition**', async (route) => {
        const u = new URL(route.request().url());
        let body: unknown;
        if (u.pathname.endsWith('/catalog')) {
            const id = u.searchParams.get('connection')!, selection = u.searchParams.get('selection'), query = u.searchParams.get('q');
            calls.push({ id, selection, query });
            if (id === 'broken' && broken)
                return route.fulfill({ status: 502, json: { error: { code: 'source_unavailable', message: 'private error' } } });
            body = catalog(id, query ?? (selection ? 'page' : undefined));
            if (id === 'browse')
                body = { ...catalog(id), searches: [] };
            if (id === 'empty' && query)
                body = { ...catalog(id), searches: [] };
        }
        else if (u.pathname.endsWith('/jobs') && route.request().method() === 'POST') {
            jobs.push(route.request().postDataJSON());
            body = { id: 'job', state: 'awaiting_approval', title: 'edition' };
        }
        else if (u.pathname.endsWith('/jobs'))
            body = { jobs: [] };
        else if (u.pathname.endsWith('/acquisition'))
            body = { connections: sources, can_acquire: false, runtime: { available: true, reasons: [] } };
        else
            return route.fallback();
        await route.fulfill({ json: body });
    });
    await page.goto('/app/find-books');
    await page.getByRole('combobox').selectOption('all');
    return { calls, jobs, recover: () => { broken = false; }, withdraw: () => { sources = sources.filter((c) => c.id !== 'b'); }, restore: () => { sources = configuredSources(); } };
}
async function search(page: Page, query = 'edition') { await page.getByRole('searchbox', { name: 'Search all catalogs' }).fill(query); await page.getByRole('button', { name: 'Search', exact: true }).click(); }
test('source-bound offers, explicit partial failure and browse-only, isolated retry and remaining batches', async ({ page }) => {
    const { calls, jobs, recover } = await fixture(page);
    await search(page);
    const a = page.getByRole('region', { name: 'Catalog a', exact: true }), b = page.getByRole('region', { name: 'Catalog b', exact: true });
    await expect(a.getByText('edition edition', { exact: true })).toBeVisible();
    await expect(b.getByText('edition edition', { exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Catalog browse' }).getByText('This catalog does not offer search.')).toBeVisible();
    const failed = page.getByRole('region', { name: 'Catalog broken' });
    await expect(failed.getByRole('alert')).toBeVisible();
    await expect(page.getByText('2 catalogs have not been searched yet.')).toBeVisible();
    expect(calls.filter((c) => c.query).map((c) => c.id).sort()).toEqual(['a', 'b']);
    await b.getByRole('button', { name: 'Request release', exact: true }).click();
    await expect.poll(() => jobs.length).toBe(1);
    expect(jobs[0]).toMatchObject({ connection_id: 'b', offer_id: 'b-offer' });
    const old = calls.length;
    recover();
    await failed.getByRole('button', { name: 'Try again' }).click();
    await expect(failed.getByText('edition edition', { exact: true })).toBeVisible();
    expect(calls.slice(old).map((c) => c.id)).toEqual(['broken', 'broken']);
    await page.getByRole('button', { name: 'Search remaining catalogs' }).click();
    await expect(page.getByRole('region', { name: 'Catalog empty' }).getByText('No books on this catalog matched that search.')).toBeVisible();
    await expect(page.getByRole('region', { name: 'Catalog six' }).getByText('edition edition', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Search remaining catalogs' })).toHaveCount(0);
});
test('source pagination opens its opaque page in individual browsing', async ({ page }) => {
    const { calls } = await fixture(page, ['a', 'b']);
    await search(page);
    await page.getByRole('region', { name: 'Catalog b', exact: true }).getByRole('button', { name: 'Next page' }).click();
    await expect(page.getByRole('combobox')).toHaveValue('b');
    await expect.poll(() => calls[calls.length - 1]?.selection).toBe('b-next');
    expect(calls[calls.length - 1]?.id).toBe('b');
    await expect(page.getByRole('searchbox', { name: 'Search all catalogs' })).toHaveCount(0);
});
test('new query and leaving shared mode discard late answers', async ({ page }) => {
    await fixture(page, ['a', 'b']);
    await page.route('**/api/v1/acquisition/catalog?**', async (route) => {
        const u = new URL(route.request().url());
        if (u.searchParams.get('q') !== 'old')
            return route.fallback();
        await new Promise((r) => setTimeout(r, 600));
        await route.fulfill({ json: catalog(u.searchParams.get('connection')!, 'old') }).catch(() => { });
    });
    await search(page, 'old');
    await search(page, 'new');
    await expect(page.getByRole('region', { name: 'Catalog b', exact: true }).getByText('new edition', { exact: true })).toBeVisible();
    await page.waitForTimeout(700);
    await expect(page.getByText('old edition', { exact: true })).toHaveCount(0);
    await search(page, 'old');
    await page.getByRole('combobox').selectOption('a');
    await page.waitForTimeout(700);
    await expect(page.getByText('old edition', { exact: true })).toHaveCount(0);
});
test('withdrawn source loses actionable results on bootstrap refresh', async ({ page }) => {
    const { withdraw, restore } = await fixture(page, ['a', 'b']);
    await search(page);
    await expect(page.getByRole('region', { name: 'Catalog b', exact: true })).toBeVisible();
    withdraw();
    await page.getByRole('button', { name: 'Refresh catalogs' }).click();
    await expect(page.getByRole('region', { name: 'Catalog b', exact: true })).toHaveCount(0);
    restore();
    const refreshed = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/acquisition');
    await page.getByRole('button', { name: 'Refresh catalogs' }).click();
    await refreshed;
    await expect(page.getByRole('combobox').locator('option[value="b"]')).toHaveCount(1);
    await expect(page.getByRole('status').filter({ hasText: 'Checked 1 catalogs' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Catalog b', exact: true })).toHaveCount(0);
    await search(page, 'fresh');
    await expect(page.getByRole('region', { name: 'Catalog b', exact: true }).getByText('fresh edition', { exact: true })).toBeVisible();
});
test('populated shared search keyboard submit and serious axe gate', async ({ page }) => {
    await fixture(page);
    await page.getByRole('searchbox', { name: 'Search all catalogs' }).fill('edition');
    await page.getByRole('searchbox', { name: 'Search all catalogs' }).press('Enter');
    await expect(page.getByRole('region', { name: 'Catalog b', exact: true })).toBeVisible();
    await expect(page.getByRole('status').filter({ hasText: 'Checked 4 catalogs' })).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    const findings = await new AxeBuilder({ page }).include('main').withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
    expect(findings.violations.filter((v) => ['critical', 'serious'].includes(v.impact ?? '')).map((v) => ({ id: v.id, nodes: v.nodes.map((n) => n.target) }))).toEqual([]);
});

for (const kind of ['query', 'catalog label'] as const) {
    test(`long ${kind} stays within the phone viewport`, async ({ page }) => {
        const longText = 'antidisestablishmentarianism'.repeat(3);
        await fixture(page, ['a', 'b'], kind === 'catalog label' ? { a: longText } : {});
        await search(page, kind === 'query' ? longText : 'edition');
        await expect(page.getByRole('status').filter({ hasText: 'Checked 2 catalogs' })).toBeVisible();
        for (const width of [375, 320]) {
            await page.setViewportSize({ width, height: 667 });
            expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
        }
    });
}
