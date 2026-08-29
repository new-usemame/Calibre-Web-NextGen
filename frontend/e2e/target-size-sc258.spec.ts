import { expect, test, type Locator, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';

type HitExtent = {
  visual: [number, number];
  effective: [number, number];
};

type BookList = {
  items: Array<{ id: number }>;
};

type BookDetail = {
  id: number;
  formats: Array<{ format: string }>;
};

const APP_PASSWORD_LABEL = `SC 2.5.8 target probe ${Date.now()}`;
const TEMP_FORMAT = 'TXT';
const TEMP_FORMAT_FILE = new URL('../../tests/fixtures/sample_books/alice_in_wonderland.txt', import.meta.url);

async function measureEffectiveHitExtent(control: Locator): Promise<HitExtent> {
  // elementFromPoint() uses viewport coordinates. Keeping the control in view
  // before probing is therefore part of the measurement, not test setup fluff.
  await control.scrollIntoViewIfNeeded();

  return control.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const centreX = rect.left + rect.width / 2;
    const centreY = rect.top + rect.height / 2;
    const ownsHit = (hit: Element | null) =>
      hit !== null && (hit === element || element.contains(hit));

    const findBoundary = (
      visualRadius: number,
      hitAtDistance: (distance: number) => boolean,
    ) => {
      const step = 0.25;
      let lastHit = 0;
      let firstMiss = visualRadius;

      // Walk from the visual centre through the visual edge and outward. The
      // first non-owned point captures clipping and paint-order interception.
      for (let distance = step; distance <= visualRadius + 40; distance += step) {
        if (!hitAtDistance(distance)) {
          firstMiss = distance;
          break;
        }
        lastHit = distance;
      }

      // Refine the hit/miss boundary so integer pixel-centre quantisation does
      // not turn a genuine 24px target into a reported 23px target.
      for (let iteration = 0; iteration < 12; iteration += 1) {
        const candidate = (lastHit + firstMiss) / 2;
        if (hitAtDistance(candidate)) lastHit = candidate;
        else firstMiss = candidate;
      }
      return (lastHit + firstMiss) / 2;
    };

    const top = findBoundary(rect.height / 2, (distance) =>
      ownsHit(document.elementFromPoint(centreX, centreY - distance)));
    const bottom = findBoundary(rect.height / 2, (distance) =>
      ownsHit(document.elementFromPoint(centreX, centreY + distance)));
    const left = findBoundary(rect.width / 2, (distance) =>
      ownsHit(document.elementFromPoint(centreX - distance, centreY)));
    const right = findBoundary(rect.width / 2, (distance) =>
      ownsHit(document.elementFromPoint(centreX + distance, centreY)));

    return {
      visual: [Number(rect.width.toFixed(0)), Number(rect.height.toFixed(0))],
      effective: [
        // Chromium resolves hit-test coordinates to device-pixel cells. The
        // two boundary searches therefore include one shared pixel cell;
        // remove it to report the CSS-pixel distance between the boundaries.
        Number((left + right - 1).toFixed(0)),
        Number((top + bottom - 1).toFixed(0)),
      ],
    };
  });
}

async function expectSc258Target(label: string, control: Locator) {
  await expect(control).toBeVisible();
  const measured = await measureEffectiveHitExtent(control);
  console.log(
    `${label}: visual ${measured.visual[0]}x${measured.visual[1]}, `
      + `effective ${measured.effective[0]}x${measured.effective[1]}`,
  );
  expect.soft(measured.effective[0], `${label} effective clickable width`).toBeGreaterThanOrEqual(24);
  expect.soft(measured.effective[1], `${label} effective clickable height`).toBeGreaterThanOrEqual(24);
}

async function csrfHeaders(page: Page) {
  const response = await page.request.get('/api/v1/auth/csrf');
  await expect(response).toBeOK();
  const { csrf_token } = await response.json() as { csrf_token: string };
  return { 'X-CSRFToken': csrf_token };
}

async function findBookWithoutTxt(page: Page) {
  const listResponse = await page.request.get('/api/v1/books?per_page=100');
  await expect(listResponse).toBeOK();
  const list = await listResponse.json() as BookList;

  for (const { id } of list.items) {
    const detailResponse = await page.request.get(`/api/v1/books/${id}`);
    await expect(detailResponse).toBeOK();
    const detail = await detailResponse.json() as BookDetail;
    if (!detail.formats.some(({ format }) => format.toUpperCase() === TEMP_FORMAT)) return detail.id;
  }

  throw new Error('The fixture has no book that can receive the temporary TXT format');
}

async function bookHasFormat(page: Page, bookId: number, format: string) {
  const response = await page.request.get(`/api/v1/books/${bookId}`);
  if (!response.ok()) return false;
  const detail = await response.json() as BookDetail;
  return detail.formats.some(({ format: current }) => current.toUpperCase() === format);
}

test('focus-revealed card actions keep SC 2.5.8 targets on touch (2026-08-29 ruling)', async ({ page, isMobile }) => {
  test.skip(isMobile !== true, 'coarse-pointer target-size regression');

  await page.goto('/app/');
  const edit = page.locator('a[aria-label^="Edit "]').first();
  await expect(edit).toBeAttached();
  const card = edit.locator('..').locator('..');
  const details = card.locator('a[aria-label^="Open details for"]');
  const read = card.locator('a[aria-label^="Read "]');
  await details.focus();
  await expect.poll(() => edit.evaluate((node) => getComputedStyle(node).opacity), {
    message: 'focus-within reveals the touch card actions before target measurement',
  }).toBe('1');
  await expectSc258Target('Touch card Read now action', read);
  await expectSc258Target('Touch card Edit action', edit);

  const headers = await csrfHeaders(page);
  const books = await page.request.get('/api/v1/books?per_page=1');
  const { items } = await books.json() as BookList;
  expect(items.length, 'the target-size fixture needs a shelfable book').toBeGreaterThan(0);
  const created = await page.request.post('/api/v1/shelves', {
    headers,
    data: { name: `sc258-card-actions-${Date.now()}` },
  });
  expect(created.ok(), 'temporary shelf creation').toBeTruthy();
  const shelfId = ((await created.json()) as { id: number }).id;
  try {
    const added = await page.request.post(`/api/v1/shelves/${shelfId}/books/${items[0].id}`, { headers });
    expect(added.ok(), 'temporary shelf membership').toBeTruthy();
    await page.goto(`/app/shelf/${shelfId}`);
    const remove = page.getByRole('button', { name: 'Remove from shelf' });
    await expect(remove).toHaveCount(1);
    await remove.locator('..').locator('a[aria-label^="Open details for"]').focus();
    await expect.poll(() => remove.evaluate((node) => getComputedStyle(node).opacity), {
      message: 'focus-within reveals the touch Remove action before target measurement',
    }).toBe('1');
    await expectSc258Target('Touch card Remove action', remove);
  } finally {
    await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => undefined);
  }
});

test('compact controls expose at least a 24x24 effective clickable target', async ({ page }) => {
  // Keep the desktop browser context (fine pointer) while exercising the
  // responsive menu. Mobile emulation would activate the unrelated 44px
  // coarse-pointer rule and make the pre-fix control pass.
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto('/app/');
  await expectSc258Target('TopBar menu button', page.getByRole('button', { name: 'Open navigation' }));

  let appPasswordId: number | undefined;
  let formatBookId: number | undefined;
  let formatQueued = false;
  let mutationHeaders: Record<string, string> | undefined;

  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/app/account');

    const created = page.waitForResponse((response) =>
      response.url().includes('/api/v1/account/app-passwords')
        && response.request().method() === 'POST'
        && response.status() === 201);
    await page.getByLabel('App password label').fill(APP_PASSWORD_LABEL);
    await page.getByRole('button', { name: 'Generate' }).click();
    const createdPayload = await (await created).json() as { id: number };
    appPasswordId = createdPayload.id;

    const revokeButton = page.getByRole('button', { name: `Revoke ${APP_PASSWORD_LABEL}` });
    await expectSc258Target('Account revoke button', revokeButton);

    mutationHeaders = await csrfHeaders(page);
    formatBookId = await findBookWithoutTxt(page);
    const uploadResponse = await page.request.post(`/api/v1/books/${formatBookId}/formats`, {
      headers: mutationHeaders,
      multipart: {
        file: {
          name: 'sc258-target-precondition.txt',
          mimeType: 'text/plain',
          buffer: await readFile(TEMP_FORMAT_FILE),
        },
      },
    });
    await expect(uploadResponse).toBeOK();
    expect(uploadResponse.status()).toBe(202);
    formatQueued = true;

    await expect.poll(
      () => bookHasFormat(page, formatBookId!, TEMP_FORMAT),
      { message: `temporary ${TEMP_FORMAT} format was not attached by ingest`, timeout: 30_000 },
    ).toBe(true);

    await page.goto(`/app/book/${formatBookId}/edit`);
    const deleteButton = page.getByRole('button', { name: `Delete ${TEMP_FORMAT}` });
    await expectSc258Target('EditBook format delete button', deleteButton);
  } finally {
    if (formatQueued && formatBookId !== undefined) {
      const deleted = await page.request.post(
        `/api/v1/books/${formatBookId}/formats/${TEMP_FORMAT}/delete`,
        { headers: mutationHeaders ?? await csrfHeaders(page) },
      );
      expect(deleted.status(), 'temporary format cleanup').toBe(204);
      await expect.poll(
        () => bookHasFormat(page, formatBookId!, TEMP_FORMAT),
        { message: `temporary ${TEMP_FORMAT} format was not cleaned up` },
      ).toBe(false);
    }

    if (appPasswordId !== undefined) {
      const revoked = await page.request.post(
        `/api/v1/account/app-passwords/${appPasswordId}/delete`,
        { headers: mutationHeaders ?? await csrfHeaders(page) },
      );
      expect(revoked.status(), 'temporary app-password cleanup').toBe(204);
    }
  }
});
