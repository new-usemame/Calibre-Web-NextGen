/* ============================================================================
   "What's New" — the in-app feature log the SPA renders on /whats-new.

   This is the SINGLE SOURCE for the reader-facing feature log. It is the
   humanized, deep-linked projection of CHANGELOG.md, grouped by published
   release (newest first). Keeping it as data — not hand-written JSX per
   release — is what makes it AI-iterable: the `whats-new-populate` skill
   (~/.claude/skills/whats-new-populate/) prepends a new WHATS_NEW[0] block on
   every public release, one WhatsNewItem per user-facing change.

   COPY STANDARD (see notes/WHATS-NEW-FEATURE-DESIGN.md — obey it on every add):
     - Lead with the reader benefit, not the mechanism.
     - Name the problem it solves in plain words, then how to use it.
     - 1–3 sentences. If it needs more, it's two items.
     - No AI tells, no marketing fluff, no exclamation storms. Smart-reader
       register: warm, precise, concise. English-authored (chrome is localized,
       entries are not — the page says so).
     - Credit no contributors here (that's release notes / outreach).

   DEEP LINKS: `link.to` is an in-app SPA path (resolved under the /app router
   base by wouter). It MUST be a route that exists in App.tsx. Omit `link`
   entirely when no navigable surface fits (a per-book screen with no universal
   entry point, a logged-out page, or a pure backend fix) — never fake one.
   ============================================================================ */

export type WhatsNewCategory =
  | 'Reading'
  | 'Library'
  | 'Sync'
  | 'Account'
  | 'Admin'
  | 'Under the hood';

export interface WhatsNewItem {
  /** Human, benefit-led headline ("Find something to read tonight"). */
  title: string;
  /** 1–3 sentences: what it is + why it exists (the problem) + how to use it. */
  body: string;
  category: WhatsNewCategory;
  /** In-app deep link. `to` is an App.tsx route path; omit if none fits. */
  link?: { to: string; label: string };
}

export interface WhatsNewRelease {
  /** Release tag, e.g. "v4.1.5". */
  version: string;
  /** ISO date of the release (YYYY-MM-DD); rendered humanized. */
  date: string;
  /** Optional one-line framing for the release as a whole (rare — a launch or a
   *  corrective release). Kept short; most releases don't need it. */
  summary?: string;
  /** Newest-first is enforced by array order; items in reader-priority order. */
  items: WhatsNewItem[];
}

/** Newest release first. The `whats-new-populate` skill prepends here. */
export const WHATS_NEW: WhatsNewRelease[] = [
  {
    version: 'v4.1.11',
    date: '2026-07-12',
    items: [
      {
        title: 'Light mode — and five more themes — for the new interface',
        body: 'The most-requested feature is here. The new interface now has a real theme picker in your account settings: choose System (which follows your device’s light/dark setting), Light, Dark, Sepia, High contrast, or Midnight for OLED screens. Your choice is saved to your account and holds across every screen, with readable contrast throughout.',
        category: 'Account',
        link: { to: '/account', label: 'Choose your theme' },
      },
      {
        title: 'Read a book in one click from the grid',
        body: 'Book cards now have a Read now action that opens the book straight in the reader instead of going through the details page first. It works for EPUB, PDF, comics, text and audio, and is fully keyboard- and screen-reader-accessible.',
        category: 'Reading',
        link: { to: '/', label: 'Open your library' },
      },
      {
        title: 'Admins can email a password reset from the new interface',
        body: 'An admin can now reset another user’s password from their user card without switching to the classic view. The new password is generated on the server and emailed to the user — it never appears in the browser, and you can’t reset your own, the Guest, or a user without an email address.',
        category: 'Admin',
        link: { to: '/admin', label: 'Open Admin' },
      },
      {
        title: 'Smart shelves that keep themselves current',
        body: 'Smart shelf date rules can now use moving windows like “in the past 28 days” for Publication Date and Date Added, so a shelf for the last month or six months stays up to date instead of freezing a fixed date into the rule.',
        category: 'Library',
        link: { to: '/magic', label: 'Smart shelves' },
      },
      {
        title: 'Calibre custom columns on the new book page',
        body: 'Custom columns you set up in Calibre — such as a Pages count — now appear on the redesigned book page with the right type and formatting. Zero and “No” values show correctly instead of disappearing.',
        category: 'Library',
      },
      {
        title: 'Accented titles and names sort where you expect',
        body: 'Accented letters like È and É now sort with their base letter across the library, search, the new interface and OPDS, instead of being stranded at the end or in a separate bucket. Spanish Ñ stays a distinct letter after N, and German ß orders as “ss”.',
        category: 'Library',
      },
      {
        title: 'A slow export no longer freezes the whole server',
        body: 'One pathological PDF export used to make every other page hang for up to 90 seconds. Metadata exports now run in a bounded background pool, so health checks and unrelated requests stay responsive while a slow export finishes.',
        category: 'Under the hood',
      },
      {
        title: 'An expired session sends you back to the login page',
        body: 'Behind a reverse proxy (Authelia, OIDC, oauth2-proxy), an expired session used to leave the new interface showing a “Failed to fetch” error instead of the login page. Now it returns you to log in — and Sign out makes the top-level logout request that logs you out of your proxy too.',
        category: 'Account',
      },
      {
        title: 'See your Hardcover token’s status at a glance',
        body: 'Basic Configuration now shows whether your Hardcover token is present, accepted, or rejected/expired — and its expiry date when the token provides one — instead of leaving you to dig through the logs.',
        category: 'Admin',
        link: { to: '/admin', label: 'Open Admin' },
      },
      {
        title: 'Edit the send-to-e-reader message from your Account page',
        body: 'Admins can now edit the server-wide send-to-e-reader email body from the redesigned Account page, which previously exposed only the recipient and subject. Non-admin accounts can’t see or change the shared template.',
        category: 'Account',
        link: { to: '/account', label: 'Open Account' },
      },
      {
        title: 'A tidier sidebar and one search box',
        body: 'Upload is now a clearly labelled button in the Library toolbar instead of another sidebar row; the Customize control moved to a quiet footer spot rather than dominating the top of the rail; and the Library no longer shows two separate search boxes that did the same thing.',
        category: 'Library',
      },
      {
        title: 'Covers show in full again',
        body: 'Covers whose artwork isn’t a standard 2:3 shape are no longer cropped to fill the card — they fit the whole image inside it, and the cover picker shows the complete artwork when you’re choosing between editions.',
        category: 'Library',
      },
      {
        title: 'Uploading books is more reliable and accessible',
        body: 'The new interface’s upload now uses the browser’s native file control, so drag-and-drop and tap-to-choose share one reliable path, repeat uploads are blocked while one is pending, and queued or rejected results are announced to assistive technology.',
        category: 'Library',
      },
      {
        title: 'Shuffling Discover no longer makes the page jump',
        body: 'The Discover picks now stay in place while a fresh random set loads and update in one step, so the library no longer jumps up and down when you shuffle. Screen readers are told when the shuffle starts and finishes.',
        category: 'Library',
      },
      {
        title: 'Edits show up in search right away',
        body: 'After you change a book’s title or author in the new interface, searching now reflects the change immediately instead of still turning up the old value.',
        category: 'Library',
      },
      {
        title: 'Reach your whole library, however you browse',
        body: 'If the grid’s automatic loading didn’t kick in you could be stranded on the first pages. There’s now a keyboard-accessible Load more button that stays available whenever more books remain.',
        category: 'Library',
        link: { to: '/', label: 'Open your library' },
      },
    ],
  },
  {
    version: 'v4.1.10',
    date: '2026-07-11',
    items: [
      {
        title: 'Your reading position follows you between both readers',
        body: 'Turn pages in the classic reader, then open the same book in the new interface (or the other way around), and it now resumes exactly where you left off. Both readers save your position to your account continuously — not just when you tap the bookmark button — and your last spot still restores when you are offline.',
        category: 'Reading',
      },
      {
        title: 'Delete a book from the new interface',
        body: 'The new book page now has a Delete button, so you no longer have to switch to the classic view to remove a book. It asks for confirmation, removes the book and its files, and returns you to your library. The button only appears if your account has permission to delete books.',
        category: 'Library',
        link: { to: '/', label: 'Open your library' },
      },
      {
        title: 'Set a rating with a click, half stars included',
        body: 'The new editor now has an inline five-star rating control. Click either half of a star for half-star precision, nudge it with the arrow keys, or clear the rating entirely — no dropdown to open.',
        category: 'Library',
      },
      {
        title: 'A compact list view for authors, series and tags',
        body: 'Browse pages for authors, series, tags, publishers and more now have a grid/list toggle. Switch to full-width rows that show each name and its book count; your choice is remembered in this browser, including on mobile.',
        category: 'Library',
        link: { to: '/authors', label: 'Browse authors' },
      },
      {
        title: 'Turn metadata sources on or off in the new editor',
        body: 'When you fetch metadata in the new editor you can now switch individual sources — like Hardcover — on and off, just as you can in the classic view. The choice is saved to your account, so it is the same in either interface.',
        category: 'Library',
      },
      {
        title: 'The Publication date field is back in the new editor',
        body: 'The redesigned editor has a Published date input again, so you can set or clear a book’s publication date without switching to the classic UI. It is prefilled from the book’s current date and can be cleared to reset it.',
        category: 'Library',
      },
      {
        title: 'KOReader keeps your furthest position across devices',
        body: 'Syncing from a device that was behind no longer overwrites a further position saved from another device, and clock differences between devices no longer make a real forward sync look like a rewind. Marking a finished book unread also clears its KOReader position so you can start it over.',
        category: 'Sync',
        link: { to: '/account', label: 'Manage sync & app passwords' },
      },
      {
        title: 'A “Currently Reading” feed for OPDS apps',
        body: 'The OPDS catalog now offers a feed of exactly the books you have in progress, instead of lumping them into the broad “not finished” group. It respects the same visibility and shelf restrictions as the rest of your OPDS catalog.',
        category: 'Sync',
      },
      {
        title: 'Send-to-e-reader shows your saved address again',
        body: 'The new interface’s Send dialog now prefills the e-reader address saved in your account, instead of a blank field that made it look lost. Type a different address to override it for one send, or clear it to fall back to your saved one.',
        category: 'Library',
        link: { to: '/account', label: 'Check your e-reader address' },
      },
      {
        title: 'Hardcover metadata fetching is more reliable',
        body: 'Several Hardcover issues are fixed: the “Run Hardcover Auto-Fetch” tool no longer stops with an internal error partway through, automatic fetching during ingest works again when a token is set in the environment, and two people on the same server can now share one Hardcover token.',
        category: 'Under the hood',
      },
      {
        title: 'The new login page shows your configured SSO button label',
        body: 'If you sign in with OpenID Connect and set a custom button label, the new interface’s login button now shows that label instead of the internal provider name — matching what the classic login page has always shown.',
        category: 'Account',
      },
      {
        title: 'Clearer server logs around covers and metadata',
        body: 'Saving a cover for a PDF-only book no longer ends with a misleading failure message, editing one book no longer floods the log with repeated “file not found” warnings, and author-sort warnings now name the affected book and point to where you can fix it. These are log-quality fixes with no change to your library.',
        category: 'Under the hood',
      },
      {
        title: 'Smaller fixes and polish',
        body: 'The classic book page’s read checkbox now matches the book’s actual state, the edit pencil on a book card can be opened in a new tab, the Help menu’s “Report Issue” link opens the bug-report form, and four remaining Russian interface strings are now translated.',
        category: 'Under the hood',
      },
    ],
  },
  {
    version: 'v4.1.9',
    date: '2026-07-11',
    items: [
      {
        title: 'Refresh your library from the new interface',
        body: 'The new interface now has a Refresh button in the library toolbar, so after you add books to your ingest folder you can trigger a scan without switching back to the classic view. It reports progress and reloads the grid when new books appear.',
        category: 'Library',
        link: { to: '/', label: 'Open your library' },
      },
      {
        title: 'Remove and recolor highlights while reading',
        body: 'In the in-browser reader you can now tap an existing highlight to change its color or remove it — previously highlights could only be created, never cleared. The change is saved, so a removed highlight stays gone after you reload.',
        category: 'Reading',
      },
      {
        title: 'The metadata editor suggests values as you type',
        body: 'Editing a book in the new interface, the Tags, Authors, Series, Publishers and Languages fields now suggest values already in your library, so a small spelling difference no longer creates a near-duplicate tag or series. Pick from the list or keep typing to add a new one.',
        category: 'Library',
      },
      {
        title: 'The new interface stays chosen',
        body: 'Once you switch to the new interface it now sticks: opening the site again — or a bookmarked link — keeps you in the new interface until you choose to go back to the classic view. Use "Back to the classic view" from the account menu to switch back.',
        category: 'Account',
      },
      {
        title: 'Half-star ratings display correctly',
        body: 'A book rated a half-star (like 3.5) now shows a cleanly half-filled star on its page, instead of a shrunken star floating inside the outline.',
        category: 'Library',
      },
      {
        title: 'Admin configuration pages open in place',
        body: 'On the Admin page, the "More server configuration" buttons now open in the same tab instead of spawning a new one each time — no more piled-up windows when you visit the deeper settings screens.',
        category: 'Admin',
        link: { to: '/admin', label: 'Open Admin' },
      },
      {
        title: 'OPDS feeds are easier to tell apart',
        body: 'In an OPDS reader, an alphabetical sub-list or a specific author, category, series, rating, format or language now names itself in the feed title — "Authors (V)", "Categories: Fantasy", "Ratings: 4.5 Stars" — so your feed list is no longer a wall of identical names.',
        category: 'Sync',
      },
      {
        title: 'Set the Hardcover token by environment variable',
        body: 'A Hardcover API token set through the HARDCOVER_TOKEN environment variable now works everywhere in the app, and the Admin page notes when an environment token is active. You can also keep the token in a file with the new HARDCOVER_TOKEN_FILE variable.',
        category: 'Admin',
        link: { to: '/admin', label: 'Open Admin' },
      },
      {
        title: 'Cleaner startup logs',
        body: 'The container no longer prints an alarming "desktop integration failed" warning with a traceback on first start — that step is skipped cleanly in a headless server, so a healthy startup now looks healthy.',
        category: 'Under the hood',
      },
    ],
  },
  {
    version: 'v4.1.8',
    date: '2026-07-10',
    items: [
      {
        title: 'See when you started a book and when it last synced',
        body: "If you read with a Kobo or KOReader, a book's page now shows the date your reading progress first synced and when it last did — so you can tell how long a book has been in progress and whether its saved position is current.",
        category: 'Sync',
        link: { to: '/', label: 'Open your library' },
      },
      {
        title: 'Show tags as a column in the table view',
        body: 'The table view can now show each book\'s tags in their own column, next to Series — useful when you\'re skimming or editing metadata and want genres and subjects at a glance. Toggle it with the "Columns" button.',
        category: 'Library',
        link: { to: '/table', label: 'Open the table view' },
      },
      {
        title: 'Book lists load as you scroll',
        body: 'The Library grid, table view, shelves, smart shelves, and search results now load the next page automatically as you near the bottom — one continuous scroll instead of a "Load more" button.',
        category: 'Library',
        link: { to: '/', label: 'Open your library' },
      },
      {
        title: 'The duplicates notice respects your archive',
        body: 'If you archived one book of a duplicate pair, the sidebar badge and pop-up notice kept counting it even though the duplicates page showed nothing. The count now honors archived and hidden books, so the badge and the page agree.',
        category: 'Library',
        link: { to: '/duplicates', label: 'Review duplicates' },
      },
      {
        title: 'Reordering the sidebar feels smooth and physical',
        body: 'In the Customize panel, dragging a section now lifts the row and glides the others aside instead of snapping and jittering — the same on mouse, touch, and pen. Keyboard reordering animates through the same motion, and it all falls back to instant when your system prefers reduced motion.',
        category: 'Library',
      },
      {
        title: 'OPDS feeds show their own names',
        body: 'In an OPDS reader app, every feed used to carry the same title — your library\'s name — so the feed list was a wall of identical entries. Each feed now shows its own name (a shelf shows its shelf name, a search shows the query).',
        category: 'Sync',
      },
      {
        title: 'Better automatic Hardcover matches',
        body: 'Automatic Hardcover metadata matching now scores the full set of search results instead of only the first ten, so the right edition is found more often without a manual review.',
        category: 'Library',
      },
      {
        title: 'Author names with commas display correctly',
        body: 'An author like "William H. Keith, Jr." showed a raw "|" where the comma should be on book cards and the book page in the new interface. The stored form is now converted for display everywhere.',
        category: 'Library',
      },
      {
        title: 'Downloads work for apps that skip the browser identifier',
        body: 'Some OPDS readers, download managers, and scripts omit the User-Agent header; those downloads hit a server error instead of the book. They now download normally.',
        category: 'Under the hood',
      },
      {
        title: 'The new interface translates more completely',
        body: 'Many menu items and whole screens — admin settings, the cover picker, advanced search, the book editor — stayed English even in fully translated languages, because their strings were never collected for translation. They are now, and the Russian translation received a round of corrections.',
        category: 'Under the hood',
      },
    ],
  },
  {
    version: 'v4.1.7',
    date: '2026-07-08',
    items: [
      {
        title: 'Book pages show ratings and more by the author',
        body: 'A book\'s page in the new interface now shows its star rating, and a "More by this author" row underneath, so a book page is a place to keep browsing instead of a dead end. Books without cover art or a description no longer leave the page looking half-empty.',
        category: 'Library',
        link: { to: '/', label: 'Open your library' },
      },
      {
        title: 'Removing a duplicate clears the old copy from your Kobo',
        body: 'When the duplicate-scanner replaces an older copy of a book with a newer one, the server now tells a synced Kobo that the old copy is gone — a signal it never sent before, so removed duplicates could linger on the device. (Some Kobos may still keep a removed side-loaded book until it\'s archived on the device; we\'re improving that next.)',
        category: 'Sync',
        link: { to: '/account', label: 'Manage sync & app passwords' },
      },
      {
        title: 'Uploading a format no longer duplicates the book',
        body: 'Adding a new format to a book — especially one with a very long filename — could import it as a separate, duplicate book instead of attaching to the original. New formats now land on the right book.',
        category: 'Library',
      },
      {
        title: 'Converting from plugin formats like KFX works again',
        body: 'Converting a book from a format that needs a Calibre input plugin — KFX and others — failed with "No plugin to handle input format" even with the plugin installed, because the converter wasn\'t reading your plugins folder. It now does.',
        category: 'Library',
      },
      {
        title: 'Changing a cover updates the file, not just the library',
        body: 'Picking a new cover updated it in your library but left the old image embedded in the book file, so downloads and the "currently embedded" preview stayed on the old art. The new cover is now written into the book itself.',
        category: 'Library',
      },
      {
        title: 'Marking a book unread clears "Currently reading" too',
        body: 'Following last release\'s progress reset: marking a book unread now also clears the "Currently reading" marker, which could otherwise stick even after the percentage was gone. Unread reads as untouched everywhere.',
        category: 'Reading',
      },
      {
        title: 'Find the Admin page from the account menu',
        body: 'In the new interface the Admin entry lived only in the sidebar rail, so admins who looked in the account (avatar) menu couldn\'t find it and some switched back to the classic view. Admin accounts now get an Admin link there too.',
        category: 'Admin',
        link: { to: '/admin', label: 'Open Admin' },
      },
      {
        title: 'Downloading on an iPhone no longer strands you',
        body: 'In the new interface, tapping a format to download it could navigate Safari away from the app to a page it couldn\'t show, leaving you stuck until you force-restarted. Downloads now open in a separate tab, so the app stays put and you land right back where you were.',
        category: 'Library',
      },
      {
        title: 'Covers apply during import on split libraries',
        body: 'On setups that keep book files separate from metadata.db (the "split library" option), auto-fetching metadata during import silently failed to save the downloaded cover. Covers now apply correctly during import.',
        category: 'Under the hood',
      },
      {
        title: 'Smaller fixes',
        body: 'The bulk-edit toolbar shows the real book count again instead of a raw "%(n)s" placeholder. And a few Russian interface labels are corrected — the system sans-serif font option no longer reads "Статистика системы".',
        category: 'Under the hood',
      },
    ],
  },
  {
    version: 'v4.1.6',
    date: '2026-07-07',
    items: [
      {
        title: 'Reading position keeps up across your devices',
        body: 'If you read the same book on two devices, the one that was behind could refuse to jump forward — a manual sync just said "already synced". This happened when each device held a slightly different copy of the book (after a re-upload, a metadata edit, or a side-load). The server now recognises them as the same book, so the furthest position wins everywhere.',
        category: 'Sync',
        link: { to: '/account', label: 'Manage sync & app passwords' },
      },
      {
        title: 'Choose the interface font',
        body: 'Account settings now has body- and display-font pickers — a system sans-serif, a bookish serif, or monospace in place of the defaults. Each option previews in its own font, and your choice is saved to your account, so it follows you to every device and browser.',
        category: 'Account',
        link: { to: '/account', label: 'Open account settings' },
      },
      {
        title: 'Arrange the sidebar the way you use it',
        body: 'A Customize control at the top of the left rail turns the sidebar into an editable list: drag sections into the order you want — move Shelves to the top so you never scroll for it — and hide the ones you don\'t use. It works with the mouse, on touch, and with the keyboard, and your layout is saved to your account.',
        category: 'Library',
        link: { to: '/', label: 'Open your library' },
      },
      {
        title: 'Apply the exact Hardcover edition when fetching metadata',
        body: 'A Hardcover search result now has an Editions view, so you can drill into a book\'s individual editions — paperback, e-book, a translation — and apply the one you actually own. That puts the right edition ISBN and id on your book, which is what Hardcover reading-progress sync needs to match the right copy.',
        category: 'Library',
      },
      {
        title: 'A What\'s New page',
        body: 'This page. It lives in the Help ("?") menu and keeps a plain-English log of what changed in each release, newest first, each with a link straight to the thing it describes. A small dot on the Help menu points it out once after an update.',
        category: 'Under the hood',
      },
      {
        title: 'A much fuller Russian translation',
        body: 'Russian coverage roughly doubled this release, so far more of the interface reads in Russian instead of falling back to English. Pick it under interface language in your account settings.',
        category: 'Account',
        link: { to: '/account', label: 'Open account settings' },
      },
      {
        title: 'Upgrades no longer crash-loop after Hardcover annotation sync',
        body: 'If your library had ever synced highlights to Hardcover, an upgrade could get stuck restarting over and over and never finish booting. A one-time database step was double-counting your own sync records; it now checks the right thing and completes. No data is lost and no manual steps are needed.',
        category: 'Under the hood',
      },
      {
        title: 'Your profile picture shows in the new interface',
        body: 'A picture set in the classic account settings was ignored by the new interface, which showed a generic silhouette in the top bar and on your account page. Both now use your picture, falling back to the silhouette only when you haven\'t set one.',
        category: 'Account',
        link: { to: '/account', label: 'Open account settings' },
      },
      {
        title: 'The "Currently reading" marker shows on a book\'s page',
        body: 'A book you\'re partway through on KOReader or Kobo showed the "Currently reading" marker on the classic book page but nothing in the new interface. The new-UI book page now shows it too — with the synced percentage when it\'s known — while unread and finished books stay unmarked.',
        category: 'Reading',
        link: { to: '/', label: 'Open your library' },
      },
      {
        title: 'Marking a book unread clears its progress',
        body: 'Opening a book just to look could leave it stuck at something like "0.6% read" with no way to reset it — the switch flipped the status but left the percentage behind. Marking a book unread now also resets its progress and where the in-browser reader would resume, so it reads as untouched everywhere.',
        category: 'Reading',
      },
      {
        title: 'The new interface hides the smart shelves you turned off',
        body: 'Unticking entries under Magic Shelves Visibility only worked in the classic view — the new-UI sidebar still listed every smart shelf. It now honours the setting, so hidden smart shelves stay hidden in both interfaces.',
        category: 'Library',
        link: { to: '/account', label: 'Open account settings' },
      },
      {
        title: 'Fetch Metadata stops reusing one cover across a series',
        body: 'Searching for one volume of a series could return results where every volume carried an identical cover — and applying metadata then saved that wrong cover onto the book. The cover step now refuses artwork whose volume number disagrees with the book, and keeps the ISBN so the exact-edition cover can be found.',
        category: 'Library',
      },
      {
        title: 'Smaller fixes',
        body: 'Your shelves are listed directly under the SHELVES heading in the new-UI sidebar again instead of at the very bottom. The "Contribute here!" link on the partial-translation banner points at a page that exists again. And the browser-tab icon is refreshed to match the app.',
        category: 'Under the hood',
      },
    ],
  },
  {
    version: 'v4.1.5',
    date: '2026-07-03',
    items: [
      {
        title: 'Your "Currently Reading" shelf shows the right books',
        body: 'If your library links read status to a Calibre column, the Currently Reading shelf used to list everything you\'d ever marked read instead of the book you\'re partway through. In-progress state now comes straight from your KOReader and Kobo sync, finished books drop off, and "Yet to Read" counts books you\'ve never opened.',
        category: 'Reading',
        link: { to: '/shelves', label: 'Open your shelves' },
      },
      {
        title: 'KOReader sync works when your reader matches by filename',
        body: 'Some readers — and apps like Crossink — identify a book by its filename rather than its contents, which used to mean "no book found" and no synced progress. The server now recognises both, and your existing library is backfilled automatically, so an older or side-loaded copy can rejoin sync by switching KOReader\'s document-matching method to "filename".',
        category: 'Sync',
        link: { to: '/account', label: 'Manage sync & app passwords' },
      },
      {
        title: 'One stubborn PDF can no longer hang the whole server',
        body: 'Downloading certain PDFs could freeze inside the metadata step and leave every request stuck until it timed out. That step is now time-bounded and falls back to serving your original file, so you get your book instead of a wedged server.',
        category: 'Under the hood',
      },
      {
        title: 'The library\'s view-settings menu stays on-screen on phones',
        body: 'On narrow screens the toolbar wraps, and the gear menu could open off the left edge of the display. It now drops neatly below the toolbar and stays fully visible at any width.',
        category: 'Library',
        link: { to: '/', label: 'Open your library' },
      },
    ],
  },
  {
    version: 'v4.1.4',
    date: '2026-07-02',
    items: [
      {
        title: 'Quick edits are back: a hover pencil and inline tags',
        body: 'Hovering a book in your library or search results now shows a small pencil that drops you straight onto its edit page — no need to open the book first. And on a book\'s page you can add or remove individual tags right there, each with an ×, instead of hand-editing one long comma-separated list. Both appear only if you can edit.',
        category: 'Library',
        link: { to: '/', label: 'Open your library' },
      },
      {
        title: 'Read a series in order, with each book\'s number shown',
        body: 'Opening a series listed its books newest-first with no way to order them 1, 2, 3. A series now opens in reading order by default, the sort menu gains "Series order" (and its reverse), and every cover shows its position.',
        category: 'Reading',
        link: { to: '/series', label: 'Browse series' },
      },
      {
        title: 'Switching shelves no longer mixes both shelves\' books',
        body: 'Going straight from one shelf to another kept the first shelf\'s books on screen underneath the next one\'s — and removing a leftover book removed it from the shelf you were now on. Each shelf now shows only its own books, and the page counter resets when you switch.',
        category: 'Library',
        link: { to: '/shelves', label: 'Open your shelves' },
      },
      {
        title: 'Table view covers look right again',
        body: 'In Table view, cover thumbnails rendered as narrow slivers with the sides cropped off. They now show at a proper book-cover shape, and a title made of one long unbreakable string wraps in its cell instead of forcing the whole table to scroll sideways.',
        category: 'Library',
        link: { to: '/table', label: 'Open Table view' },
      },
      {
        title: 'App passwords work with the KOReader plugin',
        body: 'KOReader sync only accepted your main password, so OAuth- or LDAP-only accounts (which have none) got "Invalid password". You can now create a per-user app password for KOReader progress and annotation sync, the same as OPDS already allows.',
        category: 'Sync',
        link: { to: '/account', label: 'Create an app password' },
      },
      {
        title: 'Admin\'s "More server configuration" links behave properly',
        body: 'Those deep config cards on the Admin page now open in a new browser tab, so the new interface stays exactly where it was, and they point to the right place when you run behind a reverse-proxy subpath instead of breaking out to a 404.',
        category: 'Admin',
        link: { to: '/admin', label: 'Open Admin' },
      },
      {
        title: 'The sidebar respects the sections you\'ve turned off',
        body: 'If you or an admin hid sections such as Hot, Discover, Series, Authors or Publishers, the new-UI sidebar now leaves them out instead of always showing everything. Nothing changes if you never hid anything.',
        category: 'Library',
      },
      {
        title: 'Language and mobile polish',
        body: 'The read toggle mistakenly offered "mark as unread" on unread books in French and 16 other languages — all fixed. Logging out on mobile in the classic view works again, and a custom site title now appears in the new UI\'s top bar, login screen and browser tab.',
        category: 'Under the hood',
      },
    ],
  },
  {
    version: 'v4.1.3',
    date: '2026-07-01',
    summary: 'A corrective release for anyone who saw a stuck popup over the classic view.',
    items: [
      {
        title: 'The classic view\'s feedback popup can no longer get stuck',
        body: 'A "what made you switch back?" prompt was appearing on every classic page with no way to close it, and on phones it didn\'t even fit the screen. It now only shows right after you switch back from the new interface, every button dismisses it, and it fits and scrolls on small screens.',
        category: 'Under the hood',
      },
    ],
  },
  {
    version: 'v4.1.1',
    date: '2026-07-01',
    items: [
      {
        title: 'Edit identifiers, and choose which fetched values to apply',
        body: 'Editing a book in the new interface now has an Identifiers table — add, change or remove ISBN, ASIN, Google, DOI and the rest — and when you fetch metadata from the web, each result has a "Choose fields" checklist so you apply just the title, cover or identifiers you want instead of overwriting everything.',
        category: 'Library',
        link: { to: '/', label: 'Open your library' },
      },
      {
        title: 'Your KOReader/Kobo progress and identifier links are back',
        body: 'A book\'s page again shows your synced "KOReader progress: X%", and identifiers like Goodreads, StoryGraph, Hardcover and ISBN are clickable links out to the book on those sites rather than plain text.',
        category: 'Sync',
      },
      {
        title: 'Read/unread respects a custom Calibre column',
        body: 'If you link read status to a custom Calibre column, the new interface showed every book as unread and the read/unread filters returned everything. It now reads that column, so finished books get their checkmark and the filters work again.',
        category: 'Library',
      },
      {
        title: 'Works behind a reverse proxy, keeps your place, and shows its icon',
        body: 'Serving NextGen under a subpath (like host/cwa/) used to show a blank white page — every script, style and cover is now requested with the right prefix. Scrolling down, opening a book and pressing Back restores your position and loaded pages, the mobile menu drawer is solid and scrolls on its own, and the browser tab shows the Calibre-Web icon.',
        category: 'Under the hood',
      },
    ],
  },
  {
    version: 'v4.1.0',
    date: '2026-06-30',
    summary: 'The redesigned interface opens up to everyone, plus a set of new tools.',
    items: [
      {
        title: 'The new interface is offered to everyone — opt in when you\'re ready',
        body: 'After updating, a dismissible bar invites you to try the redesigned interface; your classic view stays the default until you choose to switch, and you can switch back any time from the top bar. Dismiss the bar and it stays gone until the next update.',
        category: 'Library',
        link: { to: '/', label: 'Explore your library' },
      },
      {
        title: 'A "Discover" shelf of random picks on your home page',
        body: 'The redesigned library opens with a set-apart Discover box — a row of random books from your collection to help you stumble onto something to read. Hit shuffle for a fresh set, or hide it and bring it back any time from View settings.',
        category: 'Reading',
        link: { to: '/discover', label: 'Browse Discover' },
      },
      {
        title: 'A redesigned "Change cover" screen',
        body: 'Picking a new cover now opens a polished page: your current cover with a one-tap lock so a metadata refresh can\'t overwrite it, a grid of candidates from every source (plus the one embedded in the book), and tabs to paste a URL or upload your own. Kobo users can flip on E-reader preview to see how each looks padded for the device. Reach it from a book\'s cover or its edit page.',
        category: 'Library',
      },
      {
        title: 'Email your users straight from the admin area',
        body: 'A new "Email Your Users" page lets you write a message and send it to everyone — or just the people you pick — handy for announcing new books or server updates. It uses the mail server you already set up, formats with HTML and a plain-text fallback, and has a "Send test to me" button so you can preview before sending.',
        category: 'Admin',
        link: { to: '/admin', label: 'Open Admin' },
      },
      {
        title: 'A polished sign-in and magic-link experience',
        body: 'The redesigned login page brings back "Remember me" and a show-password toggle, and choosing "Log in with a magic link" now opens a proper screen with the QR code, one-tap link copy, a live "waiting…" indicator and an expiry countdown.',
        category: 'Account',
      },
      {
        title: 'The Admin version number links to its release notes',
        body: 'The Calibre-Web NextGen version in the Admin page\'s Version Information table is now a link to that build\'s release notes, so you can see exactly what changed in what you\'re running.',
        category: 'Admin',
        link: { to: '/admin', label: 'Open Admin' },
      },
      {
        title: 'Uploads, sorting and proxy fixes',
        body: 'A book with a very long filename no longer fails to import, editing the title-sort regular expression now re-sorts your whole library immediately, the "Discover (Random Books)" row actually appears when enabled, and bulk actions and drag-to-merge work behind a reverse-proxy subpath.',
        category: 'Under the hood',
        link: { to: '/upload', label: 'Go to Upload' },
      },
    ],
  },
];

/** The newest release in the log — used to key the "unread" dot to the build. */
export const LATEST_WHATS_NEW_VERSION: string = WHATS_NEW[0]?.version ?? '';
