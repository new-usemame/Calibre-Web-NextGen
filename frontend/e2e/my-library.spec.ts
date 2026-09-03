// Change this import to './fixtures' when ci/1927 reaches main; its
// secondaryUser contract is identical.
import { test, expect, type SecondaryUserSession } from './my-library-fixtures';
import type { Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { readFile } from 'node:fs/promises';

interface MePayload {
  id: number;
  library_mode: 'monolibrary' | 'personal_library';
  role: Record<string, boolean>;
}

interface GlobalBook {
  id: number;
  title: string;
  in_my_library: boolean;
  cover_url?: string | null;
}

interface LibraryBook {
  id: number;
  title: string;
  formats: string[];
}

interface BookDetailPayload {
  id: number;
  title: string;
  formats: Array<{ format: string }>;
  in_my_library: boolean;
}

interface KoboTargetBook {
  id: number;
  title: string;
  uuid: string;
}

type KoboEntitlement = {
  BookEntitlement: { RevisionId: string; IsRemoved: boolean };
  BookMetadata?: {
    Title?: string;
    DownloadUrls?: Array<{ Url?: string }>;
  };
};

type KoboSyncEntry = {
  NewEntitlement?: KoboEntitlement;
  ChangedEntitlement?: KoboEntitlement;
};

async function csrfHeaders(page: Page) {
  const response = await page.request.get('/api/v1/auth/csrf');
  expect(response.ok()).toBeTruthy();
  const payload = (await response.json()) as { csrf_token: string };
  return { 'X-CSRFToken': payload.csrf_token };
}

async function setManagedMode(
  adminPage: Page,
  userId: number,
  libraryMode: MePayload['library_mode'],
  browseGlobal = true,
  roles: Record<string, boolean> = {},
) {
  const response = await adminPage.request.post(`/api/v1/admin/users/${userId}`, {
    headers: await csrfHeaders(adminPage),
    data: { roles: { browse_global: browseGlobal, ...roles }, library_mode: libraryMode },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function removeMembership(page: Page, bookId: number) {
  const response = await page.request.delete(`/api/v1/books/${bookId}/my-library`, {
    headers: await csrfHeaders(page),
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function addMembership(page: Page, bookId: number) {
  const response = await page.request.put(`/api/v1/books/${bookId}/my-library`, {
    headers: await csrfHeaders(page),
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function firstGlobalBooks(page: Page): Promise<GlobalBook[]> {
  const response = await page.request.get('/api/v1/library/global?sort=new&per_page=10');
  expect(response.ok(), await response.text()).toBeTruthy();
  return ((await response.json()) as { items: GlobalBook[] }).items;
}

async function currentLibraryBooks(page: Page): Promise<LibraryBook[]> {
  const response = await page.request.get('/api/v1/books?sort=new&per_page=200');
  expect(response.ok(), await response.text()).toBeTruthy();
  return ((await response.json()) as { items: LibraryBook[] }).items;
}

async function keepOnlyMembership(page: Page, keepBookId: number) {
  const books = await currentLibraryBooks(page);
  const removeIds = books.map((book) => book.id).filter((id) => id !== keepBookId);
  if (removeIds.length) {
    const response = await page.request.post('/api/v1/books/my-library/batch', {
      headers: await csrfHeaders(page),
      data: { operation: 'remove', book_ids: removeIds },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    const result = (await response.json()) as { succeeded_ids: number[]; failed_ids: number[] };
    expect(result.failed_ids).toEqual([]);
    expect(result.succeeded_ids).toEqual(removeIds);
  }
  expect((await currentLibraryBooks(page)).map((book) => book.id)).toEqual([keepBookId]);
}

async function createBrowserHighlight(page: Page, bookId: number, marker: string) {
  const response = await page.request.post(`/annotations/${bookId}`, {
    headers: {
      ...await csrfHeaders(page),
      'X-CWNG-Webreader-Installation-Id': '19470000-0000-4000-8000-000000000001',
    },
    data: {
      cfi_range: 'epubcfi(/6/4!/4/2,/1:0,/1:9)',
      highlighted_text: marker,
      highlight_color: 'yellow',
      note_text: `${marker} note`,
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  return ((await response.json()) as { annotation_id: string }).annotation_id;
}

async function deleteBrowserHighlight(page: Page, bookId: number, annotationId: string) {
  const response = await page.request.delete(
    `/annotations/${bookId}/${encodeURIComponent(annotationId)}`,
    { headers: await csrfHeaders(page) },
  );
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function downloadText(page: Page, linkName: string) {
  const link = page.getByRole('link', { name: linkName, exact: true });
  if (!await link.isVisible()) {
    const desktopExport = page.locator('summary').filter({ hasText: /^\s*Export\s*$/ });
    if (await desktopExport.isVisible()) await desktopExport.click();
    else await page.getByRole('button', { name: 'Import and export' }).click();
  }
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    link.click(),
  ]);
  const path = await download.path();
  expect(path, `${linkName} export must be a real browser download`).not.toBeNull();
  return {
    filename: download.suggestedFilename(),
    body: await readFile(path!, 'utf8'),
  };
}

async function koboAuthToken(page: Page, userId: number) {
  const response = await page.request.get(`/kobo_auth/generate_auth_token/${userId}`);
  expect(response.ok(), await response.text()).toBeTruthy();
  const match = (await response.text()).match(/\b[a-f0-9]{32}\b/i);
  expect(match, 'Kobo setup must mint a token for this test-owned account').not.toBeNull();
  return match![0];
}

async function koboSync(
  page: Page,
  token: string,
  deviceId: string,
  incomingSyncToken?: string,
) {
  const headers: Record<string, string> = {
    'x-kobo-deviceid': deviceId,
    'x-kobo-devicemodel': 'CWNG E2E Kobo',
    'x-kobo-appversion': '4.45.23684',
  };
  if (incomingSyncToken) headers['x-kobo-synctoken'] = incomingSyncToken;
  const response = await page.request.get(`/kobo/${token}/v1/library/sync`, { headers });
  expect(response.ok(), await response.text()).toBeTruthy();
  const outgoingSyncToken = response.headers()['x-kobo-synctoken'];
  expect(outgoingSyncToken, 'Kobo sync must return its opaque continuation token').toBeTruthy();
  return {
    entries: (await response.json()) as KoboSyncEntry[],
    syncToken: outgoingSyncToken,
  };
}

function entitlementFor(entries: KoboSyncEntry[], uuid: string) {
  return entries.find((entry) => {
    const entitlement = entry.NewEntitlement ?? entry.ChangedEntitlement;
    return entitlement?.BookEntitlement.RevisionId === uuid;
  });
}

async function expectNoSeriousAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze();
  const failures = results.violations
    .filter((violation) => violation.impact === 'critical' || violation.impact === 'serious')
    .map((violation) => {
      const targets = violation.nodes.flatMap((node) => node.target).join(', ');
      return `${violation.id}: ${violation.help}${targets ? ` (${targets})` : ''}`;
    });
  expect(failures).toEqual([]);
}

test.describe('My Library', () => {
  test('a non-member Global Library card loads the global cover', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }) => {
    const page = secondaryUser.page;
    let coveredBook: GlobalBook | undefined;

    try {
      await setManagedMode(adminPage, secondaryUser.id, 'personal_library');
      const books = await firstGlobalBooks(page);
      coveredBook = books.find((book) => !!book.cover_url);
      test.skip(!coveredBook, 'seed library needs at least one book with a cover');
      await removeMembership(page, coveredBook!.id);

      const listing = await page.request.get('/api/v1/library/global?sort=new&per_page=200');
      expect(listing.ok(), await listing.text()).toBeTruthy();
      const listed = ((await listing.json()) as { items: GlobalBook[] }).items
        .find((book) => book.id === coveredBook!.id);
      expect(listed?.in_my_library).toBe(false);
      expect(listed?.cover_url).toBeTruthy();

      const cover = await page.request.get(listed!.cover_url!);
      expect(cover.ok(), await cover.text()).toBeTruthy();
      expect(cover.headers()['content-type']).toMatch(/^image\/(jpeg|webp)/);

      await page.goto('/app/global');
      await expect(page.getByRole('img', { name: coveredBook!.title })).toBeVisible();
    } finally {
      if (coveredBook) await addMembership(page, coveredBook.id).catch(() => undefined);
    }
  });

  test('a non-member Global Library card opens read-only personal state with global editing', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }) => {
    const page = secondaryUser.page;
    let book: GlobalBook | undefined;

    try {
      await setManagedMode(adminPage, secondaryUser.id, 'personal_library', true, {
        edit: true,
        delete_books: true,
      });
      // The fixture first loaded this account with viewer-only roles. A fresh
      // document discards that pre-update React Query cache and exercises the
      // same auth bootstrap a real role change receives on its next page load.
      await page.goto('/app/global');
      const me = (await page.request.get('/api/v1/auth/me').then((r) => r.json())) as MePayload;
      expect(me.role.admin, 'global editing must not depend on the admin role').toBe(false);
      expect(me.role.edit, 'the test account must exercise the editor gate').toBe(true);
      expect(me.role.delete_books, 'the test account must exercise the combined delete gate').toBe(true);
      [book] = await firstGlobalBooks(page);
      test.skip(!book, 'seed library needs at least one book');
      await removeMembership(page, book!.id);

      await page.goto('/app/global');
      await page.getByRole('link', { name: `Open details for ${book!.title}` }).click();
      await expect(page).toHaveURL(new RegExp(`/app/book/${book!.id}$`));
      await expect(page.getByRole('heading', { level: 1, name: book!.title })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Add to my library' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Edit', exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Delete from the global library' })).toBeVisible();

      await expect(page.getByRole('button', { name: 'Remove from my library' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Add to shelf' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: /Mark as (?:un)?read/ })).toHaveCount(0);
      await expect(page.getByRole('progressbar', { name: 'Reading progress' })).toHaveCount(0);
      await expect(page.getByRole('link', { name: /^Highlights/ })).toHaveCount(0);

      const detailResponse = await page.request.get(`/api/v1/books/${book!.id}`);
      expect(detailResponse.ok(), await detailResponse.text()).toBeTruthy();
      const detail = (await detailResponse.json()) as BookDetailPayload & {
        read: boolean;
        archived: boolean;
        favorited: boolean;
        hidden: boolean;
        in_progress: boolean;
        annotation_count: number;
        kosync_progress: number | null;
      };
      expect(detail).toMatchObject({
        in_my_library: false,
        read: false,
        archived: false,
        favorited: false,
        hidden: false,
        in_progress: false,
        annotation_count: 0,
        kosync_progress: null,
      });

      const metadata = await page.request.get(`/api/v1/books/${book!.id}/metadata`);
      expect(metadata.ok(), await metadata.text()).toBeTruthy();
      await expectNoSeriousAxeViolations(page);
    } finally {
      if (book) await addMembership(page, book.id).catch(() => undefined);
    }
  });

  test('two real accounts see independent selections and can discover missing books', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }, testInfo) => {
    const adminMe = (await adminPage.request.get('/api/v1/auth/me').then((r) => r.json())) as MePayload;
    const originalMode = adminMe.library_mode;
    const originalBrowseGlobal = !!adminMe.role.browse_global;
    const secondaryPage = secondaryUser.page;
    let books: GlobalBook[] = [];

    try {
      await setManagedMode(adminPage, adminMe.id, 'personal_library');
      await setManagedMode(adminPage, secondaryUser.id, 'personal_library');
      books = await firstGlobalBooks(adminPage);
      const pairOffset = testInfo.project.name === 'mobile' ? 2 : 0;
      test.skip(books.length < pairOffset + 2, 'seed library needs two books per viewport project');
      const [adminBook, secondaryBook] = books.slice(pairOffset, pairOffset + 2);

      // #2126 deliberately seeds each account once, but the assertion must not
      // rely on whatever selection that account inherited. Establish both
      // positive memberships before curating the opposite records away. Each
      // viewport project owns a different pair because both projects run at the
      // same time against one server-side primary account.
      await addMembership(adminPage, adminBook.id);
      await addMembership(secondaryPage, secondaryBook.id);
      await removeMembership(adminPage, secondaryBook.id);
      await removeMembership(secondaryPage, adminBook.id);

      const adminIds = ((await adminPage.request.get('/api/v1/books?per_page=200').then((r) => r.json())) as {
        items: Array<{ id: number }>;
      }).items.map((book) => book.id);
      const secondaryIds = ((await secondaryPage.request.get('/api/v1/books?per_page=200').then((r) => r.json())) as {
        items: Array<{ id: number }>;
      }).items.map((book) => book.id);
      expect(adminIds).toContain(adminBook.id);
      expect(adminIds).not.toContain(secondaryBook.id);
      expect(secondaryIds).toContain(secondaryBook.id);
      expect(secondaryIds).not.toContain(adminBook.id);

      await adminPage.goto('/app');
      const adminGrid = adminPage.getByTestId('catalog-grid');
      await expect(adminGrid.getByRole('link', { name: `Open details for ${adminBook.title}` })).toBeVisible();
      await expect(adminGrid.getByRole('link', { name: `Open details for ${secondaryBook.title}` })).toHaveCount(0);

      await secondaryPage.goto('/app');
      const secondaryGrid = secondaryPage.getByTestId('catalog-grid');
      await expect(secondaryGrid.getByRole('link', { name: `Open details for ${secondaryBook.title}` })).toBeVisible();
      await expect(secondaryGrid.getByRole('link', { name: `Open details for ${adminBook.title}` })).toHaveCount(0);

      await expect(adminPage.getByRole('link', { name: 'Global Library', includeHidden: true }))
        .toHaveAttribute('href', '/app/global');
      await adminPage.goto('/app/global');
      await expect(adminPage).toHaveURL(/\/app\/global/);
      await expect(adminPage.getByRole('heading', { name: 'Global Library' })).toBeVisible();
      await adminPage.getByRole('button', { name: 'Not in your library' }).click();
      await expect(adminPage.getByRole('button', { name: `Add ${secondaryBook.title} to my library` })).toBeVisible();
      await expect(adminPage.getByRole('button', { name: `Add ${adminBook.title} to my library` })).toHaveCount(0);
      await expectNoSeriousAxeViolations(adminPage);

      await adminPage.getByRole('button', { name: `Add ${secondaryBook.title} to my library` }).click();
      await expect(adminPage.getByText('Added to your library', { exact: true })).toBeAttached();
      await adminPage.goto('/app');
      await expect(adminPage.getByTestId('catalog-grid').getByRole('link', {
        name: `Open details for ${secondaryBook.title}`,
      })).toBeVisible();

      await secondaryPage.goto('/app/account');
      await expect(secondaryPage.getByRole('radio', { name: /My Library/ })).toBeChecked();
      await expectNoSeriousAxeViolations(secondaryPage);
      const wholeLibraryConfirm = secondaryPage.waitForEvent('dialog').then(async (dialog) => {
        expect(dialog.message()).toContain('Your selection is kept exactly as you left it');
        await dialog.accept();
      });
      await Promise.all([
        wholeLibraryConfirm,
        secondaryPage.getByRole('radio', { name: /The global library/ }).click(),
      ]);
      await expect(secondaryPage.getByRole('radio', { name: /The global library/ })).toBeChecked();
      const selectionConfirm = secondaryPage.waitForEvent('dialog').then(async (dialog) => {
        expect(dialog.message()).toContain('Your library goes back to the books you had chosen');
        await dialog.accept();
      });
      await Promise.all([
        selectionConfirm,
        secondaryPage.getByRole('radio', { name: /My Library/ }).click(),
      ]);
      await secondaryPage.goto('/app');
      await expect(secondaryPage.getByTestId('catalog-grid').getByRole('link', {
        name: `Open details for ${adminBook.title}`,
      })).toHaveCount(0);
    } finally {
      if (books[0]) await addMembership(secondaryPage, books[0].id).catch(() => undefined);
      if (books[1]) await addMembership(adminPage, books[1].id).catch(() => undefined);
      await setManagedMode(adminPage, adminMe.id, originalMode, originalBrowseGlobal);
    }
  });

  test('intro dismissal is server-side and survives a fresh browser page', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }) => {
    await setManagedMode(adminPage, secondaryUser.id, 'personal_library');
    const page = secondaryUser.page;
    await page.goto('/app');
    const introduction = page.getByText('New: My Library');
    await expect(introduction).toBeVisible();
    await page.getByRole('button', { name: 'Dismiss library introduction' }).click();
    await expect(introduction).toHaveCount(0);
    const freshPage = await secondaryUser.context.newPage();
    await freshPage.goto('/app');
    await expect(freshPage.getByText('New: My Library')).toHaveCount(0);
    await freshPage.close();
  });

  test('shelf controls appear only after adding an unowned book to My Library', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }) => {
    await setManagedMode(adminPage, secondaryUser.id, 'personal_library');
    const page = secondaryUser.page;
    const books = await firstGlobalBooks(page);
    test.skip(books.length < 1, 'seed library needs at least one book');
    const book = books[0];
    const shelfName = `e2e-my-library-${Date.now()}`;
    const created = await page.request.post('/api/v1/shelves', {
      headers: await csrfHeaders(page),
      data: { name: shelfName },
    });
    expect(created.status(), await created.text()).toBe(201);
    const shelfId = ((await created.json()) as { id: number }).id;
    await removeMembership(page, book.id);

    try {
      await page.goto(`/app/book/${book.id}`);
      const shelfTrigger = page.getByRole('button', { name: 'Add to shelf' });
      const addButton = page.getByRole('button', { name: 'Add to my library' });
      await expect(addButton).toBeVisible();
      await expect(shelfTrigger).toHaveCount(0);
      await addButton.click();
      await expect.poll(async () => {
        const response = await page.request.get(`/api/v1/books/${book.id}`);
        return ((await response.json()) as BookDetailPayload).in_my_library;
      }).toBe(true);
      await expect(shelfTrigger).toBeVisible();
      await shelfTrigger.click();

      // The disclosure follows the shared popover keyboard contract.
      await page.keyboard.press('Escape');
      await expect(shelfTrigger).toBeFocused();
      await shelfTrigger.click();
      await page.getByRole('button', { name: shelfName }).click();

      const library = (await page.request.get('/api/v1/books?per_page=200').then((r) => r.json())) as {
        items: Array<{ id: number }>;
      };
      expect(library.items.map((item) => item.id)).toContain(book.id);
      const shelves = (await page.request.get(`/api/v1/books/${book.id}/shelves`).then((r) => r.json())) as {
        shelf_ids: number[];
      };
      expect(shelves.shelf_ids).toContain(shelfId);
      await expectNoSeriousAxeViolations(page);
    } finally {
      await page.request.post(`/api/v1/shelves/${shelfId}/delete`, {
        headers: await csrfHeaders(page),
      }).catch(() => undefined);
      await addMembership(page, book.id).catch(() => undefined);
    }
  });

  test('an administrator can add a specific book to a managed selection', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }) => {
    await setManagedMode(adminPage, secondaryUser.id, 'personal_library', false);
    const books = ((await secondaryUser.page.request.get(
      '/api/v1/books?per_page=1',
    ).then((r) => r.json())) as { items: GlobalBook[] }).items;
    test.skip(books.length < 1, 'seed library needs at least one book');
    const book = books[0];
    await removeMembership(secondaryUser.page, book.id);

    try {
      await adminPage.goto('/app/admin');
      const card = adminPage.locator('section').filter({
        has: adminPage.getByText(secondaryUser.username, { exact: true }),
      }).first();
      await expect(card.getByText(
        "Without the global-browse role, only an administrator can add books to this user's library.",
      )).toBeVisible();
      await card.getByRole('spinbutton', { name: 'Book ID' }).fill(String(book.id));
      await card.getByRole('button', { name: 'Add book to this library' }).click();
      await expect(adminPage.getByText(
        `Added book ${book.title} to ${secondaryUser.username}.`,
        { exact: true },
      )).toBeVisible();

      const library = (await secondaryUser.page.request.get(
        '/api/v1/books?per_page=200',
      ).then((r) => r.json())) as { items: Array<{ id: number }> };
      expect(library.items.map((item) => item.id)).toContain(book.id);
      await expectNoSeriousAxeViolations(adminPage);
    } finally {
      await addMembership(secondaryUser.page, book.id).catch(() => undefined);
    }
  });

  test('removed books retain their annotation page, exports, and device views', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }) => {
    await setManagedMode(adminPage, secondaryUser.id, 'personal_library');
    const page = secondaryUser.page;
    const books = await currentLibraryBooks(page);
    expect(books.length, 'seed library needs at least one book').toBeGreaterThan(0);
    const book = books[0];
    const marker = `retained-annotation-${secondaryUser.id}`;
    let annotationId: string | undefined;

    try {
      annotationId = await createBrowserHighlight(page, book.id, marker);

      // Drive the same confirmation and membership mutation a person uses.
      await page.goto(`/app/book/${book.id}`);
      const confirmation = page.waitForEvent('dialog').then(async (dialog) => {
        expect(dialog.message()).toContain(`Remove "${book.title}" from your library?`);
        expect(dialog.message()).toContain('your highlights, notes and reading progress are kept');
        await dialog.accept();
      });
      await Promise.all([
        confirmation,
        page.getByRole('button', { name: 'Remove from my library' }).click(),
      ]);
      await expect(page.getByText('Removed from your library', { exact: true })).toBeAttached();
      await expect.poll(async () => {
        const response = await page.request.get(`/api/v1/books/${book.id}`);
        return ((await response.json()) as BookDetailPayload).in_my_library;
      }).toBe(false);

      // The retained highlight's origin device is the real recovery route to
      // a removed book: device view -> book annotations -> browser downloads.
      await page.goto('/app/account/devices');
      await page.getByRole('link', { name: /^Web reader(?: \d+)?$/ }).first().click();
      const deviceRow = page.getByRole('listitem').filter({ hasText: marker });
      await expect(deviceRow).toBeVisible();
      await expect(deviceRow).toContainText(`${marker} note`);
      await deviceRow.getByRole('link', { name: book.title, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`/app/book/${book.id}/annotations$`));
      await expect(page.getByText(marker, { exact: true })).toBeVisible();
      await expect(page.getByText(`${marker} note`, { exact: true })).toBeVisible();

      for (const [linkName, suffix] of [
        ['Markdown', '.md'],
        ['CSV', '.csv'],
        ['JSON', '.json'],
      ] as const) {
        const exported = await downloadText(page, linkName);
        expect(exported.filename).toMatch(new RegExp(`\\${suffix}$`, 'i'));
        expect(exported.body).toContain(marker);
      }
    } finally {
      // The red (#2057-reverted) run cannot read or delete the annotation while
      // the book is absent. Restore membership first so teardown proves cleanup.
      await addMembership(page, book.id).catch(() => undefined);
      if (annotationId) {
        await deleteBrowserHighlight(page, book.id, annotationId).catch(() => undefined);
      }
    }
  });

  test('Kobo sync archives a book after it is removed through My Library', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }) => {
    await setManagedMode(adminPage, secondaryUser.id, 'personal_library');
    const page = secondaryUser.page;
    const me = (await page.request.get('/api/v1/auth/me').then((response) => response.json())) as {
      features?: { kobo_sync?: boolean };
    };
    // config_kobo_sync is a server capability (config_sql.py:105, default False)
    // that the SPA admin API deliberately cannot write — deep config stays on
    // the legacy pages. No CI fixture enables it, so this cell runs against a
    // Kobo-enabled container (the local rig) and skips loudly elsewhere rather
    // than asserting a precondition the environment cannot satisfy.
    //
    // The skip is the honest state, not a silence: flipping it on and driving
    // the legacy form mid-suite would mutate GLOBAL server config while a second
    // worker is running, which trades a coverage gap for a race. That CI never
    // exercises Kobo sync at all is tracked separately as its own finding.
    test.skip(
      me.features?.kobo_sync !== true,
      'server has config_kobo_sync disabled; this cell needs a Kobo-enabled container',
    );
    const books = await currentLibraryBooks(page);
    const token = await koboAuthToken(page, secondaryUser.id);
    const deviceId = secondaryUser.id.toString(16).padStart(64, '0');
    let detail: KoboTargetBook | undefined;

    try {
      // First page delivers the book; presenting its returned token on the
      // second request acknowledges that delivery into the server's sync set.
      const delivered = await koboSync(page, token, deviceId);
      // Select from what the real endpoint actually delivered, then resolve
      // its download URL back to the SPA book id. The API's detail serializer
      // intentionally does not expose Calibre UUIDs, while Kobo's wire
      // metadata carries both identities (RevisionId + /download/<book id>/).
      for (const entry of delivered.entries) {
        const entitlement = entry.NewEntitlement ?? entry.ChangedEntitlement;
        if (!entitlement || entitlement.BookEntitlement.IsRemoved) continue;
        const downloadUrl = entitlement.BookMetadata?.DownloadUrls?.[0]?.Url ?? '';
        const idMatch = downloadUrl.match(/\/download\/(\d+)\//);
        if (!idMatch) continue;
        const candidate = books.find((book) => book.id === Number(idMatch[1]));
        if (!candidate) continue;
        detail = {
          id: candidate.id,
          title: candidate.title,
          uuid: entitlement.BookEntitlement.RevisionId,
        };
        if (detail) break;
      }
      expect(detail, 'the real Kobo page must contain a current My Library book').toBeDefined();
      const deliveredBook = entitlementFor(delivered.entries, detail!.uuid);
      expect(deliveredBook, `${detail!.title} must enter the real Kobo sync set`).toBeDefined();
      expect((deliveredBook!.NewEntitlement ?? deliveredBook!.ChangedEntitlement)!
        .BookEntitlement.IsRemoved).toBe(false);
      const acknowledged = await koboSync(page, token, deviceId, delivered.syncToken);

      await page.goto(`/app/book/${detail!.id}`);
      const confirmation = page.waitForEvent('dialog').then(async (dialog) => {
        expect(dialog.message()).toContain("it also leaves your Kobo at its next sync");
        await dialog.accept();
      });
      await Promise.all([
        confirmation,
        page.getByRole('button', { name: 'Remove from my library' }).click(),
      ]);
      await expect(page.getByText('Removed from your library', { exact: true })).toBeAttached();

      const afterRemoval = await koboSync(page, token, deviceId, acknowledged.syncToken);
      const archived = afterRemoval.entries.find((entry) =>
        entry.ChangedEntitlement?.BookEntitlement.RevisionId === detail!.uuid);
      expect(archived, `${detail!.title} must be explicitly archived on the next Kobo sync`).toBeDefined();
      expect(archived!.ChangedEntitlement!.BookEntitlement.IsRemoved).toBe(true);
      expect(afterRemoval.entries.some((entry) =>
        entry.NewEntitlement?.BookEntitlement.RevisionId === detail!.uuid)).toBe(false);
    } finally {
      if (detail) await addMembership(page, detail.id).catch(() => undefined);
    }
  });

  test('managed personal libraries hide Global Library and refuse removing the last book', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }) => {
    await setManagedMode(adminPage, secondaryUser.id, 'personal_library', false);
    const page = secondaryUser.page;
    const books = await currentLibraryBooks(page);
    expect(books.length, 'seed library needs at least one book').toBeGreaterThan(0);
    const book = books[0];
    await keepOnlyMembership(page, book.id);

    const me = (await page.request.get('/api/v1/auth/me').then((response) => response.json())) as MePayload;
    expect(me.library_mode).toBe('personal_library');
    expect(me.role.browse_global).toBe(false);
    await page.goto('/app');
    await expect(page.getByRole('link', { name: 'Global Library', includeHidden: true })).toHaveCount(0);
    await expect(page.getByTestId('catalog-count')).toHaveText('1 books');

    // Use the structured bulk lane: it presents the backend policy reason to
    // the user instead of collapsing the rejection into a generic error.
    await page.getByRole('button', { name: 'Select', exact: true }).click();
    await page.getByRole('button', { name: `Select ${book.title}`, exact: true }).click();
    const confirmation = page.waitForEvent('dialog').then(async (dialog) => {
      expect(dialog.message()).toContain('Remove 1 selected book(s) from your library?');
      await dialog.accept();
    });
    await Promise.all([
      confirmation,
      page.getByRole('button', { name: 'Remove from my library', exact: true }).click(),
    ]);
    await expect(page.locator('[aria-live="assertive"]')).toHaveText(
      '0 book(s) removed from your library; 1 failed. '
      + 'The last book cannot be removed unless you can browse the global library. '
      + `Failed: Book ${book.id}. The failed books remain selected; choose the action again to retry.`,
    );
    expect((await currentLibraryBooks(page)).map((item) => item.id)).toEqual([book.id]);
    await expect(page.getByRole('button', { name: `Deselect ${book.title}`, exact: true })).toBeVisible();
  });

  test('removing the final self-service book shows the empty-library recovery UX', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }) => {
    await setManagedMode(adminPage, secondaryUser.id, 'personal_library');
    const page = secondaryUser.page;
    const books = await currentLibraryBooks(page);
    expect(books.length, 'seed library needs at least one book').toBeGreaterThan(0);
    const book = books[0];
    await keepOnlyMembership(page, book.id);
    await page.goto(`/app/book/${book.id}`);

    // The detail action is identical for mouse, keyboard and touch. The card's
    // compact remove affordance is intentionally hover/disclosure-driven on
    // some viewports and is not the right cross-modality oracle for this cell.
    const removeButton = page.getByRole('button', { name: 'Remove from my library' });
    const confirmation = page.waitForEvent('dialog').then(async (dialog) => {
      expect(dialog.message()).toContain(`Remove "${book.title}" from your library?`);
      expect(dialog.message()).toContain('the book stays in the global library');
      expect(dialog.message()).toContain('You can add it back any time from the global library.');
      await dialog.accept();
    });
    await Promise.all([confirmation, removeButton.click()]);
    await expect(page.getByText('Removed from your library', { exact: true })).toBeAttached();
    await page.getByRole('link', { name: '← Library', exact: true }).click();

    await expect(page.getByRole('heading', { name: 'Your library is empty' })).toBeVisible();
    await expect(page.getByText(
      'Nothing is missing — the global library is still on the server. '
      + 'What you see here is your selection in My Library. Add books from the global library; '
      + 'they appear here and on your e-reader.',
      { exact: true },
    )).toBeVisible();
    expect(await currentLibraryBooks(page)).toEqual([]);

    await page.getByRole('link', { name: 'Browse the global library', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Global Library' })).toBeVisible();
    const addButton = page.getByRole('button', {
      name: `Add ${book.title} to my library`,
      exact: true,
    });
    await expect(addButton).toBeVisible();
    await addButton.click();
    await expect(page.getByText('Added to your library', { exact: true })).toBeAttached();
    await page.goto('/app');
    await expect(page.getByTestId('catalog-grid').getByRole('link', {
      name: `Open details for ${book.title}`,
    })).toBeVisible();
  });

  test('classic theme exposes the same library, global, add, and remove surfaces', async ({
    page: adminPage,
    secondaryUser,
  }: { page: Page; secondaryUser: SecondaryUserSession }) => {
    test.skip(test.info().project.name === 'mobile', 'classic parity is exercised once on desktop');
    await setManagedMode(adminPage, secondaryUser.id, 'personal_library');
    const page = secondaryUser.page;
    const books = await firstGlobalBooks(page);
    test.skip(books.length < 2, 'seed library needs at least two books');
    const [missingBook, ownedBook] = books;
    await removeMembership(page, missingBook.id);

    try {
      // This matrix cell deliberately enters the real session-scoped Classic
      // fallback before exercising its direct routes.
      await page.goto('/?cwng_feedback=newui', { waitUntil: 'domcontentloaded' });
      await expect.poll(() => new URL(page.url()).pathname).toBe('/');
      await expect(page.locator('#my-library-intro')).toContainText('New: My Library');
      await expect(page.getByRole('link', { name: 'Global Library' })).toHaveAttribute('href', '/global-library');

      await page.goto(`/book/${ownedBook.id}`);
      const removeButton = page.locator('#remove-from-my-library-btn');
      await expect(removeButton).toHaveAttribute('aria-label', 'Remove from my library');
      await removeButton.click();
      const removeDialog = page.locator('#removeFromMyLibraryModal');
      await expect(removeDialog).toContainText(`Remove "${ownedBook.title}" from your library?`);
      await expect(removeDialog).toContainText('Nothing is deleted: the book stays in the global library');
      await removeDialog.getByRole('button', { name: 'Cancel' }).click();

      await page.goto('/global-library/recent-missing');
      await expect(page.getByRole('heading', { name: /Global Library/ })).toBeVisible();
      const addButton = page.getByRole('button', { name: `Add ${missingBook.title} to my library` });
      await expect(addButton).toBeVisible();
      await addButton.click();
      await expect(addButton).toHaveCount(0);
    } finally {
      await addMembership(page, missingBook.id).catch(() => undefined);
      await page.goto('/app/?cwng_switch=spa').catch(() => undefined);
    }
  });
});
