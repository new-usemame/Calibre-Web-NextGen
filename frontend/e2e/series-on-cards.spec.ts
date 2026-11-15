import { test, expect, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/*
 * Fork issue #657 (also #673, and #855's follow-up) — the new UI dropped the
 * series name/number that the classic view showed under each book cover. Users
 * with series-heavy libraries navigate by series and could no longer tell which
 * series a book belonged to, or its position, without opening every book.
 *
 * The series name + position are restored on the book cards in the general
 * lists (main library grid, search, shelves). They are deliberately NOT repeated
 * in the series-detail view, where every card is the same series and the
 * position already shows as the #N badge — a repeated name there would be noise.
 *
 * Seed-resilient: probes the API for a book that has a series and skips (not
 * fails) when the library has none, so it stays green on any seed.
 */

interface Probe {
  bookId: number | null;
  title: string | null;
  series: string | null;
  seriesId: number | null;
  apiSeriesId: number | null;
}

async function probeSeriesBook(page: Page): Promise<Probe> {
  return await page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=250', { credentials: 'same-origin' });
    if (!r.ok) return { bookId: null, title: null, series: null, seriesId: null, apiSeriesId: null };
    const d = await r.json();
    const hit = (d.items || []).find(
      (b: { series?: string | null }) => b.series != null && b.series !== '',
    );
    if (!hit) return { bookId: null, title: null, series: null, seriesId: null, apiSeriesId: null };
    const sr = await fetch('/api/v1/series?per_page=250', { credentials: 'same-origin' });
    if (!sr.ok) return {
      bookId: hit.id, title: hit.title, series: hit.series, seriesId: null,
      apiSeriesId: hit.series_id ?? null,
    };
    const sd = await sr.json();
    const matched = (sd.items || []).find((s: { name?: string }) => s.name === hit.series);
    return {
      bookId: hit.id, title: hit.title, series: hit.series,
      seriesId: matched?.id ?? null,
      apiSeriesId: hit.series_id ?? null,
    };
  });
}

test.describe('#657 series on book cards', () => {
  test('a book that has a series shows its series name on the card', async ({ page }) => {
    await page.goto('/app/');
    const { bookId, title, series } = await probeSeriesBook(page);
    test.skip(!bookId, 'seed has no book with a series');

    // Search for the exact title so the target card is rendered in a small
    // result grid (the full library grid is virtualized, so a specific book may
    // not be in the initial window). Search results use the same BookCard in
    // general-list mode, so the series line is the thing under test here.
    await page.goto(`/app/?q=${encodeURIComponent(title!)}`);
    const card = page.locator(`[data-book-id="${bookId}"]`);
    await expect(card.locator(`a[href$="/book/${bookId}"]`)).toBeVisible();
    const seriesLine = card.getByTestId('book-card-series');
    await expect(seriesLine).toBeVisible();
    await expect(seriesLine).toContainText(series!);
  });

  test('book-list data carries the series relation ID used by the card link', async ({ page }) => {
    await page.goto('/app/');
    const { bookId, seriesId, apiSeriesId } = await probeSeriesBook(page);
    test.skip(!bookId || !seriesId, 'seed has no book with a resolvable series');
    test.info().annotations.push({
      type: 'series-id',
      description: `unmodified book-list item ${bookId}: series_id=${apiSeriesId}; series endpoint id=${seriesId}`,
    });
    expect(apiSeriesId, 'the unmodified list response exposes the related series ID')
      .toBe(seriesId);
  });

  test('series is a direct sibling link with keyboard and selection behavior', async ({ page }, testInfo) => {
    await page.goto('/app/');
    const { bookId, title, series, seriesId } = await probeSeriesBook(page);
    test.skip(!bookId || !seriesId, 'seed has no book with a resolvable series');

    // Only project the known series relation ID into the list response. The
    // book, series, catalog route, and destination remain real server data; this
    // makes the UI test portable against older fixture images while the
    // serializer unit test covers the production API field.
    await page.route('**/api/v1/books**', async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      for (const item of body.items || []) {
        if (item.id === bookId) item.series_id = seriesId;
      }
      await route.fulfill({ response, json: body });
    });
    await page.setViewportSize(testInfo.project.name === 'mobile'
      ? { width: 375, height: 812 } : { width: 1280, height: 800 });
    const searchResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname.endsWith('/api/v1/books') && url.searchParams.get('search') === title;
    });
    await page.goto(`/app/?q=${encodeURIComponent(title!)}`);
    const listed = await (await searchResponse).json() as { items?: { id: number }[] };
    expect(listed.items?.some((item) => item.id === bookId), 'the real search result contains the probed book').toBe(true);

    const card = page.locator(`[data-book-id="${bookId}"]`);
    const bookLink = card.locator(`a[href$="/book/${bookId}"]`);
    const seriesLink = card.locator(`[data-testid="book-card-series"][href$="/series/${seriesId}"]`);
    await expect(bookLink).toBeVisible();
    await expect(seriesLink).toBeVisible();
    await expect(seriesLink).toContainText(series!);
    await expect(seriesLink).toContainText(/#\d+/);
    const wrapper = seriesLink.locator('xpath=..');
    expect(await wrapper.locator('a a, button a, a button').count(), 'card actions are siblings, never nested').toBe(0);

    const box = await seriesLink.boundingBox();
    expect(box, 'series target has a rendered box').not.toBeNull();
    expect(box!.width, 'series target is at least 24 CSS pixels wide on a phone').toBeGreaterThanOrEqual(24);
    expect(box!.height, 'series target is at least 24 CSS pixels tall on a phone').toBeGreaterThanOrEqual(24);

    // The book detail link stays first; the independent series destination is
    // the next tab stop and Enter activates the advertised series URL.
    await bookLink.focus();
    await page.keyboard.press('Tab');
    await expect(seriesLink).toBeFocused();

    await page.emulateMedia({ reducedMotion: 'reduce' });
    for (const theme of ['dark', 'light'] as const) {
      await page.evaluate(async (slug) => {
        document.documentElement.setAttribute('data-theme', slug);
        await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      }, theme);
      const axe = await new AxeBuilder({ page })
        .include('[data-testid="book-card-series"]')
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
        .analyze();
      expect(
        axe.violations.filter((v) => ['critical', 'serious'].includes(v.impact ?? '')),
        `series destination passes axe in ${theme} theme`,
      ).toEqual([]);
      await page.screenshot({
        path: test.info().outputPath(`series-link-focus-${testInfo.project.name}-${theme}.jpg`),
        type: 'jpeg', quality: 75,
      });
    }

    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(new RegExp(`/series/${seriesId}$`));

    // Selection mode remains one toggle per card; it must not expose a nested
    // series link that could navigate instead of selecting the book.
    await page.goto(`/app/?q=${encodeURIComponent(title!)}`);
    await page.getByRole('button', { name: 'Select', exact: true }).click();
    const selectCard = card.getByRole('button', { name: `Select ${title}`, exact: true });
    await expect(selectCard).toBeVisible();
    // The selection toggle is unique. A writable shelf may also expose the
    // separate drag/picker grip; neither control may contain another control.
    await expect(selectCard).toHaveCount(1);
    await expect(card.locator('button button, button a, a button')).toHaveCount(0);
    await expect(selectCard.locator('a')).toHaveCount(0);
    await expect(selectCard.getByTestId('book-card-series')).toContainText(series!);
    await selectCard.click();
    await expect(page.getByRole('button', { name: `Deselect ${title}`, exact: true }))
      .toHaveAttribute('aria-pressed', 'true');
    for (const theme of ['dark', 'light'] as const) {
      await page.evaluate(async (slug) => {
        document.documentElement.setAttribute('data-theme', slug);
        await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      }, theme);
      const axe = await new AxeBuilder({ page })
        .include(`[data-book-id="${bookId}"]`)
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
        .analyze();
      expect(
        axe.violations.filter((v) => ['critical', 'serious'].includes(v.impact ?? '')),
        `selection card passes axe in ${theme} theme`,
      ).toEqual([]);
      await page.screenshot({
        path: test.info().outputPath(`series-selection-${testInfo.project.name}-${theme}.jpg`),
        type: 'jpeg', quality: 75,
      });
    }
  });

  test('series-detail view does not repeat the series name on every card', async ({ page }) => {
    await page.goto('/app/');
    // Discover a series id so we can open its detail view.
    const seriesId = await page.evaluate(async () => {
      const r = await fetch('/api/v1/series?per_page=1', { credentials: 'same-origin' });
      if (!r.ok) return null;
      const d = await r.json();
      const first = (d.items || [])[0];
      return first ? first.id : null;
    });
    test.skip(!seriesId, 'seed has no series');

    await page.goto(`/app/series/${seriesId}`);
    // In the series-detail grid the redundant name line is suppressed; the #N
    // position badge carries the ordering instead.
    await expect(page.getByTestId('book-card-series')).toHaveCount(0);
  });
});
