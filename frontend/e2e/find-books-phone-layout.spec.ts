import { test, expect, type Page, type Route } from '@playwright/test';

/*
 * The Find books search control was a ~260px-tall slab on phones.
 *
 * `.search` is `flex: 1 1 260px` so that on a wide screen it shares the
 * controls row with the catalog picker and grows into the space left over.
 * That `260px` is a flex BASIS, and a basis is measured along the container's
 * MAIN axis. The phone block flips the container — `.controls
 * { flex-direction: column }` — so the same declaration silently stopped
 * meaning "at least 260px wide" and started meaning "260px TALL". The input
 * and the submit button then stretched to fill it, and the first thing a
 * phone user saw on this feature's own page was a search box deeper than a
 * thumb.
 *
 * What is asserted here is the shape of the control, not a pixel count: a
 * search field is a single row of ~40px controls with nothing padding it out.
 * The "no dead space" assertion is the load-bearing one — centring the
 * children inside the slab would shrink the input and the button back to 40px
 * while leaving a tall empty form around them, which looks fixed in a DOM
 * dump and is still broken on the screen.
 *
 * Route interception only: no server state is touched, so this is safe in the
 * broad lanes. The spec sets its own viewports, so it reads the same in every
 * project that picks it up.
 */

const V1 = '/api/v1';

/** A phone control is ~40px. The defect rendered these at ~190px and up, so a
 *  generous ceiling still separates the two without pinning exact pixels
 *  across engines, fonts and device-scale factors. */
const CONTROL_MAX_HEIGHT = 64;
/** The form is allowed its own border and a rounding wobble over its tallest
 *  child, and nothing more. This is what rejects a slab with centred content. */
const FORM_SLACK = 8;

const GUTENBERG = {
  id: 'c1', label: 'Project Gutenberg', adapter: 'opds', enabled: true, revision: 1,
};
const SECOND_CATALOG = {
  id: 'c2', label: 'Standard Ebooks', adapter: 'opds', enabled: true, revision: 1,
};

/** One advertised search capability is what makes the form render at all —
 *  `searchCapability = catalog.data?.searches?.[0]` in the page. */
const CATALOG = {
  title: 'Project Gutenberg',
  protocol: 'opds',
  publications: [],
  navigation: [{ title: 'Popular', relations: [], selection: 'sel-popular' }],
  pagination: [],
  searches: [{ title: 'Search this catalog', selection: 'sel-search' }],
  groups: [],
  facets: [],
};

/** `/app/find-books` renders <NotFound/> unless `me.acquisition_access` is
 *  true (App.tsx), and the feature ships switched off. Grant it in the
 *  RESPONSE only — the real payload is fetched and patched — so the page
 *  renders without granting a role on the server.
 *
 *  Registration order matters: Playwright offers a request to the most
 *  recently registered handler first, so the catch-all below is reached
 *  before this one and has to `fallback()` rather than `continue()` for the
 *  override to be live at all. The path is `/api/v1/auth/me`, not
 *  `/api/v1/me`. */
async function openFindBooks(
  page: Page,
  connections: (typeof GUTENBERG)[] = [GUTENBERG],
): Promise<void> {
  await page.route(`**${V1}/auth/me`, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({
      response,
      contentType: 'application/json',
      body: JSON.stringify({ ...body, acquisition_access: true }),
    });
  });

  const replies: Record<string, unknown> = {
    [`${V1}/acquisition`]: {
      connections, can_acquire: true, runtime: { available: true, reasons: [] },
    },
    [`${V1}/acquisition/catalog`]: CATALOG,
    [`${V1}/acquisition/jobs`]: { jobs: [] },
  };
  await page.route(`**${V1}/**`, async (route: Route) => {
    const { pathname } = new URL(route.request().url());
    const body = replies[pathname];
    if (body === undefined) return route.fallback();
    await route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(body),
    });
  });

  await page.goto('/app/find-books');
  await expect(page.getByRole('heading', { name: 'Find books', level: 1 })).toBeVisible();
}

/** The app shell renders its own `role="search"` landmark in the top bar, so
 *  a bare `getByRole('search')` is ambiguous wherever that bar is visible.
 *  Scope to the form that owns THIS page's field. */
function catalogSearch(page: Page) {
  return page
    .getByRole('search')
    .filter({ has: page.getByRole('searchbox', { name: 'Search this catalog' }) });
}

/** Let webfonts and two frames land before measuring; a control measured
 *  mid-swap reports the fallback font's metrics. */
async function settle(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
    });
  });
}

test('the catalog search stays a single row of controls at phone widths', async ({ page }) => {
  for (const width of [375, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await openFindBooks(page);

    const form = catalogSearch(page);
    await expect(form).toBeVisible();
    await expect(form.getByRole('searchbox', { name: 'Search this catalog' })).toBeVisible();
    await settle(page);

    const box = await form.evaluate((element) => {
      const input = element.querySelector('input')!;
      const button = element.querySelector('button')!;
      const height = (node: Element) => node.getBoundingClientRect().height;
      return {
        form: height(element),
        input: height(input),
        button: height(button),
        inputWidth: input.getBoundingClientRect().width,
        overflow: document.documentElement.scrollWidth - window.innerWidth,
      };
    });

    expect(box.input, `the search field is a control, not a panel, at ${width}px`)
      .toBeLessThanOrEqual(CONTROL_MAX_HEIGHT);
    expect(box.button, `the Search button is a control, not a panel, at ${width}px`)
      .toBeLessThanOrEqual(CONTROL_MAX_HEIGHT);
    expect(box.form, `the search form is one row of controls at ${width}px`)
      .toBeLessThanOrEqual(CONTROL_MAX_HEIGHT + FORM_SLACK);
    // The one that survives a cosmetic fix: shrinking the children inside a
    // slab leaves the slab.
    expect(
      box.form - Math.max(box.input, box.button),
      `the search form is exactly its controls, with no dead space, at ${width}px`,
    ).toBeLessThanOrEqual(FORM_SLACK);
    // A field squeezed to nothing would satisfy every height bound above.
    expect(box.inputWidth, `the search field is still usable at ${width}px`)
      .toBeGreaterThanOrEqual(140);
    expect(box.overflow, `Find books must not scroll sideways at ${width}px`)
      .toBeLessThanOrEqual(1);
  }
});

test('the wide layout still lets the search share the controls row', async ({ page }) => {
  // The guard rail for the fix: the 260px basis earns its place on a wide
  // screen, where the picker and the search sit on one line and the search
  // takes the slack. A fix that killed the basis outright instead of scoping
  // it to the row axis would pass every phone assertion above and quietly
  // ruin this.
  await page.setViewportSize({ width: 1280, height: 800 });
  await openFindBooks(page, [GUTENBERG, SECOND_CATALOG]);

  const form = catalogSearch(page);
  const picker = page.getByRole('combobox', { name: 'Catalog' });
  await expect(form).toBeVisible();
  await expect(picker).toBeVisible();
  await settle(page);

  const formBox = (await form.boundingBox())!;
  const pickerBox = (await picker.boundingBox())!;

  expect(formBox.width, 'the search keeps its 260px basis on a wide screen')
    .toBeGreaterThanOrEqual(260);
  // Same row: the two boxes overlap vertically and the search sits after the
  // picker. `align-items: flex-end` aligns their bottoms, not their tops, so
  // overlap is the honest test rather than equal `y`.
  expect(formBox.y, 'the search and the catalog picker share one row')
    .toBeLessThan(pickerBox.y + pickerBox.height);
  expect(pickerBox.y, 'the search and the catalog picker share one row')
    .toBeLessThan(formBox.y + formBox.height);
  expect(formBox.x, 'the search follows the catalog picker across the row')
    .toBeGreaterThanOrEqual(pickerBox.x + pickerBox.width);
});
