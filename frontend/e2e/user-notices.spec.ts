import { expect, test, type Route } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

async function serveCurrentSpaBuild(page: import('@playwright/test').Page) {
  if (!process.env.E2E_CURRENT_SOURCE) return;
  await page.route(/\/app(?:\/.*)?$/, async (route) => {
    if (route.request().resourceType() !== 'document') return route.continue();
    const response = await route.fetch({ url: 'http://localhost:4174/static/app/' });
    await route.fulfill({ response });
  });
  // The focused test can run against Vite's current source while keeping the
  // established local fixture server as the authenticated API/data backend.
  await page.route('**/api/v1/**', async (route) => {
    const backend = new URL(route.request().url());
    backend.protocol = 'http:';
    backend.hostname = 'localhost';
    backend.port = '8086';
    const response = await route.fetch({ url: backend.toString() });
    await route.fulfill({ response });
  });
}

function notice(id: number, bookId: number, title: string) {
  return {
    id,
    type: 'kepub-package-repair',
    scope: 'book',
    occurred_at: '2026-08-15T12:00:00+00:00',
    book: { id: bookId, uuid: `book-${bookId}`, title },
    payload: { message_key: 'kepub_package_repaired', repair_version: 1 },
  };
}

async function json(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

test('shows the complete singular repair instruction for one affected book', async ({ page }) => {
  await serveCurrentSpaBuild(page);
  const repaired = [notice(100, 1, 'One repaired book')];
  await page.route(/\/api\/v1\/notices(?:\/[^?]*)?(?:\?.*)?$/, async (route) => json(route, {
    notices: repaired,
    summary: { count: repaired.length },
  }));

  await page.goto('/app');
  await expect(page.getByText('We repaired this book for your Kobo. If you were having trouble highlighting, try after sync — and if it still doesn’t work, remove the book from your Kobo and let it download again.', { exact: true })).toBeVisible();
});

test('aggregates repaired books and permanently bulk-dismisses explicit occurrences', async ({ page }) => {
  await serveCurrentSpaBuild(page);
  const repaired = [notice(101, 1, 'First repaired book'), notice(102, 2, 'Second repaired book')];
  let active = true;
  let dismissedIds: number[] = [];
  await page.route(/\/api\/v1\/notices(?:\/[^?]*)?(?:\?.*)?$/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === 'POST' && url.pathname.endsWith('/notices/dismiss')) {
      dismissedIds = request.postDataJSON().notice_ids;
      active = false;
      return json(route, { dismissed: dismissedIds.length, remaining: 0 });
    }
    return json(route, {
      notices: active ? repaired : [],
      summary: { count: active ? repaired.length : 0 },
    });
  });

  await page.goto('/app');
  await expect(page.getByText('We repaired 2 books for your Kobo. If you were having trouble highlighting, try after sync — and if it still doesn’t work, remove them from your Kobo and let them download again.', { exact: true })).toBeVisible();
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations.filter(
    (violation) => violation.impact === 'critical' || violation.impact === 'serious',
  )).toEqual([]);
  await page.getByText('Show affected books').click();
  await expect(page.getByRole('link', { name: 'First repaired book' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Second repaired book' })).toBeVisible();

  await page.getByRole('button', { name: 'Dismiss all 2 notices permanently' }).click();

  await expect(page.getByText('Kobo book compatibility repaired')).toHaveCount(0);
  expect(dismissedIds).toEqual([101, 102]);
});

test('chunks the reported 872-notice dismissal at the API cap', async ({ page }) => {
  await serveCurrentSpaBuild(page);
  const repaired = Array.from({ length: 872 }, (_, index) =>
    notice(index + 1, index + 1, `Repaired book ${index + 1}`));
  let active = true;
  const dismissedBatches: number[][] = [];
  await page.route(/\/api\/v1\/notices(?:\/[^?]*)?(?:\?.*)?$/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === 'POST' && url.pathname.endsWith('/notices/dismiss')) {
      const ids = request.postDataJSON().notice_ids as number[];
      dismissedBatches.push(ids);
      active = dismissedBatches.flat().length < repaired.length;
      return json(route, {
        dismissed: ids.length,
        remaining: repaired.length - dismissedBatches.flat().length,
      });
    }
    return json(route, {
      notices: active ? repaired : [],
      summary: { count: active ? repaired.length : 0 },
    });
  });

  await page.goto('/app');
  await page.getByRole('button', { name: 'Dismiss all 872 notices permanently' }).click();

  await expect(page.getByText('Kobo book compatibility repaired')).toHaveCount(0);
  expect(dismissedBatches.map((ids) => ids.length)).toEqual([500, 372]);
  expect(dismissedBatches.every((ids) => ids.length <= 500)).toBe(true);
  expect(dismissedBatches.flat()).toEqual(repaired.map((item) => item.id));
});

test('shows a visible error when bulk dismissal fails', async ({ page }) => {
  await serveCurrentSpaBuild(page);
  const repaired = [notice(301, 1, 'Still active')];
  await page.route(/\/api\/v1\/notices(?:\/[^?]*)?(?:\?.*)?$/, async (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'dismiss_failed', message: 'Nope' } }),
      });
    }
    return json(route, { notices: repaired, summary: { count: repaired.length } });
  });

  await page.goto('/app');
  await page.getByRole('button', { name: 'Dismiss permanently' }).click();

  const noticeBanner = page.locator('section[aria-labelledby="user-notice-title"]');
  await expect(noticeBanner.getByRole('alert')).toHaveText('Could not dismiss the notices. Please try again.');
  await expect(page.getByText('Kobo book compatibility repaired')).toBeVisible();
});

test('book detail presents and independently dismisses its book-scoped occurrence', async ({ page }) => {
  await serveCurrentSpaBuild(page);
  await page.goto('/app');
  const book = await page.evaluate(async () => {
    const response = await fetch('/api/v1/books?per_page=1');
    return (await response.json()).items?.[0] as { id: number; title: string } | undefined;
  });
  test.skip(!book, 'seed has no book');

  let active = true;
  let dismissedId: number | null = null;
  await page.route(/\/api\/v1\/notices(?:\/[^?]*)?(?:\?.*)?$/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === 'POST' && url.pathname.endsWith('/notices/201/dismiss')) {
      dismissedId = 201;
      active = false;
      return json(route, { dismissed: 1, remaining: 0 });
    }
    const isBookInbox = url.searchParams.get('book_id') === String(book!.id);
    const notices = active && isBookInbox ? [notice(201, book!.id, book!.title)] : [];
    return json(route, { notices, summary: { count: notices.length } });
  });

  await page.goto(`/app/book/${book!.id}`);
  await expect(page.getByText('We repaired this book for your Kobo. If you were having trouble highlighting, try after sync — and if it still doesn’t work, remove the book from your Kobo and let it download again. If you read it some other way, download it again to get the repaired copy.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Dismiss permanently' }).click();

  await expect(page.getByText('We repaired this book for your Kobo. If you were having trouble highlighting, try after sync — and if it still doesn’t work, remove the book from your Kobo and let it download again. If you read it some other way, download it again to get the repaired copy.', { exact: true })).toHaveCount(0);
  expect(dismissedId).toBe(201);
});
