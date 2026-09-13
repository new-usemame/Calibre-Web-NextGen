/* Contract fixtures + harness for the cover-designer v2 e2e specs.
 *
 * These are CONTRACT shapes (state/cover-designer/v2/CONTRACT.md), served via
 * page.route — the v2 backend lives in a parallel branch, so the specs never
 * depend on which designer version the running server has. The type import
 * keeps the fixture honest: tsc fails here if the fixture drifts from the
 * contract the frontend codes against.
 */
import type { Page } from '@playwright/test';
import type { CataloguePreset, CoverDesign, DesignerCatalogue } from '../../src/features/coverDesigner/contract';

export const CATALOGUE_V2: DesignerCatalogue = {
  styles: [
    { id: 'blocks', label: 'Blocks', description: 'Title above, authors on a colour band',
      thumbnail_url: '/cover-designer/style-thumb/blocks' },
    { id: 'banner', label: 'Banner', description: 'Title on a ribbon near the top',
      thumbnail_url: '/cover-designer/style-thumb/banner' },
    { id: 'ornamental', label: 'Ornamental', description: 'Title inside a decorative frame',
      thumbnail_url: '/cover-designer/style-thumb/ornamental' },
    { id: 'emblem', label: 'Emblem', description: 'A centred emblem above the title',
      thumbnail_url: '/cover-designer/style-thumb/emblem' },
    { id: 'stripes', label: 'Stripes', description: 'Horizontal bands behind every line',
      thumbnail_url: '/cover-designer/style-thumb/stripes' },
  ],
  schemes: [
    { id: 'ink', label: 'Ink on cream', builtin: true,
      colors: { background: '#f4efe3', band: '#1f3a5f', title: '#1f3a5f', author: '#f4efe3' } },
    { id: 'meadow', label: 'Meadow green', builtin: true,
      colors: { background: '#eef4e6', band: '#3f6b3a', title: '#24451f', author: '#f2f7ec' } },
    { id: 'ember', label: 'Ember red', builtin: true,
      colors: { background: '#fff3e6', band: '#c0392b', title: '#7a2d12', author: '#fff3e6' } },
    { id: 'slate', label: 'Slate grey', builtin: true,
      colors: { background: '#e9ecef', band: '#343a40', title: '#212529', author: '#f8f9fa' } },
    { id: 'plum', label: 'Plum violet', builtin: true,
      colors: { background: '#f3ecf7', band: '#5b2c6f', title: '#3d1e4a', author: '#f7f0fa' } },
  ],
  fonts: [
    { id: 'serif', label: 'Serif', css_stack: "Georgia, 'Times New Roman', serif",
      sample_url: '/cover-designer/font-sample/serif' },
    { id: 'sans', label: 'Sans-serif', css_stack: 'Arial, Helvetica, sans-serif',
      sample_url: '/cover-designer/font-sample/sans' },
    // No sample_url on purpose: the card must fall back to the css_stack rendering.
    { id: 'mono', label: 'Monospace', css_stack: "'Courier New', monospace", sample_url: '' },
  ],
  presets: [
    { id: 'classic', name: 'Classic', builtin: true, scope: 'library',
      design: { style: 'blocks', scheme: 'ink' } },
    { id: 'meadow', name: 'Meadow', builtin: true, scope: 'library',
      design: { style: 'banner', scheme: 'meadow' } },
    { id: 'ember', name: 'Ember', builtin: true, scope: 'library',
      design: { style: 'blocks', scheme: 'ember' } },
    { id: 'noir', name: 'Noir', builtin: false, scope: 'library',
      design: { style: 'ornamental', scheme: 'slate' } },
    { id: 'my-draft', name: 'My Draft', builtin: false, scope: 'user',
      design: { style: 'banner', scheme: 'plum' } },
  ],
  defaults: {
    style: 'blocks',
    scheme: 'ink',
    colors: {},
    fonts: {
      title: { family: 'serif', size: 64 },
      subtitle: { family: 'serif', size: 32 },
      author: { family: 'serif', size: 28 },
    },
    align: { title: 'center', subtitle: 'center', author: 'center' },
    text: { title: '{title}', subtitle: '{series} {series_index}', author: '{authors}' },
    size: { width: 1200, height: 1800 },
  },
  limits: {
    min_width: 200, max_width: 2400,
    min_height: 200, max_height: 2400,
    font_size_min: 8, font_size_max: 200,
  },
};

// ---- harness ------------------------------------------------------------------

export interface Captured {
  previewBodies: { design: CoverDesign }[];
  applyBodies: Record<string, unknown>[];
  presetPosts: { name: string; design: CoverDesign; scope?: string }[];
  presetPuts: { id: string; body: { name?: string } }[];
  presetDeletes: string[];
  presetRestores: string[];
}

/** Each mocked render is a distinct data URL keyed by the design that produced
 *  it, so "the preview changed" is a real observation rather than a spinner. */
export const dataUrlFor = (design: CoverDesign) => {
  const marker = [
    design.style ?? '-',
    design.scheme === null ? 'custom' : design.scheme ?? '-',
    design.colors?.background ?? '-',
    design.fonts?.title?.family ?? '-',
    design.fonts?.title?.size ?? '-',
    design.align?.title ?? '-',
    design.text?.title ?? '-',
    design.size?.width ?? '-',
  ].join('|');
  return `data:image/jpeg;base64,${Buffer.from(`rendered:${marker}`).toString('base64')}`;
};

/** A tiny stand-in for the server-rendered style thumbnails: an SVG sketch of
 *  the arrangement, so a loaded thumbnail is distinguishable from the inline
 *  fallback glyph (which has no <img>). */
const thumbSvg = (id: string) => {
  const bg = '#44403c', ink = '#f5f5f4', accent = '#a8a29e';
  const band = (y: number, h: number) => `<rect x="0" y="${y}" width="200" height="${h}" fill="${accent}"/>`;
  const title = (y: number) => `<rect x="40" y="${y}" width="120" height="12" rx="6" fill="${ink}"/><rect x="60" y="${y + 20}" width="80" height="8" rx="4" fill="${ink}" opacity="0.7"/>`;
  const sketch: Record<string, string> = {
    blocks: `${title(60)}${band(210, 60)}`,
    banner: `${band(30, 70)}${title(150)}`,
    ornamental: `<rect x="20" y="20" width="160" height="260" fill="none" stroke="${accent}" stroke-width="6"/>${title(120)}`,
    emblem: `<circle cx="100" cy="80" r="34" fill="${accent}"/>${title(160)}`,
    stripes: `${band(40, 26)}${band(90, 26)}${band(140, 26)}${title(210)}`,
  };
  return `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="${bg}"/>${sketch[id] ?? sketch.blocks}</svg>`;
};
const fontSvg = (id: string) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80"><rect width="240" height="80" fill="#eee"/><text x="120" y="52" font-size="36" text-anchor="middle" fill="#333">Aa ${id}</text></svg>`;

/** Route-mock every v2 designer endpoint with the contract fixture. Preset
 *  storage lives in the closure: POST/PUT/DELETE mutate it and GET re-reads
 *  it, exactly like the contract server will. */
export async function installContractFixtures(page: Page, bookId: number): Promise<Captured> {
  const captured: Captured = {
    previewBodies: [], applyBodies: [], presetPosts: [], presetPuts: [], presetDeletes: [], presetRestores: [],
  };
  let presets: CataloguePreset[] = structuredClone(CATALOGUE_V2.presets);
  let userCounter = 0;

  await page.route(`**/book/${bookId}/cover/state*`, (route) => route.fulfill({
    json: {
      locked: false,
      ereader_enabled: false,
      ereader_defaults: { aspect: 'kobo_libra_color', fill_mode: 'edge_mirror', color: '' },
      designer: { available: true, renderer: 'pil', catalogue: CATALOGUE_V2 },
    },
  }));

  await page.route('**/cover-designer/presets**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const tail = url.pathname.split('/cover-designer/presets')[1] ?? '';
    const idMatch = tail.match(/^\/([^/]+)$/);
    const restoreMatch = tail.match(/^\/([^/]+)\/restore$/);

    if (restoreMatch && req.method() === 'POST') {
      const preset = CATALOGUE_V2.presets.find((p) => p.id === restoreMatch[1]);
      captured.presetRestores.push(restoreMatch[1]);
      if (preset && !presets.some((p) => p.id === preset.id)) presets = [...presets, preset];
      await route.fulfill({ json: { preset } });
      return;
    }
    if (idMatch && req.method() === 'PUT') {
      const body = (req.postDataJSON() ?? {}) as { name?: string };
      captured.presetPuts.push({ id: idMatch[1], body });
      const preset = presets.find((p) => p.id === idMatch[1]);
      if (preset && body.name) preset.name = body.name;
      await route.fulfill({ json: { preset } });
      return;
    }
    if (idMatch && req.method() === 'DELETE') {
      captured.presetDeletes.push(idMatch[1]);
      presets = presets.filter((p) => p.id !== idMatch[1]);
      await route.fulfill({ status: 204, body: '' });
      return;
    }
    if (req.method() === 'POST') {
      const body = (req.postDataJSON() ?? {}) as { name: string; design: CoverDesign; scope?: string };
      captured.presetPosts.push(body);
      const preset: CataloguePreset = {
        id: `user-${++userCounter}`, name: body.name, design: body.design,
        builtin: false, scope: body.scope === 'library' ? 'library' : 'user',
      };
      presets = [...presets, preset];
      await route.fulfill({ status: 201, json: { preset } });
      return;
    }
    await route.fulfill({ json: { presets } });
  });

  await page.route('**/cover-designer/style-thumb/*', (route) =>
    route.fulfill({ contentType: 'image/svg+xml', body: thumbSvg(route.request().url().split('/').pop() ?? '') }));
  await page.route('**/cover-designer/font-sample/*', (route) =>
    route.fulfill({ contentType: 'image/svg+xml', body: fontSvg(route.request().url().split('/').pop() ?? '') }));

  await page.route('**/cover/design-preview*', async (route) => {
    const body = (route.request().postDataJSON() ?? {}) as { design: CoverDesign };
    captured.previewBodies.push(body);
    await route.fulfill({
      json: { data_url: dataUrlFor(body.design ?? {}), renderer: 'pil', design: body.design ?? {} },
    });
  });

  await page.route('**/cover/apply', async (route) => {
    const body = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
    if (body.kind !== 'generated') { await route.fallback(); return; }
    captured.applyBodies.push(body);
    await route.fulfill({ json: { ok: true, cover_url: `/cover/${bookId}/og?ts=designed` } });
  });

  // Candidate fan-out is irrelevant here and slow; keep the page quiet.
  await page.route('**/cover/candidates*', (route) =>
    route.fulfill({ json: { candidates: [], providers: [], query: 'seed' } }));

  return captured;
}

export async function firstBookId(page: Page): Promise<number | null> {
  await page.goto('/app/');
  return page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=1', { headers: { Accept: 'application/json' } })
      .then((x) => (x.ok ? x.json() : null)).catch(() => null);
    return r?.items?.[0]?.id ?? null;
  });
}

/** The designer's <details>. Scoped to its own summary so the Advanced
 *  disclosure nested inside it can never match. */
export const designerPanel = (page: Page) =>
  page.locator('details').filter({ has: page.locator(':scope > summary', { hasText: 'Design a cover' }) });

/** Same for the Advanced disclosure inside the panel. */
export const advancedDetails = (page: Page) =>
  designerPanel(page).locator('details')
    .filter({ has: page.locator(':scope > summary', { hasText: 'Advanced' }) });

export const lastPreview = (c: Captured) => c.previewBodies[c.previewBodies.length - 1]?.design;
