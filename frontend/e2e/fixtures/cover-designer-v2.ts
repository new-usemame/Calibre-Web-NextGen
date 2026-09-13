/* Contract fixtures + harness for the cover-designer v2 e2e spec.
 *
 * These mirror the REAL v2 backend (cps/services/cover_generator.py catalogue
 * vocabulary, merged from feat/cover-designer-v2-api) served via page.route —
 * the fixture spec must not depend on the rig seed or the renderer. The type
 * import keeps the fixture honest: tsc fails here if the fixture drifts from
 * the contract the frontend codes against. cover-designer-live.spec.ts covers
 * the same flows against the real server with no route mocks.
 */
import type { Page } from '@playwright/test';
import type { CataloguePreset, CoverDesign, DesignerCatalogue } from '../../src/features/coverDesigner/contract';

const scheme = (background: string, band: string, title: string, author: string) => ({
  background: `#${background}`, band: `#${band}`, title: `#${title}`, author: `#${author}`,
});

export const CATALOGUE_V2: DesignerCatalogue = {
  styles: [
    { id: 'blocks', label: 'Blocks',
      description: 'Title at the top, authors on a colour band across the bottom third.',
      thumbnail_url: '/cover-designer/style-thumb/blocks',
      align: { title: 'center', subtitle: 'center', author: 'right' },
      color_roles: { background: 'Upper two thirds', band: 'Bottom third',
                     title: 'Title and subtitle', author: 'Authors, on the band' } },
    { id: 'banner', label: 'Banner',
      description: 'Title on a folded ribbon near the top, authors below on the page.',
      thumbnail_url: '/cover-designer/style-thumb/banner',
      align: { title: 'center', subtitle: 'center', author: 'center' },
      color_roles: { background: 'Page', band: 'The ribbon',
                     title: 'Title and subtitle, on the ribbon', author: 'Authors, on the page' } },
    { id: 'ornamental', label: 'Ornamental',
      description: 'Title inside a decorated frame over a soft radial wash.',
      thumbnail_url: '/cover-designer/style-thumb/ornamental',
      align: { title: 'center', subtitle: 'center', author: 'center' },
      color_roles: { background: 'Centre of the wash', band: 'Edge of the wash',
                     title: 'Title and subtitle', author: 'Authors, and the ornaments' } },
    { id: 'cross', label: 'The Cross',
      description: 'A band down the left edge meeting a rounded title panel.',
      thumbnail_url: '/cover-designer/style-thumb/cross',
      align: { title: 'center', subtitle: 'center', author: 'center' },
      color_roles: { background: 'Page', band: 'Left edge and the title panel',
                     title: 'Title and subtitle, on the panel', author: 'Authors, on the page' } },
    { id: 'half', label: 'Half and half',
      description: 'One colour washing into another from top to bottom.',
      thumbnail_url: '/cover-designer/style-thumb/half',
      align: { title: 'center', subtitle: 'center', author: 'center' },
      color_roles: { background: 'Top and bottom of the wash', band: 'Middle of the wash',
                     title: 'All three text blocks', author: 'Unused by this arrangement' } },
  ],
  schemes: [
    { id: 'ink', label: 'Ink on cream', builtin: true,
      colors: scheme('f4efe3', '1f3a5f', '1f3a5f', 'f4efe3'),
      swatch: ['#f4efe3', '#1f3a5f'] },
    { id: 'meadow', label: 'Meadow green', builtin: true,
      colors: scheme('eef4e6', '3f6b3a', '24451f', 'f2f7ec'),
      swatch: ['#eef4e6', '#3f6b3a'] },
    { id: 'ember', label: 'Ember red', builtin: true,
      colors: scheme('fff3e6', 'c0392b', '7a2d12', 'fff3e6'),
      swatch: ['#fff3e6', '#c0392b'] },
    { id: 'slate', label: 'Slate grey', builtin: true,
      colors: scheme('e9ecef', '343a40', '212529', 'f8f9fa'),
      swatch: ['#e9ecef', '#343a40'] },
    { id: 'plum', label: 'Plum violet', builtin: true,
      colors: scheme('f3ecf7', '5b2c6f', '3d1e4a', 'f7f0fa'),
      swatch: ['#f3ecf7', '#5b2c6f'] },
    { id: 'midnight', label: 'Midnight', builtin: true,
      colors: scheme('11151f', '2d3b55', 'e8ecf5', 'f2f5fa'),
      swatch: ['#11151f', '#2d3b55'] },
    { id: 'noir', label: 'Noir', builtin: true,
      colors: scheme('f5f5f5', '161616', '161616', 'f5f5f5'),
      swatch: ['#f5f5f5', '#161616'] },
  ],
  fonts: [
    { id: 'serif', label: 'Serif', css_stack: "Liberation Serif, Georgia, 'Times New Roman', serif",
      sample_url: '/cover-designer/font-sample/serif', generic: 'serif' },
    { id: 'sans', label: 'Sans-serif', css_stack: 'Liberation Sans, Helvetica, Arial, sans-serif',
      sample_url: '/cover-designer/font-sample/sans', generic: 'sans' },
    // No sample_url on purpose: the card must fall back to the css_stack rendering.
    { id: 'mono', label: 'Monospace', css_stack: "Liberation Mono, 'Courier New', monospace",
      sample_url: '', generic: 'mono' },
  ],
  presets: [
    { id: 'classic', name: 'Classic', label: 'Classic', builtin: true, scope: 'builtin',
      hidden: false, design: { style: 'blocks', scheme: 'ink' } },
    { id: 'meadow', name: 'Meadow', label: 'Meadow', builtin: true, scope: 'builtin',
      hidden: false, design: { style: 'banner', scheme: 'meadow' } },
    { id: 'ember', name: 'Ember', label: 'Ember', builtin: true, scope: 'builtin',
      hidden: false,
      design: { style: 'blocks', scheme: 'ember',
                fonts: { title: { family: 'sans' }, subtitle: { family: 'sans' }, author: { family: 'sans' } } } },
    { id: 'slate', name: 'Slate', label: 'Slate', builtin: true, scope: 'builtin',
      hidden: false,
      design: { style: 'ornamental', scheme: 'slate',
                fonts: { title: { family: 'sans' }, subtitle: { family: 'sans' }, author: { family: 'sans' } } } },
    { id: 'plum', name: 'Plum', label: 'Plum', builtin: true, scope: 'builtin',
      hidden: false, design: { style: 'ornamental', scheme: 'plum' } },
    { id: 'library-1', name: 'Noir nights', builtin: false, scope: 'library', hidden: false,
      position: 0, design: { style: 'cross', scheme: 'noir' } },
    { id: 'user-1', name: 'My Draft', builtin: false, scope: 'user', hidden: false,
      position: 1, design: { style: 'banner', scheme: 'plum' } },
  ],
  defaults: {
    style: 'blocks',
    scheme: 'ink',
    colors: {},
    fonts: {
      title: { family: 'serif', size: 120, bold: true, italic: false },
      subtitle: { family: 'serif', size: 80, bold: false, italic: true },
      author: { family: 'serif', size: 80, bold: true, italic: false },
    },
    align: { title: 'center', subtitle: 'center', author: 'right' },
    text: { title: '{title}', subtitle: '{series} {series_index}', author: '{authors}' },
    size: { width: 1200, height: 1800 },
  },
  limits: {
    min_width: 200, max_width: 2400,
    min_height: 200, max_height: 2400,
    font_size_min: 8, font_size_max: 400,
    max_template_length: 200, max_name_length: 60, max_presets: 50,
  },
  placeholders: [
    { id: 'title', label: 'Book title' },
    { id: 'authors', label: 'All authors, separated by commas' },
    { id: 'series', label: 'Series name' },
    { id: 'series_index', label: 'Number within the series' },
  ],
  alignments: ['left', 'center', 'right'],
  text_slots: ['title', 'subtitle', 'author'],
  color_slots: ['background', 'band', 'title', 'author'],
  default_preset: 'classic',
};

// ---- harness ------------------------------------------------------------------

export interface Captured {
  previewBodies: { design: CoverDesign }[];
  applyBodies: Record<string, unknown>[];
  presetPosts: { name: string; design: CoverDesign; scope?: string }[];
  presetPuts: { id: string; body: { name?: string } }[];
  presetDeletes: string[];
  presetRestores: string[];
  presetOrders: string[][];
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
    design.fonts?.title?.bold ?? '-',
    design.align?.title ?? '-',
    design.align?.author ?? '-',
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
    cross: `<rect x="0" y="0" width="46" height="300" fill="${accent}"/><rect x="70" y="90" width="110" height="80" rx="10" fill="${accent}"/>${title(210)}`,
    half: `<rect width="200" height="300" fill="${accent}"/><rect width="200" height="150" fill="${bg}"/>${title(120)}`,
  };
  return `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="${bg}"/>${sketch[id] ?? sketch.blocks}</svg>`;
};
const fontSvg = (id: string) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80"><rect width="240" height="80" fill="#eee"/><text x="120" y="52" font-size="36" text-anchor="middle" fill="#333">Aa ${id}</text></svg>`;

/** Route-mock every v2 designer endpoint with the contract fixture. Preset
 *  storage lives in the closure: POST/PUT/DELETE/order mutate it and GET
 *  re-reads it, exactly like the real server (GET includes hidden entries,
 *  flagged — it is the manage list). */
export async function installContractFixtures(page: Page, bookId: number): Promise<Captured> {
  const captured: Captured = {
    previewBodies: [], applyBodies: [], presetPosts: [], presetPuts: [], presetDeletes: [],
    presetRestores: [], presetOrders: [],
  };
  let presets: CataloguePreset[] = structuredClone(CATALOGUE_V2.presets);
  // user-1 and library-1 exist in the fixture; new rows take the next number.
  let userCounter = 1;
  let libCounter = 1;

  // The real server sends the catalogue FLAT inside `designer` (plus available/
  // renderer/hidden_presets/can_share_presets), not nested under `catalogue`.
  await page.route(`**/book/${bookId}/cover/state*`, (route) => route.fulfill({
    json: {
      locked: false,
      ereader_enabled: false,
      ereader_defaults: { aspect: 'kobo_libra_color', fill_mode: 'edge_mirror', color: '' },
      designer: {
        ...CATALOGUE_V2,
        presets: presets.filter((p) => !p.hidden),
        available: true, renderer: 'pil', hidden_presets: [], can_share_presets: true,
      },
    },
  }));

  await page.route('**/cover-designer/presets**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const tail = url.pathname.split('/cover-designer/presets')[1] ?? '';
    const orderMatch = tail === '/order' && req.method() === 'POST';
    const idMatch = tail.match(/^\/([^/]+)$/);
    const restoreMatch = tail.match(/^\/([^/]+)\/restore$/);

    if (orderMatch) {
      const body = (req.postDataJSON() ?? {}) as { order?: string[] };
      const order = body.order ?? [];
      captured.presetOrders.push(order);
      const rank = new Map(order.map((oid, i) => [oid, i]));
      presets = presets.map((p) =>
        p.builtin || !rank.has(p.id) ? p : { ...p, position: rank.get(p.id) });
      presets = [...presets].sort((a, b) =>
        (a.builtin ? -1 : (a.position ?? 0)) - (b.builtin ? -1 : (b.position ?? 0)));
      await route.fulfill({ json: { presets, hidden: presets.filter((p) => p.hidden).map((p) => p.id) } });
      return;
    }
    if (restoreMatch && req.method() === 'POST') {
      captured.presetRestores.push(restoreMatch[1]);
      presets = presets.map((p) => (p.id === restoreMatch[1] ? { ...p, hidden: false } : p));
      await route.fulfill({ json: { preset: presets.find((p) => p.id === restoreMatch[1]) } });
      return;
    }
    if (idMatch && req.method() === 'PUT') {
      const body = (req.postDataJSON() ?? {}) as { name?: string };
      captured.presetPuts.push({ id: idMatch[1], body });
      presets = presets.map((p) => (p.id === idMatch[1] && body.name ? { ...p, name: body.name } : p));
      await route.fulfill({ json: { preset: presets.find((p) => p.id === idMatch[1]) } });
      return;
    }
    if (idMatch && req.method() === 'DELETE') {
      captured.presetDeletes.push(idMatch[1]);
      const target = presets.find((p) => p.id === idMatch[1]);
      // The real server hides built-ins and deletes saved presets.
      presets = target?.builtin
        ? presets.map((p) => (p.id === idMatch[1] ? { ...p, hidden: true } : p))
        : presets.filter((p) => p.id !== idMatch[1]);
      await route.fulfill({ status: 204, body: '' });
      return;
    }
    if (req.method() === 'POST') {
      const body = (req.postDataJSON() ?? {}) as { name: string; design: CoverDesign; scope?: string };
      captured.presetPosts.push(body);
      const scope = body.scope === 'library' ? 'library' as const : 'user' as const;
      const preset: CataloguePreset = {
        id: scope === 'library' ? `library-${++libCounter}` : `user-${++userCounter}`,
        name: body.name, design: body.design,
        builtin: false, scope, hidden: false,
        position: presets.filter((p) => !p.builtin).length,
      };
      presets = [...presets, preset];
      await route.fulfill({ status: 201, json: { ok: true, preset } });
      return;
    }
    await route.fulfill({
      json: { presets, hidden: presets.filter((p) => p.hidden).map((p) => p.id) },
    });
  });

  await page.route('**/cover-designer/style-thumb/*', (route) =>
    route.fulfill({ contentType: 'image/svg+xml', body: thumbSvg(route.request().url().split('/').pop() ?? '') }));
  await page.route('**/cover-designer/font-sample/*', (route) =>
    route.fulfill({ contentType: 'image/svg+xml', body: fontSvg(route.request().url().split('/').pop() ?? '') }));

  await page.route('**/cover/design-preview*', async (route) => {
    const body = (route.request().postDataJSON() ?? {}) as { design: CoverDesign };
    captured.previewBodies.push(body);
    await route.fulfill({
      json: { data_url: dataUrlFor(body.design ?? {}), renderer: 'pil',
              design: body.design ?? {}, resolved: body.design ?? {} },
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
