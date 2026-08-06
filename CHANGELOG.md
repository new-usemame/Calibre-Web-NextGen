# Changelog

All notable user-facing changes to Calibre-Web NextGen. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

**Docker tags:** `:latest` = newest stable release · `:dev` = every merge to main
(canary channel — what the maintainers run at home) · `:vX.Y.Z` = immutable pins
for rollback.

**Compatibility promise:** patch releases (`vX.Y.Z` → `vX.Y.Z+1`) are safe to
auto-update — no breaking config, database, or API changes without a `BREAKING`
callout at the top of the release notes.

Internal refactors, CI changes, and test-only work don't appear here — this file
is for things you can see or feel when running the app.

## [Unreleased]

### Changed

- **A sign-in page whose only button is your one provider now just takes you
  there.** If your server runs in OAuth-only mode with exactly one provider
  switched on, opening the login page showed you a page whose sole purpose was
  to click through to that provider. NextGen now starts it for you. If the
  provider is unreachable, or you want the plain page back for any reason, add
  `?local=1` to the login URL. A cancelled or failed sign-in returns you to the
  login page instead of bouncing you straight back out to the provider.
  Contributed by @lduesing.
  ([#1411](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1411))

## [v4.1.31] - 2026-08-06

### Added

- **You can now re-apply covers and metadata to your whole library from the
  admin page, instead of a shell.** NextGen writes your edits into the ebook
  files themselves, but only for the book you just edited — so anything changed
  before a fix, or imported with bad metadata, kept the old values inside the
  file even though the web page looked right. The only way to sweep the whole
  library was a `docker exec` command that isn't in the docs. There's now a
  **NextGen Cover & Metadata Enforcement** page, linked from the admin page next
  to the EPUB Fixer, with a Start button, a live progress bar and log, a Cancel
  button, and an archive of previous runs — the same shape as the convert and
  EPUB-fixer pages. This is what you want after updating to v4.1.30, which
  taught NextGen to write `.kepub` files: a single pass backfills every book you
  sent to a Kobo before that release, so your series and tags finally show up on
  the device. Reported by @stripeymonkey.
  ([#1408](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1408))

- **Your Kobo now gets books in Kobo's own format, automatically.** Kobo devices
  read two kinds of EPUB: a plain one, and a "kepub" that Kobo's own store always
  sends. The kepub is the one the device is built for — faster page turns,
  working chapter progress, and highlights and annotations that actually stick.
  Until now NextGen only made a kepub the first time a device asked for a
  particular book, so most of your library sat in the plain format. There's now a
  **Produce and prefer KEPUB for Kobo delivery** switch in Settings → Kobo, **on
  by default** — on a fresh install and when you update to this version — and it
  makes the kepub ahead of time for every book you've already sent to a Kobo, so
  it's ready before the device asks. EPUB stays the source format and nothing is
  replaced; the kepub is an extra file about the same size, so expect the books
  you sync to your Kobo to take roughly twice the disk they do now. Turn the
  switch off and you get the old behaviour. If kepubify isn't installed the
  switch tells you so instead of silently doing nothing.

- **You can turn off the "Read now" and edit buttons on book covers.** If you
  read on an ereader, the "Read now" link on every cover is just noise, and on a
  touchscreen both it and the edit pencil stay visible all the time rather than
  appearing on hover — which made the library look busy. There's now a **Show
  Read now and edit buttons** switch in the library's View settings (the gear
  next to the sort control). Turn it off and the buttons come off every book
  cover, everywhere they appear: the library, shelves, smart shelves, search
  results, Discover and "More by this author". Both actions are still on the
  book's own page, which is what the cover has always linked to. The setting is
  remembered in your browser and is on by default, so nothing changes unless you
  ask it to. Thanks to @Glennza1962 for the request and @chloeroform for the
  detail about how the classic view handled this.

### Changed

- **Metadata working files moved onto your `/config` volume.** The change logs
  and scratch space the cover/metadata enforcer uses used to live inside the
  application folder, which is replaced wholesale every time you pull a new
  image. They now sit alongside the rest of your per-install state, so an edit
  saved moments before an upgrade still gets applied to the book file after it.
  Anything left in the old location is moved across automatically on first
  start; there is nothing to do. Thanks to @chloeroform for the patch.

### Fixed

- **Running the container as a non-root user gave you a container that said it
  was fine and served nothing.** If you start NextGen with `--user`, or under
  rootless Podman with `--userns=keep-id`, every service died the moment it
  tried to switch to its own app user — something an unprivileged process isn't
  allowed to do. The supervisor restarted them forever, so `docker ps` showed
  the container **Up** while nothing was listening on the port and the log
  filled with `Operation not permitted`. NextGen now checks whether it can
  switch users before trying, and stays as whoever you started it as when it
  can't. It also stops trying to take ownership of your files in that mode,
  which produced most of those errors, and says once in the log why. Running
  normally is unchanged. Diagnosed down to the call sites by @KucharczykL, from
  nine days of running it under rootless Podman.
  ([#947](https://github.com/new-usemame/Calibre-Web-NextGen/issues/947))

- **"Source Code" in the package details opened a list of downloads instead of
  the code.** If you inspect the installed package — `pip show`, a package
  index, the dependency view — its Source Code link pointed at the releases
  page, which is already what the Release Management link is for. It now opens
  the repository. The same details block used to send you to the upstream
  tracker for bugs in this build; that was repointed here by @chloeroform in
  [#1298](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1298), and
  this finishes the last link that was still wrong.
  ([#1361](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1361))

- **Kobo syncs wrote to the database once per book instead of once per batch.**
  Each book a Kobo received was recorded in its own separate save, so a sync
  carrying a hundred books did a hundred separate writes — and everyone else's
  pages waited behind them. It's now one write per batch. You'll notice it most
  on a device's first sync and on libraries kept on a NAS, where each write is
  slow.

- **Kobo book covers froze the site while they were being prepared.** Covers are
  padded to your Kobo's screen shape the first time each one is needed, which is
  a fraction of a second of image work — but it was holding up every other page
  while it ran, and it happens once per cover, so a device catching up on a
  shelf-full stacked those pauses back to back. The padding now happens out of
  the way. This affects anyone with Kobo sync on, since cover padding is on by
  default; nothing about the covers themselves changes.

- **With "proxy unknown requests to Kobo Store" turned on, your Kobo could stall
  the site for seconds at a time.** Some of what a Kobo asks for is passed
  through to Kobo's own servers, and the site sat still waiting for their reply —
  up to 12 seconds if they were slow to answer, with everyone else's pages
  waiting too. Measured against the real store, individual calls took anywhere
  from 0.1 to 1.1 seconds. The waiting now happens out of the way. Only affects
  you if you turned that setting on; it's off by default.

- **Sending a large book to a Kobo briefly froze the site for everyone else.**
  If you have "embed metadata" turned on, every book sent to a Kobo is rebuilt
  on the way out so its details are up to date — and that rebuild was holding up
  every other page in the meantime. On a 24 MB book, an unrelated page load went
  from about 10 ms to 493 ms; it now stays at 21 ms, and the book still arrives
  just as fast.

- **A book could end up permanently broken on your Kobo if the server was
  restarted at the wrong moment.** While converting a book for Kobo, the new file
  was written straight into place, so stopping the container mid-write left a
  half-written book behind — and the next run would accept that half-written file
  as finished and record it in the library. From then on your Kobo was handed a
  file it couldn't finish opening, and nothing would ever repair it. Converted
  books are now written aside and only swapped in once complete and verified as a
  readable archive, and a damaged file is never accepted as finished.

- **One unreadable or unwritable book could stop every other book being prepared
  for Kobo — on every restart, forever.** If a single book failed to convert, for
  instance because the library is mounted read-only, the whole preparation run
  stopped at that book and started over from scratch at the next restart, getting
  no further. It now skips what it can't do, reports how many failed, and finishes
  the rest.

- **Right after updating, books sent to a Kobo could arrive in the wrong format
  and take 25 seconds each.** The first run after the update prepares your Kobo
  books in the background, and a download arriving during that window queued up
  behind the whole job, timed out, and fell back to the plain format. Downloads
  now go through immediately while that background work is still running.

- **Kobo syncs were slow on big libraries, and froze everything else while they
  ran.** Every sync re-opened and re-parsed each book's EPUB from disk just to
  check one rarely-used property, every single time — and because that reading
  happened inside the sync request, nobody else could load a page until it
  finished. That answer never changes unless the file itself does, so it's now
  remembered. Measured on a 215-book library on local disk, the per-100-book cost
  dropped from 400 ms to 11 ms; on a first sync after a restart it dropped from
  about 6 seconds to the same 11 ms. Two honest caveats: the memory holds 4,096
  books, and a sync walks the library in order, so libraries larger than that see
  little benefit on a full sync; and on a NAS or network share each book still
  costs one small filesystem check, so the saving is real but smaller than the
  local-disk numbers above.

- **The whole server paused whenever a Kobo asked for a book it hadn't converted
  yet.** The first time a Kobo downloaded any book that didn't already have a
  kepub, the conversion ran inside that request and froze every other page for
  everyone until it finished — and because it queued behind whatever else the
  server was doing, a download landing behind a long import or conversion held
  the freeze for that job's whole duration too. Measured on a 24 MB book, an
  unrelated page load went from 9 ms to 754 ms; it now stays at 21 ms.

- **The whole library stops responding while a book is being imported.** Saving
  a metadata edit, renaming or merging a tag, or uploading while an import was
  running could stop the server answering *anyone* — not just the person who
  saved, but every page for every user, until the import finished. Nothing was
  logged and it recovered on its own, so it read as "the server is randomly
  slow" rather than as one action blocking the rest. Both jobs need the same
  library lock, and the web side waited for it in a way that also parked the
  thread every other request is served from. It now waits without holding
  everyone else up: the edit still queues behind the import, which is correct
  and unchanged, but the rest of the library stays usable while it does.
  Measured on a test instance, an unrelated page load during that wait went
  from 6.5 seconds to 36 milliseconds.

- **The new tag tools read in English on an otherwise Russian interface.**
  Merging a tag, deleting one, and the confirmation prompts that tell you how
  many books are affected all arrived in v4.1.30 without Russian text, so the
  Tags page switched to English at exactly the point it was asking you to
  confirm something destructive. Two upload and reading-position messages had
  the same gap. The new interface falls back to its English text when a phrase
  is missing rather than reporting anything, so the page still worked and
  simply stopped being translated. All thirteen phrases are now translated and
  Russian is complete again at 2,622 of 2,622. Contributed by
  [@standhaftsohnsergius](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1392)
  ([#1392](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1392)),
  translation by ZIZA.

## [v4.1.30] - 2026-08-04

### Added

- **Tags can be merged and deleted from the Tags page.** Tidying up a library
  meant living with whatever tags had accumulated. Renaming a tag onto a
  near-duplicate — "Sci-Fi" onto "SciFi", which is how you merge two tags into
  one — was refused with "A tag with that name already exists", so consolidating
  them was impossible, and there was no way to delete a tag at all, not from the
  Tags list and not from a tag's own page. Now the rename tells you which tag it
  clashed with and how many books that one has, and offers to merge into it; the
  books move across and the leftover tag disappears. Delete removes a tag from
  every book that carries it, and the books themselves are kept. Both are on the
  Tags list itself, so you can spot near-duplicates side by side and fix them
  without opening each tag first, and both ask before they change anything.
  Editing metadata is required, so read-only accounts and guests see the plain
  list as before. Reported by @magdalar.

### Fixed

- **Series and other metadata edits now reach your Kobo.** Setting a series name
  and number on a book saved fine in the library, and the change showed up in the
  web interface, but the book on a Kobo still had no series — and downloading the
  file back confirmed the series was missing from it. Books converted for Kobo are
  stored as `.kepub` files, and those were the one format the metadata writer
  skipped, so every edit reached the `.epub` and the library while the file your
  reader actually opens was left untouched. Re-saving did not help, because
  nothing was wrong with the edit. `.kepub` files are now written too, so setting
  or changing a series, adding tags and updating the cover turn up on the device
  after the next sync. Clearing a field is not covered yet — remove a series or a
  tag and the `.kepub` keeps the old value, tracked in #1376. Existing books pick
  up their metadata the next time you edit them, or in one pass from Settings if
  you run the cover and metadata enforcement over the whole library. Reading
  positions and bookmarks already on your reader are preserved. Reported by
  @bjekel.

- **Books already sitting in the ingest folder when the container starts are
  now imported.** If a book was waiting in the ingest folder at the moment
  Calibre-Web NextGen started — you copied files in while it was stopped, the
  server rebooted mid-copy, or an import was still pending when the container
  restarted — it was never picked up. No error appeared, nothing showed in the
  log, and the book simply never arrived in the library. Restarting did not
  help; the only way out was to touch or re-copy every file. The ingest folder
  is now swept once at startup, so anything waiting there gets imported. Books
  left in the retry queue by a previous run are also picked back up instead of
  waiting for an unrelated file to arrive.

### Security

- **Requests that change your library are now refused when they come from
  another website.** Every write the new interface makes already had to carry a
  one-time token, and a page on another site cannot read that token — but if one
  ever obtained it, the server would have carried out the write without noticing
  the request came from somewhere else entirely. It now checks, for the whole
  `/api/v1` surface at once rather than route by route, and refuses anything that
  says it came from a site other than yours. Nothing changes for ordinary use:
  your own browser identifies itself correctly, and tools like `curl` or a script
  that send no such information keep working as before. If you reach your library
  through a reverse proxy, it has to tell the server both the address **and**
  whether the connection is `https` — a proxy that handles TLS but forwards no
  `X-Forwarded-Proto` leaves the server thinking the request arrived over plain
  `http`, and writes are refused even though the address matches. Most proxies do
  this correctly out of the box. If yours does not, either forward
  `X-Forwarded-Host` and `X-Forwarded-Proto`, or set the existing `PROXY_HOST` and
  `PROXY_SCHEME` variables, or name the address you actually use in a new optional
  `CWNG_TRUSTED_ORIGINS` setting, comma-separated. No setting is needed for a
  normal install, and the same symptom already showed up as `http://` links in
  emails and redirects, so a install that works today is very likely unaffected.

- **The Statistics page no longer shows your server's version details to
  everyone.** It listed the exact Calibre-Web NextGen release, the host kernel
  build, the Python build and the version of every library the server uses —
  around 70 entries — to any visitor who could open the page. On an instance
  with guest browsing turned on, that included people who were not signed in at
  all. It is enough detail to look up known vulnerabilities for the exact
  software you are running, which matters if your instance is reachable from
  the internet. Those details now go to admins only, and the server withholds
  them rather than just hiding them on the page, so they are no longer sent to
  anyone else. Book, author, series and category counts are unchanged for
  everyone. Reported by @kabili207; @chloeroform sent the first fix and the
  page-side change.

## [v4.1.29] - 2026-08-03

### Fixed

- **Marking a book unread no longer marks it read instead.** Telling a book you
  had not read it — from the book editor, a bulk edit, or the API — set it to
  *read* whenever that book had never been marked either way before. The
  opposite of what was asked, and the only way to notice was to look at the
  checkmark afterwards. It happened on ordinary libraries and on ones where an
  admin has pointed Calibre-Web at a custom column for read status. Both now
  record what you actually asked for.

  Read status is also kept in step now if you use a custom column for it.
  Calibre-Web tracks reading in two places — your column, and an internal record
  the Kobo and KOReader sync both write to — and only the column was being
  updated when you toggled a book. So the "Currently reading" marker could drift
  away from the checkmark on the book, and a book you had marked Read could
  still be reported to your Kobo as one you were part-way through. Both records
  now follow the toggle.

  One limit worth knowing if you use a custom read column: the toggle reaches
  your Kobo for books it has already synced, but not yet for a book the device
  has never seen. That gap is tracked on
  [#1350](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1350).

- **Japanese and Chinese ebooks now turn the page the right way in the new
  reader.** Books that read right-to-left were paged as if they read
  left-to-right: the button to go forward sat on the right of the screen, so
  tapping the side you actually read towards took you backwards a page instead
  of onwards. The arrow keys were reversed in the same way. Forward now sits on
  the left for these books, where it belongs, and the buttons announce what they
  really do for anyone using a screen reader. Books that read left-to-right are
  unchanged.

  This covers epub. Comics and manga read as CBZ or CBR still page
  left-to-right — they carry no equivalent marker for reading direction, so that
  needs its own detection and is tracked on
  [#1354](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1354).

- **Your reading position survives a busy database instead of being dropped.**
  Both readers save your place constantly — the classic reader on every page
  turn, the new one every second or so — and if that save failed because
  something else was writing to the database at that moment, the browser was
  told it had worked. It hadn't: the position was thrown away, and because the
  browser thought it was saved, nothing ever went back for it. You would come
  back to the book and find yourself pages behind, with nothing in the log to
  explain it. A save that fails now reports the failure instead of a success,
  and both readers act on it: the new reader retries a few times, and the
  classic reader keeps your place locally and sends it the next time you open
  the book.

  Two honest limits. There is still no on-screen warning when a save fails for
  good — the new reader announces it to screen readers only
  ([#1352](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1352)) —
  and if you close the tab within a few seconds of turning a page, the new
  reader can still lose that last page turn. Neither is a step back from the
  previous release; the new reader did not send your position anywhere at all
  before this one.

  The same "said it worked when it didn't" answer turned up in three other
  places, all now fixed: changing an admin password from the command line could
  print "Password for user X changed" and exit successfully when the change had
  in fact been rolled back; revoking a Kobo sync token could report success
  while the token stayed valid; and editing an allowed-registration domain in
  the admin panel could show the new value in the table without it being saved.

- **The Epub Fixer stops reporting the same fixes every time you run it, and
  stops touching books you deleted.** Running it over the same library
  repeatedly kept listing conversions like "Converted page_styles.css from
  ascii to utf-8" — on every run, for books it had already been through. The
  conversion was never real: a stylesheet that is plain ASCII is *already*
  valid UTF-8, so nothing was being changed, but it was counted and announced
  as a fix anyway. Worse, every book was rewritten and copied into the backup
  folder whether or not anything about it changed, so a library-wide run
  restamped every file — which pushes the whole library back through Kobo,
  KOReader and any file-sync you have set up, and grew the backup folder every
  time. Books that genuinely need fixing are still fixed, backed up and
  rewritten exactly as before; books that don't are now left alone, and the log
  says "No issues found" instead of inventing two. A book whose language
  Calibre never set was counted the same way: the fixer left the language
  exactly as it found it and still recorded that as a repair, adding a row to
  the Epub Fixer history on every run. It no longer does. The fixer also no
  longer walks Calibre's hidden `.caltrash` folder, so books you deleted are no
  longer processed and reported alongside the ones in your library.

- **The "update available" banner stops re-appearing every time you restart.**
  The banner is meant to show at most once a day, and it remembered the date it
  last appeared in a file. That file was kept in a part of the container that
  gets wiped whenever the container is recreated, which is exactly what happens
  when you pull a new image. So the reminder forgot itself at the one moment it
  was most likely to be redundant, and admins saw it again on the next page
  load. It now lives in your `/config` folder alongside the logs, so the
  once-a-day promise holds across restarts and upgrades. You may see the banner
  one extra time on the first start after updating, then it settles.
  Thanks to @chloeroform for finding and fixing this.

### Added

- **Reading in your browser now counts towards your reading progress
  everywhere else.** Until now the web reader kept its position to itself: it
  could *show* you how far your Kobo or KOReader had got, but reading a few
  chapters in the browser left no trace. Your Kobo still thought you were where
  you left it, and the book still showed the old percentage in your library.
  Picking a book up on the web during a lunch break and then going back to your
  device meant finding your place by hand. Now the position you reach in the
  browser travels the other way too — your Kobo picks it up on its next sync,
  the progress shown on the book updates, and finishing a book in the browser
  marks it read. Both the classic reader and the new interface do this.

  Reading backwards never costs you anything: if your Kobo is at 80% and you
  flip back to chapter 1 in the browser, your own place in the browser follows
  you, but the furthest point stays 80% so nothing on your device is lost.
  Starting a book over is still "mark as unread", which clears it everywhere as
  it always has.

  One honest limit: this covers the Kobo direction and the progress shown in
  your library. KOReader reads its position from a separate store in a format
  only KOReader understands, so it doesn't pick up browser reading yet — that
  half needs a position translation and is still tracked on
  [#324](https://github.com/new-usemame/Calibre-Web-NextGen/issues/324).

  Worth knowing if your server has several users *and* an admin has pointed
  Calibre-Web at a custom column for read status: that column belongs to the
  book rather than to each reader, so one person finishing a book in the
  browser now shows it as read for everyone. Marking a book read by hand always
  worked that way on those libraries; what is new is that reading to the end
  does it too. Ordinary libraries keep read status per person and are
  unaffected. Discussion of what the right behaviour should be is on
  [#1351](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1351).

## [v4.1.28] - 2026-08-02

### Fixed

- **The button at the bottom of a page works again while the "new interface"
  notice is on screen.** The notice sits along the bottom of the window, and it
  was covering the last thing on the page — so clicking the middle of that
  button did nothing at all, and only a thin sliver along its top edge
  responded. Scrolling all the way down did not help. The clearest case was the
  emergency "Restore Calibre Database (Last Resort)" button on Admin → Database
  Configuration, but it applied to any page ending in a button or a link. On
  phones it was worse: the notice is taller there because it wraps onto two
  lines, and it hid the last button completely rather than just its lower edge.
  Pages now keep enough space clear at the bottom for the notice, at every
  screen size. Dismissing the notice was always a workaround and still is; you
  no longer need it.

- **The emergency "Restore Calibre Database (Last Resort)" button now actually
  restores.** On Admin → Database Configuration, clicking it did nothing at all —
  no message, no error, no restore. It was quietly saving the database settings
  again instead, so anyone reaching for it during a real library corruption got
  a button that looked like it worked and didn't. It now rebuilds your Calibre
  database from the OPF files in your library, as the page describes. The rest of
  the page is unchanged, and the Save button is unaffected. Spotted and diagnosed
  upstream by @luisalduucin.

- **Typing a page address with a slash on the end no longer gives you "404 Not
  Found".** `/kosync/` failed while `/kosync` worked, and the same was true of
  156 other pages — admin settings, your profile, statistics, search, the shelf
  and author pages. Links inside the app were always fine, so this only bit
  people who typed an address, bookmarked one, or followed a link from a forum
  post that happened to end in a slash. Addresses that end in a slash now take
  you to the page instead of an error, including behind a reverse proxy on a
  sub-path. Reported by @iroQuai.
- **The admin settings page no longer leaves one option in English.** With the
  interface in another language, the "Default book language" dropdown still
  opened on "Show All" while every other label on the page was translated. It
  now reads in your own language — "Alle talen" in Dutch, "Montrer tout" in
  French. The same dropdown in your account settings was already correct; both
  pages now build it from one place, so they cannot drift apart again. Reported
  by @iroQuai.
- **The Upload button no longer disappears once you browse anywhere.** In the
  new UI it only ever showed on the plain Library page, so the moment you opened
  an author, a series, a tag, Hot, Discover, Top Rated or a book, there was no
  way to add a book at all — the classic view keeps Upload in the toolbar on
  every page. Upload now stays put while you browse, and it is also in the
  account menu (next to Admin), so it is reachable from anywhere including on a
  phone. Reported through the in-app feedback form.
- **"Enable Uploads" now actually disables uploads.** Switching it off in Admin
  hid the button in the classic view but nothing more — the new UI still offered
  Upload, and the upload request still went through either way. The setting is
  now enforced on the server and the button is hidden in both views. Uploading
  stays on by default, so nothing changes unless you deliberately turned it off.

## [v4.1.27] - 2026-08-02

### Changed

- **The admin "Version Information" table now reports the Calibre you are
  actually running.** It used to show a value stamped into the image at build
  time, so if the Calibre binaries had been replaced, or the converter path
  pointed somewhere else, the number on the page was not the number in use.
  It is now read from the binary itself — the same source the Statistics page
  already used — and formatted as `v9.11.0` to match the rows above it. When
  Calibre can't be found or can't be run, the row now says which of the two it
  is instead of "Unknown". Thanks to @chloeroform for the change.

- **The Kepubify row of that same table now reports the running binary too.**
  It had the same problem for the same reason, and it was the more visible of
  the pair: the Statistics page already read the real binary, so the two pages
  could show different Kepubify versions on the same install with no way to
  tell which one was right. Both now read the same source. As with Calibre, a
  Kepubify that can't be found or can't be run says so rather than showing
  "Unknown". Thanks again to @chloeroform.

- **The Russian interface is complete again — the custom-columns section of the
  edit-metadata screen now reads in Russian.** Three phrases there were still in
  English on an otherwise fully Russian page: the "Custom columns" heading, the
  "Not set" placeholder shown for an empty column, and the hint telling you a
  field takes comma-separated values. Russian is back to every string
  translated. Contributed by
  [@standhaftsohnsergius](https://github.com/standhaftsohnsergius)
  ([#1269](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1269)).

### Fixed

- **The "Duplicates found" popup kept naming books you had already deleted.**
  If you removed duplicate copies in Calibre itself rather than in the web app,
  the popup carried on listing them — while the Duplicates page and a fresh
  scan both correctly reported nothing. The popup was reading a list saved at
  the time of the last scan and never re-checked it against your actual
  library, so it was the one place still remembering the deleted books. It now
  re-checks before it shows, and a group whose books are gone (or archived, or
  hidden from you) drops out. Groups that merely lost one copy now show the
  real remaining count instead of the stale one. The sidebar duplicate badge
  reads the same number, so it was wrong in the same way and is fixed too.

- **A duplicate you dismissed could come back on its own.** Dismissals were
  being matched against a label built from the title and author of whichever
  copy happened to sort first, so editing a book's metadata — or importing
  another copy — quietly changed the label and the dismissal stopped applying.
  Dismissals now hold onto a stable identity that metadata edits don't move.
  Thanks to @blahblah57, whose report on the duplicates popup led here.

- **Marking a book unread left its "Started reading" and "Last synced" dates on
  the page.** The percentage cleared, but the two dates stayed — and "Last
  synced" jumped forward to the moment you pressed the button, so a book you had
  just marked unread looked like it had synced seconds ago. The reading position
  the device holds was also left behind, which meant a Kobo could quietly restore
  the exact spot you had just cleared on its next sync. Marking a book unread now
  clears the whole position: both dates and the device resume point. Books that
  were already left in this state by an earlier version display correctly again
  without any migration. Thanks to @uschi1 for catching it and following up.

- **Saving a book from the edit-metadata screen, or switching on a metadata
  source, made the server stop answering everyone.** Not only the tab doing the
  work — every other person's page load hung for as long as the cover download
  or the metadata lookup took, which on a slow cover host is up to 30 seconds.
  Both actions now do their network work off the request handler, so the rest of
  the site keeps responding while they run. Measured on a real server during a
  1.5s cover download: other page loads went from 1 request served with a
  1254ms worst-case wait, to 201 served with an 18ms worst case.

## [v4.1.26] - 2026-07-31

### Changed

- **Ebook conversion and metadata reading now run on Calibre 9.11.0**, up from
  9.1.0. Ten minor versions of Calibre fixes land in one step, covering format
  conversion, metadata extraction and the ingest path — so books that previously
  converted badly, imported with wrong or missing metadata, or failed to ingest
  at all are worth retrying. Thanks to @chloeroform for the upgrade and
  @darkmatterpelican for reporting it.

### Fixed

- **KOReader's plugin updater kept saying "no new release available" when a newer
  sync plugin existed.** If you had pointed Updates Manager (or
  appstore.koplugin) at the main Calibre-Web NextGen repository, it stopped
  finding updates after v4.1.16 — nine releases in a row shipped no plugin
  download for it to see, so it reported you were current while three plugin
  fixes went out without reaching your device. Plugin-changing releases now
  attach the plugin download automatically, so that setup updates itself again.
  Releases that don't touch the plugin still publish nothing, so you won't be
  prompted to reinstall an identical plugin.

- **The interface now reads in Polish throughout, instead of leaving about two
  thirds of its labels in English.** With the language set to Polish, the admin
  screens, metadata editing, upload, shelves, the reader and a long tail of task
  and error messages still showed in English. A further 273 phrases were worse
  than untranslated: gettext had guessed them from a similar English sentence and
  marked the guess provisional, and a provisional entry is dropped when the
  catalog is compiled — so those rendered in English while Polish that said
  something else sat in the file waiting for somebody to confirm it. Polish was
  covering 950 of 2,609 phrases and now covers all 2,609, which makes it the most
  complete translation the project ships. Contributed by
  [@bywciu](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1249)
  ([#1249](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1249)),
  building on the original Polish translation by Radosław Kierznowski.

- **German now reads in German across several hundred more labels and
  messages.** With the language set to German, a long tail of admin screens,
  task and error messages, and much of the new interface still showed in
  English. German was covering 1,584 of 2,609 phrases and now covers 1,891.
  Two of those were previously worse than untranslated: the *Email Your Users*
  admin heading said "Benutzer bearbeiten" ("edit user") and the warning for
  sending mail with nobody chosen said "select a book" rather than "select a
  recipient". Both are now correct, and both now actually appear — gettext had
  them marked as unconfirmed guesses, and an unconfirmed entry is dropped when
  the catalog is compiled, so the German sat in the file while the screen
  showed English. Contributed by
  [@monimkxl-web](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1264)
  ([#1264](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1264)).

- **Every CWA settings page 404'd when the app was mounted under a subpath.** If
  you run Calibre-Web NextGen behind a reverse proxy on a prefix that starts the
  same way as its own pages — `/cwa` being the obvious one — then "CWA settings
  (ingest/convert)", "Duplicate detection settings", the statistics dashboard,
  and the library-refresh button all came back as Not Found, while the ordinary
  Admin links right beside them worked. That mismatch was the tell: the mount
  prefix was being removed from any address that merely *began* with the same
  letters, so `/cwa-settings` was cut down to `-settings`, which is not a page.
  All 37 CWA pages and actions were affected — settings, duplicate detection,
  library refresh, log viewing and downloads, the EPUB fixer, library conversion,
  scheduled tasks and the statistics screens. The prefix is now only removed at a
  real path boundary, so both styles of proxy setup work: one that strips the
  prefix before passing the request on, and one that leaves it in place. A prefix
  written with a trailing slash (`PROXY_SCRIPT_NAME=/cwa/`) is also accepted now
  — it used to break every page in a second, separate way. Reported by
  [@chloeroform](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1248)
  ([#1248](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1248)).

### Added

- **The KOReader page now explains how to auto-update the sync plugin.** Visiting
  `/kosync` only ever described the manual download-and-copy route, so the
  in-place update path existed but was undiscoverable — the repository to point
  an update manager at was written down nowhere a user would look. That page and
  the README now name it, and spell out why the plugin's version can sit behind
  your server version without anything being wrong.

## [v4.1.25] - 2026-07-30

### Added

- **You can give ComicVine your own API key.** ComicVine searches have always
  gone out on a single key shipped inside the app that every install shares, so
  one busy install can use up the allowance for everyone and searches quietly
  come back empty. You can now put your own free key in the 🔑 Keys panel of the
  metadata search window and get your own allowance. Nothing changes if you
  don't: ComicVine keeps working out of the box on the shared key. If you prefer
  to configure it at the container level, `COMICVINE_API_KEY` and
  `COMICVINE_API_KEY_FILE` work too. When ComicVine does refuse a search, the log
  now says so and tells you which key was used, instead of looking like the book
  simply wasn't found. Reported by
  [@tomaioo](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1242)
  ([#1242](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1242)).

- **Custom columns are editable in the new UI.** Your own calibre columns — page
  counts, reading status, notes, shelf location — showed up on a book's page but
  there was nowhere to change them, so setting one meant switching back to the
  classic view. The edit screen now has a Custom columns section with the right
  control for each column type: a number box for integers, a date picker, a
  yes/no menu, a star rating, a text area for long notes, and a dropdown of the
  allowed values for enumerated columns. Reported by @jasonxbergman (#997) and
  by mx.meredith on Discord.

### Fixed

- **A mistyped API-key file path can no longer hang or crash the whole server.**
  If you supply a provider token by pointing `HARDCOVER_TOKEN_FILE` or
  `COMICVINE_API_KEY_FILE` at a file (the Docker-secrets style), the app reads
  that file when it needs the token. Point it at something that isn't a plain
  file — a named pipe nothing is writing to, or a device like `/dev/zero` — and
  the read never finished: the whole app froze, or memory climbed until it was
  killed. Both now fail cleanly with a log line naming the file, and the read is
  size-limited. A correctly configured secret file behaves exactly as before.

- **A custom column holding just the word "None" no longer erases itself when
  you save.** Type `None` into a notes or comments column — on its own, as the
  whole value — and the column came back empty, in both the new UI and the
  classic editor, with the save reporting success. Anything already in the
  column was lost. `None` is the value the yes/no dropdown uses behind the
  scenes to mean "not set", and that meaning was being applied to every kind
  of column, including free-text ones where it is ordinary writing. Number and
  date columns are deliberately unchanged: there the word still empties the
  field, as it always has
  ([#1233](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1233)).

- **Deleting a highlight in KOReader now actually removes it from the server.**
  Creating highlights synced fine, but deleting one reported "Server push
  failed" and the deletion never went anywhere — reopen the book on another
  device and the highlight was still there. The push was failing inside the
  plugin before it ever became a request, so nothing showed up in the server log
  either, which is why this took several passes to find. The KOReader plugin is
  updated to 4.1.25; open KOReader's Updates Manager to pick it up, or the plugin
  page in its menu will show the new version. Reported by
  [@iroQuai](https://github.com/new-usemame/Calibre-Web-NextGen/issues/920)
  ([#920](https://github.com/new-usemame/Calibre-Web-NextGen/issues/920),
  [#905](https://github.com/new-usemame/Calibre-Web-NextGen/issues/905),
  [#699](https://github.com/new-usemame/Calibre-Web-NextGen/issues/699)).

- **The version number on the admin page no longer links to a release that
  doesn't exist.** On a build that was never stamped with a version — running
  from a source checkout rather than one of the published Docker images — the
  version read `v0.0.0`, and because that looks like an ordinary version it was
  turned into a link to a release tag of that name, which 404s. It now renders
  as plain text when there is nothing real to point at. The same page also
  stopped showing a stray blank line after the version, and now agrees with the
  version the app reports to other services. Contributed by
  [@chloeroform](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1231)
  ([#1231](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1231)).

- **Brazilian Portuguese covers 120 more of the interface, and 57 phrases that
  were holding the wrong text are corrected.** With the language set to
  Brazilian Portuguese, the Hardcover match-review screens, the announcement
  email form, backup and restore messages, the app-password help text and a
  long tail of task and error messages still read in English. Some of those
  were worse than untranslated: gettext had guessed them from a similar English
  sentence and marked the guess provisional, so the file was holding Portuguese
  that said something else entirely — "Hardcover ID applied successfully" was
  carrying the text for "{} user(s) removed successfully", and "Email Your
  Users" was carrying "Edit Users". A provisional entry is dropped when the
  catalog is compiled, so those showed in English while the wrong Portuguese sat
  in the file waiting for somebody to confirm it. All 57 are now written out
  properly, which takes Brazilian Portuguese to 1,409 of 2,609 phrases.
  Contributed by [@pedronora](https://github.com/pedronora)
  ([#1227](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1227)).

- **Ingest told you to install an ACSM plugin you already had.** When an `.acsm`
  fulfillment ticket failed to convert, the log always said no ACSM-capable
  plugin was installed — even when one was installed, had run, and had printed
  the actual reason it gave up, such as `DeACSM v0.0.16: ADE auth is missing or
  broken`. That sent people looking for a missing plugin instead of at the real
  problem. The ingest log now repeats the plugin's own reason when a plugin ran,
  and still suggests installing one only when nothing handled the file. Reported
  by @auspex (#984).

## [v4.1.24] - 2026-07-29

### Fixed

- **The "Browse languages" button on the What's New page now reads in Russian.** Russian is otherwise fully translated, so this one phrase sat in English on a screen that already said "Просмотр авторов" and "Просмотр тегов" right beside it. The new interface uses its English text as the lookup key, so a missing translation quietly renders the English rather than reporting anything — which is how a single gap like this survives until someone reading the page notices it. Russian is now complete at 2,606 of 2,606 phrases. Contributed by [@standhaftsohnsergius](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1220) ([#1220](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1220)).

- **The new interface now speaks Dutch throughout, instead of showing most of its labels in English.** The sidebar, search, upload, book pages, shelves and settings were largely English even with the language set to Dutch, which is what "several labels are still in english, others in Dutch" meant when it was reported. Nothing was broken, which is why it lasted: the new interface uses its English text as the lookup key, so a missing translation quietly shows the English rather than reporting anything, and only someone reading the screens would notice. Of the 821 phrases the new interface can translate, Dutch had 84. It now has all of them — 737 added. Along the way the wording was lined up with the Dutch already used on the older screens, so tags read as "Labels" and shelves as "Boekenplanken" everywhere rather than changing name depending on which page you were looking at. The five built-in shelves ("Currently Reading", "Highly Rated" and the rest) were a separate problem found while checking the fix: they come from a different part of the code, so they sat in English in an otherwise Dutch sidebar even once everything else was translated, and they are now Dutch too. So were the sidebar's Appearance and theme settings, the sort fields, the whole list of search filter conditions ("contains", "is between", "is empty") and the What's New buttons: the check that was meant to catch missing Dutch could only see phrases written out at the point they are displayed, and those are assembled from a list, so 76 of them were never checked and stayed English while the check reported Dutch as complete. The check now works from the full set of phrases the app ships for translation, which is what the two earlier gaps had in common, so a phrase cannot go missing by virtue of how it happens to be written. Release notes stay in English on purpose. Other languages are still mostly untranslated in the new interface and are tracked separately. Reported by [@iroQuai](https://github.com/new-usemame/Calibre-Web-NextGen/issues/886) ([#886](https://github.com/new-usemame/Calibre-Web-NextGen/issues/886), [#1217](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1217)).

- **Most of the new interface now speaks French instead of falling back to English.** Uploading a book, editing its metadata, the Files and convert-format panel, your profile page, the admin screens, the cover picker and the reader were largely in English even with the interface set to French. Nothing was broken, which is why it lasted: the new interface uses its English text as the lookup key, so a missing translation quietly shows the English rather than reporting anything, and only someone reading the screens would notice. Of the 821 phrases the new interface can translate, French had 276. It now has all of them — 545 added — so the pages in the report are covered along with the ones not yet reached, including the Appearance and theme settings, the sort fields and the full list of search filter conditions ("contains", "is between", "is empty"). Two checks were added so this cannot creep back unnoticed: one fails the build if a French phrase goes missing, and one covers all 28 languages, catching a translation that loses a placeholder like the name in "Reset password for {name}" — which would look translated while rendering wrong. Release notes stay in English on purpose. Other languages are still mostly untranslated in the new interface and are tracked separately. Reported by [@hayvan96](https://github.com/new-usemame/Calibre-Web-NextGen/issues/615) ([#615](https://github.com/new-usemame/Calibre-Web-NextGen/issues/615)).

- **A KOReader sync that could not read the device's highlights no longer reports them as deleted.** To work out what you removed, the plugin compares the highlights on the device now against the ones it last sent, and anything missing is named to the server as a deletion. Whether it had actually managed to read the device was decided by a fixed property of the device type rather than by the read itself, so a read that came back with nothing was indistinguishable from a book you had cleared by hand — and every highlight in it was named for deletion. Coming back with nothing is not unusual: the plugin picks the source before asking the server for the book's highlights and reads it afterwards, so closing the book while that request is in flight is enough, as is the reader's own database being locked or an SD card being pulled. The server carries out the deletions it is given and does not bring a deleted highlight back, so the loss was permanent. A read that fails now says so, and a sync that cannot see your highlights leaves them alone; one that genuinely finds none still syncs the deletion, so removing your last highlight works as before. Found while investigating [#920](https://github.com/new-usemame/Calibre-Web-NextGen/issues/920), reported by [@iroQuai](https://github.com/new-usemame/Calibre-Web-NextGen/issues/699). Needs plugin 4.1.24 on the device.

- **Adding a book no longer fills the log with "Permission denied" warnings, and the one-time database upgrades now actually record that they've run.** Every ingest printed five warnings about being unable to write to `/app/calibre-web-automated/.cwa_migrations`, a folder that doesn't exist and isn't writable. The upgrades themselves worked, so nothing was broken in a way you could see — but because they could never write down that they'd finished, they ran again from scratch on every single book that came in. The cause was that the app works out where to keep its own files from a setting that was only being handed to two of the eleven background services. The ingest service wasn't one of them, so it kept its notes in the program folder instead of your config folder, while everything else used the right one. That setting is now applied once for the whole container, so every service agrees on where state lives, including any added later. Reported by [@auspex](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1162) ([#1162](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1162)).

- **When a highlight fails to sync, KOReader now tells you why instead of just "Server push failed".** The plugin knew the reason and threw it away: it was written to the debug log, which is off unless you have turned it on, and the part of the code that shows the message was handed a blank. So a sync that never left the device looked exactly like one the server had rejected, and neither the device nor the server had anything written down about it. The reason is now shown on screen and recorded in `crash.log`. This is a diagnostic change rather than a fix for the underlying failure, and it is what the remaining investigation into highlight deletions ([#920](https://github.com/new-usemame/Calibre-Web-NextGen/issues/920), reported by [@iroQuai](https://github.com/new-usemame/Calibre-Web-NextGen/issues/920)) has been waiting on.

- **A running install is told about new releases again, instead of being stuck on whatever was newest the day its container started.** The admin page's "Update available" line compared your installed version against a value fetched once, at container start, and written to a file — so a container that had been up for a week was still comparing against the release list from a week earlier, and the notice for anything published since never appeared. Restarting the container was the only way to refresh it, which is the one thing someone who doesn't know an update exists has no reason to do. The latest release is now looked up when the page is actually rendered, cached for six hours so it costs at most a handful of requests a day, and run on a background thread so a slow lookup delays only that one page and never holds up anyone else's browsing. Two related mix-ups went with it: `--version` on the command line and the updater's own version report both named the newest *published* release rather than the one you were running, which made the updater tell some installs they were already current when they weren't. Reported by [@chloeroform](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1108) ([#1108](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1108)).

## [v4.1.23] - 2026-07-28

### Fixed

- **A newly imported book no longer has a different book's details written over it.** With automatic metadata fetching switched on, importing a book would sometimes replace its correct title, author and description with those of an entirely different book — often another title by the same author, sometimes one that isn't published yet. Nothing flagged it: the log read `Successfully applied`, and the only way to notice was to spot the wrong book in your library later. The cause was that whichever result a metadata site happened to list first was accepted without checking it was even the same book. A freshly imported file usually carries no ISBN, and the ISBN was the only thing being checked. Results are now matched on the title's identifying words, and one that doesn't match — or that matches the title but names a different author — is refused, leaving the book as it was imported. Books in a series are the case this most needed to get right, since their titles differ by only a word or two. Reported by [@auspex](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1164) ([#1164](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1164)).

- **A book you just edited no longer drops out of your library.** Save a change to a book, head back to the library, and the book you had just edited was missing from the grid until you reloaded the page. The library holds on to the cards it has already loaded so that coming back from a book leaves you where you were, and saving an edit was pulling the edited book straight out of that store — which is the right thing to do when a book is deleted, and the wrong thing here. The book still exists and still belongs in the list, and the only page the library asks for again on the way back is the one you were sitting on, so a book from any earlier page had nothing to bring it back. Where it did return, it returned as the very last card of everything loaded, which from the top of the library is indistinguishable from gone. An edited book now keeps its place in the list and shows its new title, author and series straight away, including on pages you have scrolled past. Reported by [@magdalar](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1169) ([#1169](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1169)).

- **KOReader devices stopped recognising a book once its file changed on the server, and re-downloading it never helped.** Progress sync identifies a book by a fingerprint of the file's contents, and the server only knows the fingerprints of files it has actually served. The plugin was reading that fingerprint out of KOReader's own cache, which is stored per file location and survives the file at that location being replaced. So once a book's file changed on the server (a Calibre database rebuild, a metadata write-back, a re-import), the device kept reporting the fingerprint of a copy it no longer held, and the server logged `No book found for checksum`. Re-downloading the book made things worse rather than better, because each download registered one more fingerprint the device would never produce. The plugin now works the fingerprint out from the file it actually has, and falls back to the cached value only when that file cannot be read. Update the NextGen Progress Sync plugin to 4.1.23 on each device to pick this up. Reported by [@Metamatam](https://github.com/new-usemame/Calibre-Web-NextGen/issues/991) ([#991](https://github.com/new-usemame/Calibre-Web-NextGen/issues/991)).

- **The edit pencil no longer sits on top of a book's series or author line, and "Read now" no longer breaks across two lines on a phone.** The bottom of each book card wasn't a real layout: the pencil floated over the card and room for it was reserved by padding the "Read now" label. That works only while the card is wider than the space being reserved — on a phone in Compact or Dense view the card is around 82–106 pixels and the reservation alone was 60, so the label wrapped, the row grew, and the pencil rode up into the line above it. Books with no readable format had no "Read now" at all, so the pencil landed directly on the series. The two controls now share one row, sized by the browser rather than guessed at, and on the narrowest cards the pencil scales down and the label gives way to its icon instead of being squeezed. Reported by rogovmtlz on Discord, [@iroQuai](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1166) and [@HLRobius](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1166) ([#1166](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1166), [#1112](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1112)).

- **A book whose title, author or series contains one very long word no longer drags the page sideways on a phone.** The header column beside the cover is only about 217 pixels wide on a phone, and a word longer than that — a German compound title, a long transliterated name, a lengthy series title — ran straight past the edge and made the whole book page scroll horizontally. Those three lines now wrap mid-word when a word cannot fit on a line by itself; ordinary titles are unchanged, since wrapping still prefers to break between words ([#1178](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1178)).

## [v4.1.22] - 2026-07-27

### Changed

- **The "read" mark on a book cover is now a green "Read" label at the bottom-left, instead of a small tick in the corner.** It was a 22-pixel checkmark circle in the top-right with no wording, which was easy to miss at a glance — the reporter, who reads in the light theme because of their eyesight, pointed out that the classic view's labelled green badge was far easier to spot. It now says what it means, sits where the classic one sits, and gets larger again on phones and tablets. The wording follows your language, so a German library reads "Gelesen" exactly as the classic view does. Reported by [@uschi1](https://github.com/new-usemame/Calibre-Web-NextGen/issues/351) ([#1117](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1117)).


### Fixed

- **Books whose language is written as `ger`, `fre` or `dut` now show that language instead of "Unknown".** A language can be recorded two ways in book metadata: `deu`/`fra`/`nld`, which is what Calibre normally writes, or `ger`/`fre`/`dut`, which is what library-catalogue records and some EPUB files carry. Only the first form was recognised, so a book using the second showed "Unknown" as its language on the book page and in the language list, and importing one could be rejected outright with "'ger' is not a valid language". Both forms are now accepted for all twenty languages where they differ — German, French, Dutch, Chinese, Czech, Greek, Icelandic, Persian, Welsh and the rest. Relatedly, whether a language counts as valid no longer depends on the language you read the interface in. The list of language names is translated separately for each interface language and is incomplete for most of them, and importing checked a book against that list, so a Greek book could be refused as invalid for someone using the site in Portuguese while importing normally for someone using it in English. Imports are now checked against the full list of languages instead. The log line this produced on every single lookup has also been lowered from an error to a warning, since a language code we don't recognise is a detail of the book's metadata and not a fault in the server ([#1109](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1109)).

- **Two people using the library at once no longer break each other's pages.** The server kept one working copy of your library data and shared it across every request it was handling. As soon as any one page finished, it closed that copy, so any other page still being built lost the books it had already read and failed. In the log it showed up as `Instance ... is not persistent within this Session`, pointing at whatever the unlucky page happened to be doing at the time, which made it look like a different bug on every occurrence. It got likelier the busier the server was, so it hit big libraries, shared instances and anything running during an import hardest. Each request now keeps its own working copy ([#1150](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1150)).
- **A page you are reading no longer breaks because a background job finished.** Duplicate scans, thumbnail generation, metadata backups and Hardcover syncs all share the server's connection to your library, and when one of them finished it could close that connection while a page you had open was still reading from it. The page then failed, usually with `Cannot operate on a closed database` in the log. It was intermittent by nature — it only bit when the timing overlapped, which is why a manual scan could break browsing once and then work fine on the next try. Background jobs and page loads now keep their own handle on the library, so one finishing can no longer interrupt the other ([#1121](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1121), reported downstream of [#1048](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1048) by [@auspex](https://github.com/auspex)).

- **Your library keeps working when its extra fields can't be read.** Book pages, the books table and both search screens all ask the library for its custom-column definitions — the extra fields you can add to a book in Calibre. If that lookup failed because the library was mid-write, its folder had moved, or the definitions table wasn't there yet, those pages returned an error instead of simply leaving the extra fields out, even though the rest of the library was perfectly readable. Custom columns are supplementary, so these pages now load without them and note the reason in the log ([#1153](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1153)).

- **A book that fails to convert now still lands in your library.** If CWA could not convert an incoming book to your target format — a big or image-heavy PDF that ran out of time, a file with internals Calibre chokes on, a format needing a plugin you don't have — the book was dropped altogether: no entry in the library, and the file gone from the ingest folder. The log even said `Successfully processed` on its way past, so nothing suggested a book had gone missing until you noticed it wasn't there. A failed conversion says nothing about whether the file is a readable book, and the two settings that decline to convert — auto-convert switched off, or the format on your do-not-convert list — already import the original, so this was inconsistent as well as lossy. The original is now imported whenever a conversion fails.

  A long conversion needed a second fix to get there. Nothing inside the app limited how long a conversion could run; the only limit was the watchdog wrapped around the whole ingest step, and when that fired it killed the ingest outright, so none of the recovery above could happen — which is exactly what the reported 63MB PDF hit after 45 minutes. Conversions now have their own time limit set just inside the watchdog's, so running long ends as a normal failure and the book is imported in its original format instead of disappearing. Raising **Ingest Timeout** in CWA Settings raises both.

  Two smaller things from the same report. The log now names the folder it keeps the copy in, `/config/processed_books/failed/`, where before it said only "failed backup" — a copy existed, but not where. And when a conversion overran, the service claimed the app "should have timed out internally", which was misleading advice about a limit that did not exist; it now points at the setting that does control it. Reported by [@auspex](https://github.com/auspex) ([#1094](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1094)).

- **Opening your library no longer runs the same search twice.** Every visit to the library asked the server for the first page of books, then immediately asked for it again at a different size and threw the first answer away. Nothing looked wrong — the wasted answer never reached the screen — but on a large library that first page is the slowest query on it, and it was being run twice on every load, for every reader. The grid now waits until it knows how many columns it has before asking, so the page is fetched once ([#1144](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1144)).

- **Book pages no longer scroll sideways on a phone when a book carries long subject tags.** Libraries catalogued with Library-of-Congress subject headings — "France -- History -- Revolution, 1789-1799 -- Fiction" and the like — carry tags wider than a phone screen, and the tag row would not break one onto a second line, so the whole book page could be dragged sideways. It hit anyone reading without an editor account, guests included, because the editing view already wrapped its tags. Long tags now wrap ([#1170](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1170)).

- **The series line on a book cover is dark enough to read again.** The small series text under a title on each cover was styled with the muted grey the rest of the interface uses, and then faded a second time on top of that. The two together took it to a contrast of about 4:1 against the card, below the 4.5:1 that small text needs to stay legible, which made it hard to read for anyone whose eyesight or screen is less than ideal. The second fade is gone, so the line is the muted grey it was meant to be ([#1135](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1135)).

## [v4.1.21] - 2026-07-25

### Added

- **The reading-progress export now includes each book's identifiers, so an external tracker can match on ISBN instead of guessing from the title.** `GET /kosync/export` previously handed out only title and author, which is ambiguous for reissues, translations and common titles, and left the receiving service to guess. Every exported book now carries an `identifiers` map holding whatever Calibre has for it (ISBN, Goodreads, Amazon, and any custom types), or an empty map when the book has none. Contributed by [@Kyraminol](https://github.com/Kyraminol) ([#1092](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1092)).

### Fixed

- **Uploading a new cover now shows the new cover.** Replacing a book's cover from the edit page appeared to do nothing — the image on the left kept showing the old one, so it looked like the upload had failed even though it had worked. The cover lives at a fixed address, so the browser kept serving the copy it already had. The preview now updates the moment the upload finishes. Reported by [@chloeroform](https://github.com/new-usemame/Calibre-Web-NextGen/issues/989) ([#989](https://github.com/new-usemame/Calibre-Web-NextGen/issues/989)).

- **On a phone, the Edit button no longer sits on top of "Read now" on a book cover.** The label was clipped to "Read no…" because the pencil overlapped its tail — 52 pixels of it, on both phone and tablet widths. Both controls are permanently visible on a touch screen, so this was what every phone user saw rather than a hover quirk. The label now keeps clear of the button at every width, and cards you can't edit still centre it as before. Reported by [@Andrew-H2O](https://github.com/Andrew-H2O) ([#1112](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1112)).
- **Search engines are now told not to index your library.** `robots.txt` pointed at a file that had never existed, so every install answered "not found" — inherited from upstream Calibre-Web, where it is also missing. A crawler that gets no answer falls back to its own rules and indexes whatever it can reach, which on a server with guest browsing enabled is your catalogue, and by extension what the people using it read. The server now returns a policy that asks crawlers to stay out. If you deliberately want your library indexed, put your own `robots.txt` next to `app.db` in the config folder and it will be used instead — no rebuild, and it survives upgrades ([#1104](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1104)).

- **Books you email with "Send to Reader" now sync your reading position.** If you send books to your e-reader by email rather than downloading them, KOReader would push your progress and the server would quietly drop it — the position was saved but never attached to the book, so the library showed nothing and the log said `No book found for checksum`. The same book fetched over OPDS synced fine, which made it look like a device problem. The cause was on our side: the server recognises a book by fingerprinting the exact file it hands out, and it fingerprinted downloads but never the copy it emailed — which, with metadata embedding on, is a different file. Emailed books are now registered the same way downloads are, under both content and filename matching. Reported by [@uschi1](https://github.com/new-usemame/Calibre-Web-NextGen/issues/627) ([#627](https://github.com/new-usemame/Calibre-Web-NextGen/issues/627)).
- **The quick tag box on a book page now suggests tags you already use.** Adding a tag from the book page offered no suggestions, so it was easy to type "sci-fi" once and "Sci-Fi" the next time and end up with two tags meaning the same thing — which is exactly what two people asked for after the full editor got suggestions and this field didn't. It now offers matching tags as you type, skips the ones the book already has, and still lets you type something new. Arrow keys move through the list, Enter picks, Escape closes. Reported by [@magdalar](https://github.com/new-usemame/Calibre-Web-NextGen/issues/741) ([#741](https://github.com/new-usemame/Calibre-Web-NextGen/issues/741), [#572](https://github.com/new-usemame/Calibre-Web-NextGen/issues/572)).

- **When KOReader says "Server push failed", the server now records why.** Highlight sync gives the reader one message and no detail, and the server was writing nothing at all about it — a rejected push, a book it couldn't match, and a highlight it declined to store all left the log completely empty, so there was no way to tell them apart from the outside. Two of those cases even answered "success", which meant the reader reported the sync as fine while nothing had been saved. Every highlight sync now leaves a line naming the book and what happened to it — how many highlights were stored, deleted or dropped, and for a refusal, which field was wrong. Anyone chasing a highlight-sync problem can now get the answer from `docker logs`. Reported by [@iroQuai](https://github.com/iroQuai) ([#920](https://github.com/new-usemame/Calibre-Web-NextGen/issues/920)).

- **Guests can open and read books again.** On a server with anonymous browsing enabled, a visitor who clicked "Read" got an error page in the classic view and was bounced back to the home page in the new interface — no book, no explanation. Three separate faults stacked up on that one click: the classic reader crashed before it could render, the new interface mistook "this visitor is a guest" for "this visitor's session expired" and signed them out, and the reader then waited forever for personal settings a guest never has. All three are fixed, and a guest now reads with the default appearance while signed-in readers keep their saved theme, font and position. Reported by [@bentsea](https://github.com/bentsea) ([#1074](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1074)).

- **Author names in the reading-progress export no longer come out with a stray `|` where a comma belongs.** An author stored in Calibre as "William H. Keith, Jr." was exported as "William H. Keith| Jr.", because Calibre escapes a comma inside a single author name and `GET /kosync/export` handed the stored form out unchanged. Any service ingesting the export was matching against a name no catalogue lists. The rest of the app already un-escaped it; the export now does too.

- **Firefox no longer draws permanent scrollbars down the side of the page.** Firefox 153 changed how it treats one of the scrollbar rules the app was using, and the effect landed the day people updated: bars that used to fade away when you stopped scrolling became fixed, always-on, and squeezed the content beside them. In the new interface that hit the sidebar and the Discover row; the classic theme had the same thing on both columns of every page. Both are fixed — the app now describes scrollbars using the standard properties, so Firefox goes back to its own fading scrollbars, and the slim dark styling is unchanged in other browsers. Reported by [@chloeroform](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1089) ([#1089](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1089)).

- **Adding the library to an iPhone or iPad home screen now gives you the app icon instead of a screenshot of the page.** Every page offered iOS an `.ico` file as its home-screen icon, and described it as a size that file does not contain. iOS cannot use an `.ico` there, so it gave up and used a thumbnail of whatever page you happened to be on. The correctly sized icon had been sitting in the app unused the whole time; every page now points at it, and the same fix applies to the new interface and to servers running behind a reverse proxy on a sub-path.

### Removed

- **The two leftover app icons that nothing used are gone.** `cps/static/icon.png` and `icon.svg` were the icons used before `favicon.ico` replaced them, and nothing had pointed at them since. They were still being shipped in every release, so every download carried about 29 KB of files no browser ever asked for. Reported by [@chloeroform](https://github.com/chloeroform) ([#1095](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1095)).

### Changed

- **The Russian interface is now complete — the last two English strings are translated.** The "Support Calibre-Web NextGen" link added to the user menu in v4.1.20 was the only text left untranslated in Russian, so it showed in English next to an otherwise fully Russian menu. Russian is now the only language in the app with every string translated and nothing falling back to English. Contributed by [@standhaftsohnsergius](https://github.com/standhaftsohnsergius) ([#1088](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1088)).

## [v4.1.20] - 2026-07-24

### Added
- **The classic interface now has a way to support the project.** There was no link anywhere in the classic UI — only the new interface had one — so anyone who wanted to chip in had to go looking for it. There is now a "Support Calibre-Web NextGen" entry in the user menu, in both the default and caliBlur themes.

### Fixed

- **The new UI no longer signs you out when a request doesn't make it through.** Clicking between shelves could spin and then drop you on the sign-in page, and signing back in didn't help for long — "remember me" included. The new UI treated any request that failed to reach the server as proof your session had expired, and it responded by ending the session for real, so one dropped request on a busy or slow server logged you out for good. It now checks whether you're actually still signed in before doing anything, and a request that simply didn't get through is reported as an error instead. Sessions that genuinely have expired still return you to the sign-in page as before. Reported by [@mrfearless](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1067) ([#1067](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1067)).
- **The edit page no longer jumps around when you rate a book.** On the new UI's edit page, the Languages field was 8 pixels wider while a book was unrated and snapped back the moment you clicked a star, so the row visibly shifted every time the rating changed. The stars now take the same space in both states. Reported by [@KucharczykL](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1064) ([#1064](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1064)).
- **Star ratings can be set again on the new UI's edit page.** Clicking a star did nothing on an unrated book, and wiped the rating on a book that already had one — so there was no way to rate a book from the editor at all. Clicking now sets the rating you aimed at (half-stars included), and it saves with the rest of the form. Reported by [@KucharczykL](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1061) ([#1061](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1061)).
- **Faster startup on large libraries.** On a big library the container could sit for several minutes on boot before the app came up — the startup step that locates your library and settings database scanned every file under `/config` and `/calibre-library` on each start. It now checks the usual location first and only falls back to a full scan when a database isn't where it's expected, so most installs boot in seconds. Diagnosed and first patched by [@chloeroform](https://github.com/chloeroform) ([#1022](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1022), [#1075](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1075)).

### Changed

- **The new UI now reads fully in Russian.** The duplicate-scan controls added in v4.1.19 and the annotation-sync task labels had no Russian translation yet, so Russian users saw those in English. They are now translated, along with a spelling correction in the Kobo delete warning. Contributed by [@standhaftsohnsergius](https://github.com/standhaftsohnsergius) ([#1058](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1058)).
- **Brazilian Portuguese now covers the new UI.** Large parts of the new interface — the cover picker, smart shelves, search filters and the sign-in screen — had no Brazilian Portuguese translation and fell back to English. 106 of those strings are now translated, and the smart-shelf button reads "Criar estante inteligente" so it matches the word used for shelves everywhere else. Contributed by [@pedronora](https://github.com/pedronora) ([#1072](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1072)).

## [v4.1.19] - 2026-07-21

### Changed

- **The new UI now reads in Brazilian Portuguese.** Around 120 strings added in recent releases had no pt-BR translation yet, so Brazilian Portuguese users saw them in English — reading progress, the cover picker, the account and profile actions, and most of the task and error messages. All of them are now translated, along with a handful of corrections to existing wording. Contributed by [@pedronora](https://github.com/pedronora) ([#1021](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1021)).

- **The container no longer re-installs Calibre on every start.** Each boot ran a Calibre installation step, on a container that already had Calibre in it — about 12 seconds of a one-minute startup on the hardware where this was reported, roughly two on a fast machine. The binaries did ship in the image; what was missing were the shortcuts that let the startup check find them, so the check failed and the install ran again. Those shortcuts are now built into the image, the check passes, and the step finishes in well under a second. Nothing about how Calibre itself works changes, and the step still repairs an image that turns up without them. Reported and originally patched by [@chloeroform](https://github.com/chloeroform) ([#875](https://github.com/new-usemame/Calibre-Web-NextGen/issues/875), [#1014](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1014)).

### Added

- **Smart shelves can be marked for Kobo sync from the new UI.** Ordinary shelves have had an "Enable Kobo sync" button on the shelf page since the new UI arrived; smart shelves did not, so the only way to set it was to open the rule editor and re-save the whole shelf. The button is now on the smart-shelf page too, next to Edit and Duplicate. It appears when your server has Kobo sync on and an admin has enabled "Sync Magic Shelves to Kobo", so it never shows up as a control that cannot do anything. Reported by [@auspex](https://github.com/auspex) ([#867](https://github.com/new-usemame/Calibre-Web-NextGen/issues/867), [#870](https://github.com/new-usemame/Calibre-Web-NextGen/issues/870)).

### Fixed
- **You can run a duplicate scan from the new UI again, and the Admin panel's duplicate entry now opens the settings.** The new interface had no way to start a duplicate scan at all: the button lives on the classic duplicates page, which the new one replaces, and the empty "a full scan is needed" notice told you to run it from CWA settings — a page that has no such button. The Duplicates page now has its own "Scan for duplicates" button that starts the scan in the background and tells you when it's queued. Separately, the Admin panel's "Duplicate Books" row opened the same page as the sidebar link, so it did nothing new; it now opens the duplicate-detection settings, where the matching rules, scan schedule and automatic-resolution options are. Reported by [@auspex](https://github.com/auspex) ([#1048](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1048)).

- **Anonymous browsing works again in the new UI.** With "Enable Anonymous Browsing" turned on, visitors were still stopped by a sign-in screen and could not reach the library at all — the classic view let them straight in, so the only way round it was to switch back. The new UI asked the server who you were, the server refused to answer for a guest, and the UI took that as "not allowed in" even though every other request would have been served. Guests now reach the library, and the account menu offers them a Sign in link instead of a sign-out and an account page they could not open. Sites without anonymous browsing enabled are unaffected and still require a login. Reported by [@bentsea](https://github.com/bentsea) ([#1023](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1023), [#1045](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1045)).

- **Library sorting no longer merges distinct letters in Russian, Ukrainian, Greek and Japanese libraries.** A recent diacritic fix stripped accent marks from every alphabet, so Й and И, Ї and І, ά and α, and が and か each collapsed into a single letter in the book list, the A–Z filter and OPDS feeds. Accent folding now applies only to Latin text. ([#521](https://github.com/new-usemame/Calibre-Web-NextGen/issues/521))

- **Smart-shelf pages now warn you when a Kobo-sync mark isn't doing anything.** Marking a shelf for Kobo sync has no effect while your account is still set to sync your whole library to the device. Ordinary shelves already explained this; smart shelves didn't. The same note, and its one-click "Sync only my selected shelves" button, now appear on the smart-shelf page too. Originally reported by [@auspex](https://github.com/auspex) ([#866](https://github.com/new-usemame/Calibre-Web-NextGen/issues/866)).

- **The web interface no longer checks for duplicate books every 2.5 seconds, all day, on every open tab.** Any page you left open kept asking the server whether duplicates had been found — around 1,400 requests an hour per tab, on a library where nothing was happening. It filled up reverse-proxy logs and did needless work on the server. The check now runs when a page loads, when you come back to a tab, and while a duplicate scan is actually running (backing off as the scan goes on) — and stops as soon as there is nothing left to wait for. The duplicates badge and notification still appear on their own when a scan finishes. Reported by [@blurrycontour](https://github.com/blurrycontour) ([crocodilestick/Calibre-Web-Automated#1288](https://github.com/crocodilestick/Calibre-Web-Automated/issues/1288)), with a patch proposed by [@budget-coder](https://github.com/budget-coder) ([#1018](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1018)).

- **KOReader highlight syncs no longer say "Server push failed", and the whole server no longer freezes while a highlight is sent to Hardcover.** With "Sync Kobo annotations to Hardcover" turned on, every highlight in a book was sent to Hardcover one at a time while your device waited, and the server stopped answering *anything* — other people's page loads included — for about ten seconds per highlight. A book with a few highlights blew past the reader plugin's own time limit, so the device reported a failure for a sync the server had actually saved, and long freezes could make the container's own health check give up and restart it. Highlights are now saved and confirmed to your device immediately, and the Hardcover half runs in the background, where a slow or unreachable Hardcover can't hold anything up. Measured on the same machine: sending three highlights went from 30.2s to 0.13s, and an unrelated page load during that sync from 29.0s to 0.01s. Reported by [@iroQuai](https://github.com/iroQuai) ([#920](https://github.com/new-usemame/Calibre-Web-NextGen/issues/920), [#699](https://github.com/new-usemame/Calibre-Web-NextGen/issues/699)).

- **Kobo sync no longer occasionally un-downloads books from a magic shelf and takes your highlights with them.** If you sync only selected shelves and one of them is a magic shelf, a book you already had on the device could sometimes come back with the download arrow on it; re-downloading it lost the annotations and highlights you had made. The sync was working out which books belong to a magic shelf by loading each book's full record, and if the library database happened to be mid-rewrite at that moment — which the automatic ingest does routinely — a few books quietly failed to load and were treated as "no longer on the shelf", so they were archived off the device. Shelf membership is now read from the shelf's own book list, which does not depend on the library database being readable at that instant. Reported by [@TheDarkSpock](https://github.com/TheDarkSpock) and [@bigbold1023](https://github.com/bigbold1023) ([crocodilestick/Calibre-Web-Automated#1307](https://github.com/crocodilestick/Calibre-Web-Automated/issues/1307)).

## [v4.1.18] - 2026-07-20

### Added

- **Export all your KOReader reading progress as JSON.** A new read-only endpoint, `GET /kosync/export`, returns every book you have reading progress for — Calibre book id, title, authors, percentage, and when you started and last updated it — so you can feed your progress into another service (for example a unified media tracker). It authenticates the same way as the other KOReader sync endpoints (HTTP Basic, app passwords supported) and only ever returns your own progress, limited to books you're allowed to see. Example: `curl -u 'user:APP_PASSWORD' https://your-instance/kosync/export`. Contributed by @Kyraminol (#978).

### Changed

- **The new UI is fully translated into Russian again.** A dozen strings added in recent releases had no Russian yet, so Russian users saw them in English — among them the account and Kobo shelf-sync settings, the format picker and converter, and the "Something went wrong" error screen. All of them now read in Russian. Contributed by [@standhaftsohnsergius](https://github.com/standhaftsohnsergius) ([#1012](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1012)).

- **Startup logs now show where the time goes.** If your container takes a long time to come up, `docker logs --timestamps <container>` used to show long unexplained gaps, because two of the startup steps never said when they began — you couldn't tell whether a step was running slowly or hadn't started yet. The library-mount step and the web server now both announce themselves as they start, so every second of boot is attributable to a named step. Reported and originally patched by [@chloeroform](https://github.com/chloeroform) ([#1002](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1002)), while investigating slow startup for [#868](https://github.com/new-usemame/Calibre-Web-NextGen/issues/868).

### Fixed

- **Marking a shelf for Kobo sync now actually works — and the new UI tells you what else it needs.** Turning on "Kobo sync" for a shelf does nothing until your account is also set to sync only selected shelves; until now nothing said so, so a device would keep paging through the entire library instead of settling on the shelf. The shelf page now says it right where you make the mark, with a one-click switch and a note that books off the shelf leave the device but stay in your library. Separately, turning that account setting on used to leave your reader syncing everything anyway — the new UI skipped the step that tells the device to drop the shelves you are no longer syncing, and the step itself worked against the sync rather than with it: it quietly removed the server-side record of what your device already had, which is the only thing the next sync uses to tell the reader to let those books go. So the books stayed on the device permanently, and were hidden from your library here into the bargain. The next sync after you turn the setting on now removes exactly the books that are not on a shelf you selected, and leaves everything else — on the device and in your library — alone. Reported by [@auspex](https://github.com/auspex) ([#866](https://github.com/new-usemame/Calibre-Web-NextGen/issues/866)).

- **Switching a user to "sync only selected shelves" from the admin user list now takes effect properly.** Admins can flip that Kobo setting from the column in the user table as well as from the user's own settings page, but only the settings page told the reader to drop the shelves it should no longer be syncing — set from the table, the device kept every collection until someone toggled it again from the other screen. Both routes now do the same thing, whether you change one user or select several at once. Saving from the user table also tells you when a save fails instead of reporting success and quietly discarding it. Originally patched by [@Sanjays2402](https://github.com/Sanjays2402) ([#1010](https://github.com/new-usemame/Calibre-Web-NextGen/pull/1010)), following on from [#866](https://github.com/new-usemame/Calibre-Web-NextGen/issues/866).

- **A page that hits an error no longer blanks the whole new UI.** If anything went wrong while a page was drawing — a smart shelf that would not open, or a reader whose files could not be fetched after an upgrade — the new UI went completely blank. There was no message and no way back, so the only escape was to close and reopen the browser. Any such error is now caught and shown as a short "Something went wrong" screen with **Reload** and **Back to library** buttons, plus the technical detail if you want to include it in a bug report. Navigating away clears it. Reported by [@monimkxl-web](https://github.com/monimkxl-web) ([#855](https://github.com/new-usemame/Calibre-Web-NextGen/issues/855)).

- **Book covers no longer blend into the page.** A cover whose own artwork background happens to match your theme's background used to lose its edges and look like it was floating loose on the page. Every cover now has a thin outline in your theme's colour — in the library grid, on the book page, in the Discover and "More by this author" strips, in the table view, and in the duplicate list. Reported and originally patched by [@chloeroform](https://github.com/chloeroform) ([#987](https://github.com/new-usemame/Calibre-Web-NextGen/issues/987)).

## [v4.1.17] - 2026-07-19

### Added

- **Series name and number are back under the book covers in the new UI.** If your library is organized into series, each book card in the library, search results, and shelves now shows its series and position (for example "Dune #2") under the title and author, so you can see at a glance which series a book belongs to without opening it. The series-heavy library view already showed the position badge; this restores the series name that the classic view had. Requested by several users through the in-app feedback form ([#657](https://github.com/new-usemame/Calibre-Web-NextGen/issues/657)).

### Changed

- **Convert on the book edit page now offers a dropdown of valid target formats instead of a free-text box.** The control reads "Convert from [EPUB] to [MOBI]", the source list is limited to formats the configured converter can read, and the target list excludes the already-selected source.
- **Faster container startup, less disk churn.** On every boot the container was re-setting ownership across the entire application tree — around 1,800 files — which took anywhere from a couple of seconds to half a minute depending on your hardware, and on some storage back-ends copied every one of those files into the container's writable layer. The application code is read-only and already readable by everyone, so almost none of that work was needed. Startup now re-owns only the handful of folders the app actually writes to (metadata change logs and export temp), leaving the static code untouched ([#941](https://github.com/new-usemame/Calibre-Web-NextGen/issues/941)). Reported by @auspex and @chloeroform.

### Fixed

- **The Library remembers your sort order and read filter after a refresh.** In the new UI, changing the Library sort (for example to "Author A–Z") or the read-status filter (Unread/Read) no longer resets to the default when you reload the page — your choice is kept per browser and restored on the next visit. Series, author, and other scoped views keep their own natural ordering as before. Reported by @standhaftsohnsergius ([#640](https://github.com/new-usemame/Calibre-Web-NextGen/issues/640)).

- **“Read now” actions now form a straight bottom row across New UI book cards on iPad and other touch devices.** Short titles reserve the same two-line space as long ones, while shelf removal and quick edit are no longer hidden behind hover on touch hardware. Desktop keeps its uncluttered hover treatment, with keyboard focus revealing the actions. Thanks @Andrew-H2O (#863).

- **Dismissing the duplicate-scan setup notice no longer fails silently.** On a standard container, clicking to dismiss the one-time "run a full duplicate scan" notice returned a server error and the notice kept coming back, because the app tried to record the dismissal in a location it isn't allowed to write to. The dismissal is now stored on your config volume like other per-user settings, so it sticks and survives upgrades ([#992](https://github.com/new-usemame/Calibre-Web-NextGen/issues/992)).

## [v4.1.16] - 2026-07-17

### Added

- **You can now support CWNG development from the app** — announcements queue in the top banner, and clicking anywhere on the Ko-fi message opens Ko-fi and dismisses it; dismissals are remembered. A Support on Ko-fi link is also available in the Help menu.

### Fixed

- **GitHub releases include the KOReader sync plugin again.** The
  `cwasync.koplugin.zip` download disappeared after v4.1.11 even as three plugin
  fixes shipped, so people installing from the release page could not get the
  current plugin. v4.1.16 restores the ready-to-install archive and identifies
  the bundled plugin as version 4.1.16. Thanks to @KucharczykL for flagging the
  missing asset in [#400](https://github.com/new-usemame/Calibre-Web-NextGen/issues/400).

- **Brazilian Portuguese now covers ~150 more of the interface.** Strings across the reader, shelves, and admin screens that still showed in English — including the "New" badge — now appear in Portuguese, and four entries that displayed the wrong text are corrected ("Shelf duplicated successfully" had been showing the message for deleting users; "Read Status" now reads "Status de leitura"). Translation work by @pedronora ([#949](https://github.com/new-usemame/Calibre-Web-NextGen/pull/949)).

- **Russian is now fully translated.** The last 48 English strings — renaming a tag and its error messages, smart-shelf rule failures, page-not-found and page-load errors, and the Hardcover token-file notice — now appear in Russian, and a fuzzy entry on the Hardcover notice is confirmed. Translation update by @standhaftsohnsergius ([#970](https://github.com/new-usemame/Calibre-Web-NextGen/pull/970)).

## [v4.1.15] - 2026-07-17

### Fixed

- **KOReader stopped seeing new versions of the sync plugin.** If you update the
  plugin from inside KOReader — through Updates Manager or appstore.koplugin —
  the newest version it offered was the one from 13 July, even though three
  plugin fixes have shipped since: highlights and notes syncing into your
  library, highlight deletions syncing to the server, and a guard that stops a
  device deleting highlights it never had. The plugin releases those tools read
  had quietly stopped being published, so the fixes were in the server but never
  reached the device. The current plugin is published now, and publishing it is
  no longer a manual step that can be missed. If you install the plugin by
  downloading it from your own server's KOReader page, nothing changed for you —
  that copy was always current. Thanks to @KucharczykL for spotting it and to
  @filiporlo for #400.

- **Books with two or more authors were hard to read in the new UI.** Authors were separated
  with a comma — but an author's name can contain a comma itself ("Dumas, Alexandre"), so
  two authors came out as "Dumas, Alexandre, Maquet, Auguste" and you could not tell where
  one person ended and the next began. Authors are now separated with "&", the same way the
  classic interface, Calibre itself, and the new UI's own edit box ("Authors (separate with
  &)") have always done it — so the book page, the library grid and the table view now agree
  with the edit form instead of contradicting it. Tags, languages and publishers are
  unaffected and still use commas. Thanks to @chloeroform for the report.

- **New accounts ignored the default theme you picked in Admin.** Whichever theme an admin
  chose under Admin → Theme, some new accounts still started on Dark. Which accounts
  depended on how they signed up: people who registered themselves through the new UI got
  it wrong only on servers upgraded from an older build, while accounts created by OAuth,
  LDAP import, or an external/proxy login always got Dark no matter what you had set —
  those three still carried a hardcoded default from back when Light was removed, and were
  never updated when the six themes returned. Admin-created accounts were always fine, so
  the same setting could produce two different results on one server. All seven ways an
  account can be created now seed the theme you configured.

- **"Change cover" made the whole server unreachable until it finished.** Opening the
  cover picker on one book froze every other page for everyone using the server — up to
  about 12 seconds, however long the slowest metadata source took to answer. The same
  freeze hit the "Search metadata" button on the edit page. Measured on a test library: a
  book page that normally answers in 30ms took 11.4 seconds while a cover search ran; it
  now answers in well under a quarter second, and the cover search itself is no slower.
  Thanks to @darkmatterpelican for reporting it in #954.

- **Setting a default library view turned your library into the search page.** After saving
  a default view, the library home showed the "Advanced search" form pinned above the books,
  the page was retitled "Advanced search", and the library heading, its actions and the
  Discover strip disappeared. Your library now stays your library — it simply shows the
  books your default view selects, with a note saying so and a "Show all books" link to see
  everything again. Reported by @chloeroform.

- **Automatic duplicate resolution never ran if you set a cooldown.** Turning on the
  cooldown ("wait N minutes between automatic resolutions") stopped automatic duplicate
  resolution from running at all, and the log reported a wait of about four hours
  counting up rather than down. Two separate causes: the cooldown you typed was thrown
  away and replaced with one minute, and the clock comparison mixed your local time with
  UTC, so the wait never elapsed. If your server runs east of UTC the opposite happened —
  the cooldown was ignored and resolution ran on every scan. Both are fixed and your
  existing resolution history stays intact. Thanks to @jdbway, who diagnosed both causes
  and pinpointed the exact lines in #944.

- **Startup no longer sets permissions on your Calibre library twice.** Every container
  start walked the whole library once from a hardcoded list and again from `dirs.json`,
  and re-walked a folder inside `/config` that had already been covered. Each folder is
  now visited once, which shortens startup on large libraries. Thanks to @chloeroform for
  spotting it in the startup log and measuring it.

- **Russian screen readers announced the reader's progress as "Прочитано: 45% r"** — a
  stray letter left over from the English "read". It only ever reached people using a
  screen reader, since the text is spoken rather than drawn on screen. Brazilian
  Portuguese was already correct and is unchanged.

## [v4.1.14] - 2026-07-16

### Added

- **You can now hide books from your personal library in the new interface without deleting them or affecting anyone else.** Hide/Unhide lives beside Delete on book details, and View settings can reveal clearly marked hidden books whenever you want one back. The feature is on by default for new installations; upgrades preserve the admin's existing **Allow users to hide books** switch, which is the kill switch to check if Hide is absent.

### Fixed

- Translated entity pages now say “Show all authors/tags/…” in the signed-in
  user's language instead of leaking the English route segment, and locale-
  sensitive search labels now lowercase using the app language rather than the
  browser's language. Most-downloaded lists also remain usable for libraries
  large enough to exceed SQLite's single-query parameter limit.

- Brazilian Portuguese users now see more of the New UI in Portuguese,
  including book actions, upload feedback, favorites, and hide/archive status;
  stale catalog entries and misleading action/toast wording were corrected
  during adoption. Translation update by @pedronora (#865).

- Russian users now see the newly added New UI controls, smart-shelf date
  filters, theme choices, upload flow, and accessibility announcements in
  Russian instead of English fallback text. Translation update by
  @standhaftsohnsergius (#895), with terminology corrections during adoption.

- New UI translation updates no longer manufacture fuzzy guesses that look
  complete but disappear at runtime. Legacy SPA guesses are now an explicit
  untranslated review queue, an all-locale gate prevents fuzzy entries from
  returning, and reviewed French, Russian, German, and Hungarian navigation,
  shelf, status, error, and accessibility text now renders in those languages.
  Built-in smart-shelf names also follow the signed-in user's language without
  renaming the shelf (#879, #886).

- Hardcover setup no longer hides token status behind a disabled sync switch,
  points secret-file users at the wrong environment variable, or shows two
  conflicting enable checkboxes. One server-wide switch now controls both
  reading-progress sync and scheduled Hardcover ID fetching, existing enabled
  installations are preserved during migration, and compose deployments can
  manage it with `HARDCOVER_SYNC_ENABLED`. Startup logs report enabled/token
  presence and their sources without exposing the token. ([#897](https://github.com/new-usemame/Calibre-Web-NextGen/issues/897), [#898](https://github.com/new-usemame/Calibre-Web-NextGen/issues/898), [#899](https://github.com/new-usemame/Calibre-Web-NextGen/issues/899), [#900](https://github.com/new-usemame/Calibre-Web-NextGen/issues/900))

- **Classic catalog cards now use the same read checkbox state as book details:**
  checked means read and empty means unread, while the tooltip still names the
  action clicking will perform. Thanks @darkmatterpelican for the cache-free,
  list-versus-detail reproduction (#771).

- **Dismissing the classic-view “Try the new UI” banner now keeps it dismissed
  after updates.** It is a one-time adoption cue, not a What's New notice, so a
  previous version-specific dismissal is migrated to one durable browser choice.
  Thanks @darkmatterpelican (#907).

- **Advanced server settings now say before you click that they open in the
  classic view.** Those deep configuration pages intentionally remain in the
  proven server-rendered interface during the hybrid cutover; the New UI no
  longer makes that transition look accidental. Thanks @HLRobius (#909).

- **Series, tag, author, publisher, and language pages now put their real name
  in the browser tab.** Direct links previously captured the `…` loading state
  before the entity query finished and never refreshed it. Thanks
  @chloeroform (#892).

- **Tags can now be renamed from their New UI page.** Editors previously reached
  a read-only tag page with no rename action; the corrected name now updates the
  shared tag in the library and every linked book. Thanks @chloeroform (#914).

- **Signing in through the new interface now opens the requested page instead of showing “This page doesn't exist here.”** Password and magic-link logins honor safe same-site destinations, fall back to the library when no destination was supplied, preserve reverse-proxy subpaths, and reject links that try to send the browser to another site.

- **Syncing highlights from a second KOReader device no longer wipes the
  highlights from your first one.** Opening a book on another device could
  silently delete every highlight the other device had made, permanently and
  with no error — a later sync never brought them back. Deleting a highlight on
  a device still removes it everywhere, which is what this path is for; the
  device now says which highlights the user deleted instead of the server
  guessing from what a sync left out. Caught before release, so no published
  version ever shipped it (#920).

- **Deleting a KOReader highlight now actually syncs.** The fix released for
  this in v4.1.13 never reached the server: the plugin set the field, but its
  request spec did not list it, so it was dropped before the request was sent
  and the deleted highlight stayed in your library. Update the plugin to
  4.1.14 (Highlight sync → the plugin ships with this release) for device
  deletions to sync (#905, #906).
- Admin → Theme no longer says "Settings saved." and then changes nothing. The
  picker stored its choice in an old numbering the theme system stopped reading,
  so "Light" always came back dark. It is now the default theme for **new
  accounts**, it offers every theme (System, Light, Dark, Sepia, High contrast,
  Midnight) instead of just two, and a "Light" you saved earlier is honoured
  rather than discarded. Your own theme stays where it belongs, under Account →
  Theme. Thanks @auspex for reporting it and pushing back when the first fix
  missed the part you filed about.

- KOReader progress now appears on both classic and new book pages even when
  the book already had a read/unread record before its first matched sync. The
  devices could exchange positions while the web page showed no “KOReader
  Progress” entry because that existing-row path never created the separate
  bookmark state the pages display. This is a server-side fix; no device plugin
  update is required. Reported and carefully re-tested by @uschi1 (#627).

- **Signing out no longer drops a browser that prefers the New UI onto the
  classic login page.** The anonymous login state now honors the same durable,
  per-browser interface choice as the signed-in library, while new browsers,
  non-HTML clients, disabled-SPA instances, and reverse-proxy subpaths keep
  their existing behavior. Thanks to @iroQuai for reporting the logout gap
  after the separate #807 login-label fix. ([#908](https://github.com/new-usemame/Calibre-Web-NextGen/issues/908))

- The classic smart-shelf editor now actually offers working “In the past N
  days” and “Not in the past N days” choices for Publication Date and Date
  Added. Both editors now read the same rule schema, preventing fields and
  operators from silently drifting apart again. Reported by @Glennza1962
  ([#467](https://github.com/new-usemame/Calibre-Web-NextGen/issues/467)).
- KOReader: deleting a highlight on your device now removes it from Calibre-Web
  NextGen too. Previously the highlight stayed in the book's highlights list
  forever, however many times you synced. Reported by @iroQuai (#905). Update the
  NextGen Progress Sync plugin on your device to pick this up.

## [v4.1.13] - 2026-07-14

### Added

- **Metadata searches for English and Dutch books can now find Goodreads and bol.com results after you opt in to their clearly labeled best-effort providers.** Both are off by default, require no API key, use hard request timeouts, and leave other enabled sources working if either website blocks a request or changes its pages. ([#303](https://github.com/new-usemame/Calibre-Web-NextGen/issues/303), [#315](https://github.com/new-usemame/Calibre-Web-NextGen/issues/315))

- Platform-specific install and switch guides now cover Synology, Unraid, Portainer, TrueNAS SCALE, QNAP, Dockge, and Docker Compose, with verified first-run screenshots, safer migration guidance, and matching generated-wiki pages. Contributor documentation now also explains the supported local-development workflow and pull-request quality checks. ([#527](https://github.com/new-usemame/Calibre-Web-NextGen/issues/527), [#843](https://github.com/new-usemame/Calibre-Web-NextGen/issues/843), [#765](https://github.com/new-usemame/Calibre-Web-NextGen/issues/765))
- Book details and the sortable table now show when each book was added and last modified, restoring metadata that was only visible in the classic interface. ([#878](https://github.com/new-usemame/Calibre-Web-NextGen/issues/878))
- Libraries that need a standing filter—such as hiding comics by tag—can now save any advanced search as the account's default library view, with the choice following the user across devices and a one-click way to clear it. ([#498](https://github.com/new-usemame/Calibre-Web-NextGen/issues/498))
- KOReader highlights and notes from the open book can now sync into Calibre-Web NextGen, survive concurrent updates from multiple devices, and appear in the existing Highlights list on the book page. ([#699](https://github.com/new-usemame/Calibre-Web-NextGen/issues/699))

### Changed

- Editors can now correct a book title directly in the sortable table with keyboard-friendly Save/Cancel controls, while viewers retain a read-only table. ([#783](https://github.com/new-usemame/Calibre-Web-NextGen/issues/783))
- The new in-browser reader now keeps font family, size, margins, line height, and page theme with your account, so your preferred reading layout follows you between browsers and the classic/new interfaces; its appearance panel is touch- and keyboard-accessible on phones and desktops.
- Book grids can now load a chosen number of complete rows at any card density, Discover respects the server's random-book count, and touch-screen “Read now” actions align along the bottom of each card.

### Fixed

- The Fetch Metadata window's Keys panel now shows Hardcover as "Configured" when the token comes from the `HARDCOVER_TOKEN` environment variable or a `HARDCOVER_TOKEN_FILE` secret, instead of claiming no key was set. Searches worked the whole time — only the badge was wrong, which made a working setup look broken. ([#896](https://github.com/new-usemame/Calibre-Web-NextGen/issues/896))
- Hardcover auto-fetch now records what each run did, so the Stats & Activity page's Hardcover section shows the books processed and matched instead of staying blank. Runs still finished their work before, but every one of them logged "Error saving stats to database" and saved nothing. ([#876](https://github.com/new-usemame/Calibre-Web-NextGen/issues/876))
- Changing one classic-reader appearance control no longer erases the user's other saved reader settings.
- The classic book page's favorite star now changes immediately after a click instead of waiting for a reload, because object-shaped action responses no longer crash the shared flash-message handler. ([#880](https://github.com/new-usemame/Calibre-Web-NextGen/issues/880))
- Smart-shelf moving date windows are now available in the new interface's rule builder—not only the classic builder—with Publication Date and Date Added fields and day-based operators. ([#467](https://github.com/new-usemame/Calibre-Web-NextGen/issues/467))
- Reload metadata now reads PDF, FB2, comic, audio, EPUB, and KEPUB files instead of failing through an EPUB-only path, and applies only the details a file actually contains — a book whose file carries no title or author keeps the title and authors you curated instead of being renamed after its filename. Editors can also run it from the new book page. ([#877](https://github.com/new-usemame/Calibre-Web-NextGen/issues/877))
- Uploading a PDF now picks up the author recorded inside the file. PDFs without an XMP block — most of them — previously imported as "Unknown" even when the file said who wrote it. ([#877](https://github.com/new-usemame/Calibre-Web-NextGen/issues/877))

## [v4.1.12] - 2026-07-13

- **Newly imported books now sync KOReader progress immediately in filename-matching mode.** A book added after server startup could report “No book found” until it was downloaded once or the server restarted, leaving progress detached from the web UI and other devices. Both document identities are now registered as part of ingest. ([#509](https://github.com/new-usemame/Calibre-Web-NextGen/issues/509), [#627](https://github.com/new-usemame/Calibre-Web-NextGen/issues/627))
- **A replaced side-loaded book no longer stays duplicated in a Kobo's My Books list after the old copy is deleted.** Hard-delete sync now uses the full archive/removal response Kobo firmware honors, while preserving official Kobo-store sync responses and hiding the dead entry from Archive. ([#832](https://github.com/new-usemame/Calibre-Web-NextGen/issues/832))

### Added

- **Mobile libraries can show two, three, or four complete covers per row instead of spending the whole screen on one book.** Library View settings now offer Comfortable, Compact, and Dense layouts, remember the choice in that browser, and use more of wide desktop screens without cropping cover art. ([#835](https://github.com/new-usemame/Calibre-Web-NextGen/issues/835), [#764](https://github.com/new-usemame/Calibre-Web-NextGen/issues/764))
- **Series pages no longer force every book into the cover grid.** A keyboard- and touch-accessible grid/list switch provides a more readable alternative and remembers the choice. ([#662](https://github.com/new-usemame/Calibre-Web-NextGen/issues/662))

### Changed

- **Long tag collections no longer push a book's description several screens down on mobile.** Book details now keep the cover and useful title information together, collapse tags after the first eight behind an accessible Show all control, and show synced reading position as a semantic progress bar. ([#836](https://github.com/new-usemame/Calibre-Web-NextGen/issues/836))
- **Magic-link and SSO choices no longer stretch the login page into separate sections.** Every configured method now appears in one compact “Login with” row, including all enabled providers under their configured display names. ([#833](https://github.com/new-usemame/Calibre-Web-NextGen/issues/833))

### Fixed

- **High-resolution Amazon covers remain available when the Amazon metadata provider is turned off.** The cover picker now uses a book's stored ISBN to offer the high-resolution image independently, so unreliable Amazon metadata can stay disabled without losing the cover source. Thanks to @briffaantoine for identifying the missing configuration path. ([#304](https://github.com/new-usemame/Calibre-Web-NextGen/issues/304))
- **Russian and French no longer fall back to English across several new-interface menus.** Data-driven sidebar, Admin, filter, and sort labels now enter the translation catalogs just like directly translated text; Russian gains the remaining menu translations, French gains the library/search/sort translations reported in #615, and the classic database troubleshooting guide is now translatable. Credit: @standhaftsohnsergius (#844). Addresses [#719](https://github.com/new-usemame/Calibre-Web-NextGen/issues/719) and [#615](https://github.com/new-usemame/Calibre-Web-NextGen/issues/615).
- **A hidden Table view can be restored without switching back to the classic interface.** Customize navigation now includes the same server-backed “Show book list” setting that controls the Table link. ([#837](https://github.com/new-usemame/Calibre-Web-NextGen/issues/837))
- **The new book page shows the real imported filename again instead of losing it—or showing an internal timestamp/random staging prefix after a browser upload.** Uploads now carry the browser-selected name explicitly through ingest, and the SPA displays that stored name as “Imported as.” ([#840](https://github.com/new-usemame/Calibre-Web-NextGen/issues/840))
- **Reloading metadata now refreshes stale EPUB/PDF/other format sizes too.** The refresh rechecks every real file on disk and persists changed sizes, so a conversion or external replacement no longer leaves an obsolete size blocking Send to eReader. ([#841](https://github.com/new-usemame/Calibre-Web-NextGen/issues/841))
- **Shelf actions no longer push the page wider than a 375 px phone screen.** Rename, visibility, Kobo, reorder, and delete controls now wrap inside the shelf instead of creating horizontal scrolling.

## [v4.1.11] - 2026-07-12

### Added

- **The most-requested Light design is here, as part of a complete per-account theme system.** Open **Account → Theme** in the new interface to choose **System** (follows `prefers-color-scheme` and switches live with your device), **Light**, **Dark**, **Sepia**, **High contrast**, or **Midnight** (true-black for OLED screens). The choice applies instantly, belongs to your account rather than the whole server, and survives reloads, signing out and back in, and server restarts. Light and Dark hold across the whole SPA — library, book details, editor, Admin, dialogs, status messages, and reader chrome — with WCAG 2.2 AA contrast; High contrast goes further for low-vision reading. Thanks to @uschi1 and @auspex for pushing this to the top of the list ([#351](https://github.com/new-usemame/Calibre-Web-NextGen/issues/351), [#736](https://github.com/new-usemame/Calibre-Web-NextGen/issues/736)).
- **Admins can reset another user's password without leaving the new UI.** Eligible user cards now offer a confirmed, admin-only reset that generates the replacement on the server and emails it to the user's existing address; the browser never receives the password, non-admin/Guest/self targets are rejected, and a mail-queue failure leaves the old password usable. ([#745](https://github.com/new-usemame/Calibre-Web-NextGen/issues/745))
- **Readable books now have a one-click Read now action on their grid card.** It opens EPUB/KEPUB in the new reader and supported PDF, comic, text, and audio formats in their in-browser reader, while the main card still opens book details. The action is always visible on touch screens and fully named for keyboard and screen-reader users. ([#653](https://github.com/new-usemame/Calibre-Web-NextGen/issues/653))
- **Smart shelves can now use moving date windows such as “in the past 28 days.”** Publication Date and Date Added rules support both “In the past N days” and its inverse, so a shelf for the past four weeks or six months keeps itself current instead of freezing a date into the rule. Invalid, empty, negative, and excessively large windows are rejected safely. ([#467](https://github.com/new-usemame/Calibre-Web-NextGen/issues/467))
- **The new book page now shows Calibre custom columns such as Pages.** Every displayable custom field shown on the classic detail page is carried into the redesigned page with its correct type and formatting, while ignored fields and the configured read-status column stay private/unduplicated. Zero and “No” values are preserved instead of disappearing. ([#767](https://github.com/new-usemame/Calibre-Web-NextGen/issues/767))
- **Admins can edit the send-to-eReader email body from the new Account page.** The redesigned page already exposed the recipient and subject but omitted the classic global message-body template. Admin accounts now get the missing localized textarea; non-admin accounts cannot read or change the server-wide template. ([#834](https://github.com/new-usemame/Calibre-Web-NextGen/issues/834))
- **Basic Configuration now shows whether the active Hardcover token is present, accepted, and expiring.** A rejected token no longer requires log archaeology: the admin page distinguishes missing, valid, rejected/expired, and temporarily unverifiable states, and shows the expiry when the token provides one. ([#838](https://github.com/new-usemame/Calibre-Web-NextGen/issues/838))

### Changed

- **Upload is now a distinct Library action instead of another identical sidebar row.** Accounts allowed to add books get a clearly named, touch-sized Upload books button in the Library toolbar on desktop and mobile; direct `/upload` bookmarks still work. Admin also has one conventional home in the account menu instead of a duplicate sidebar link. ([#664](https://github.com/new-usemame/Calibre-Web-NextGen/issues/664), [#722](https://github.com/new-usemame/Calibre-Web-NextGen/issues/722))
- **Customize navigation no longer dominates the top of the sidebar.** The oversized glass capsule has become a quiet, touch-sized footer control beside the navigation it changes, while the existing keyboard/touch reorder flow, focus restoration, reset, and status announcements remain intact. ([#714](https://github.com/new-usemame/Calibre-Web-NextGen/issues/714))
- **The Library no longer shows two search boxes that do the same thing.** Simple search now lives in the top bar on desktop and its focused search row on mobile; the Library still keeps Advanced Search for richer filters, and the top-bar field stays synchronized with deep links and browser back/forward navigation. ([#723](https://github.com/new-usemame/Calibre-Web-NextGen/issues/723))
- **Books without a cover now show a clean Calibre-Web NextGen placeholder instead of the old logo card.** Coverless books used to display a generic dark logo image that looked out of place — especially on the new Light and Sepia themes. In the new interface they now get a tasteful typographic cover (the book's title and author on a card that matches your theme); the classic interface, OPDS, and Kobo get a refreshed NextGen placeholder image. Real covers are unchanged.

### Fixed

- **Expired reverse-proxy sessions now return you to the login page instead of a dead screen, and Sign out logs you out of your proxy too.** With Authelia/OIDC/oauth2-proxy in front of the app, leaving the new UI open until the login session expired showed a "Failed to fetch" error on the next action instead of bouncing you back to the login page. And Sign out only cleared the app's own session, so a reverse proxy that ends its login on a top-level `/logout` request never saw one — you could stay signed in at the proxy. Both are fixed: any expired-session response now returns you to the login page, and Sign out makes the real top-level `/logout` request the proxy can act on (the reverse-proxy sub-path is preserved). Thanks to @auspex for the report ([#824](https://github.com/new-usemame/Calibre-Web-NextGen/issues/824), [#674](https://github.com/new-usemame/Calibre-Web-NextGen/issues/674)).
- **A theme that failed to save no longer looks as though it succeeded.** If the server rejects the change, the picker, live preview, and local reload cache now all return to the account's saved theme instead of leaving an unsaved palette active.
- **Light mode now stays visually consistent in Admin, book-card actions, the cover picker, and the EPUB reader.** Native Admin dropdowns no longer fall back to square browser-default styling, controls layered over cover art use a guaranteed-contrast backing, and the reader's toolbar follows its Light/Sepia/Dark reading surface instead of always remaining dark. Addresses [#351](https://github.com/new-usemame/Calibre-Web-NextGen/issues/351).
- **Shuffling the new UI's Discover picks no longer makes the Library jump up and down.** The existing cards stay in place while a fresh random set loads, then update in one step; screen readers are also told when the shuffle starts and finishes. ([#850](https://github.com/new-usemame/Calibre-Web-NextGen/issues/850))
- **Uploading books in the new UI now uses the browser's native, keyboard-accessible file control.** Drag-and-drop and tap-to-choose share one reliable control, repeat uploads are blocked while one is pending, and queued/rejected results are announced to assistive technology. ([#654](https://github.com/new-usemame/Calibre-Web-NextGen/issues/654))
- **Accented titles and names now sort with their base letters in the library, search results, the new UI, and OPDS.** `È`/`É` entries no longer fall to the end or get stranded in separate letter buckets; composed and decomposed Unicode forms agree, Spanish `Ñ` remains a distinct letter after N, and German `ß` receives `ss` primary ordering — all without adding a native collation dependency. ([#521](https://github.com/new-usemame/Calibre-Web-NextGen/issues/521))
- **One pathological PDF download no longer freezes every other page for up to 90 seconds.** Calibre metadata exports now run in a bounded gevent-aware thread pool, so health checks and unrelated requests remain responsive while a slow export waits; excess exports immediately serve the original file instead of building a queue. Timeout and process-tree cleanup remain intact. ([#561](https://github.com/new-usemame/Calibre-Web-NextGen/issues/561))
- **Book covers show the whole cover again instead of being cropped.** In the new interface, covers whose artwork isn't a standard 2:3 shape had their edges cut off to fill the card. Covers now fit the whole image inside the card (letterboxed on a matching background), and the cover picker shows the complete artwork when you're choosing between editions — the grid layout and density are unchanged. ([#660](https://github.com/new-usemame/Calibre-Web-NextGen/issues/660))
- **Searching after editing a book now shows the corrected details.** After you changed a book's title or author in the new UI, searching could still turn up the old value. The library now refreshes its in-memory view when you save an edit, so search reflects your change right away. ([#744](https://github.com/new-usemame/Calibre-Web-NextGen/issues/744))
- **You can reach your entire library, not just the first screenful.** The new library grid loaded more books only as you scrolled, and if that automatic loading didn't kick in you were stranded on the first pages. There's now a keyboard-accessible **Load more** button that stays available whenever more books remain, so the whole library is reachable however you browse. ([#704](https://github.com/new-usemame/Calibre-Web-NextGen/issues/704))

## [v4.1.10] - 2026-07-11

### Added

- **You can delete a book from the new UI again.** The redesigned book page had no delete control, so removing a book meant switching to the classic interface. The book page now has a Delete button (shown only to accounts with the "Delete books" permission) that asks for confirmation, then removes the book and its files just like the classic view — after which you're taken back to your library with the book gone from the grid. Thanks to @Glalith121 for the report ([#803](https://github.com/new-usemame/Calibre-Web-NextGen/issues/803)).
- **The new UI's metadata-search dialog now has the per-provider on/off toggles.** You can turn individual metadata sources (like Hardcover) off and back on directly from the new editor, just like the classic view — the choice is saved to your account, so it's the same whichever interface you use. ([#677](https://github.com/new-usemame/Calibre-Web-NextGen/issues/677))
- **Book ratings can now be set with an inline five-star control in the new editor.** Click either half of a star for half-star precision, use the arrow keys to adjust in half-star steps, or clear the rating explicitly — no dropdown required. ([#779](https://github.com/new-usemame/Calibre-Web-NextGen/issues/779))
- **Authors, series, tags, publishers, and other browse pages now have a compact list view.** Use the grid/list toggle to switch from cards to full-width name-and-book-count rows; the choice is remembered in this browser, including on mobile. ([#697](https://github.com/new-usemame/Calibre-Web-NextGen/issues/697))
- **The new UI's book editor has a Publication date field again.** The redesigned editor shipped without the publication-date field the classic editor has, so the date could only be set from the classic UI. The editor now has a "Published" date input — prefilled from the book's current date, and clearable to reset it. This completes the #689 report alongside the metadata autocomplete that shipped in v4.1.9. ([#689](https://github.com/new-usemame/Calibre-Web-NextGen/issues/689))

### Fixed

- **The new UI's login page now shows your configured OIDC/SSO button label.** With OpenID Connect login set up, the classic login page showed your admin-set "Button label" (e.g. "Continue with Acme Identity"), but the new interface's login button showed the internal provider name ("generic") instead. The new UI now reads the same configured label the classic page does, so both surfaces show identical SSO button text. Thanks to @thelastblt for the report ([#807](https://github.com/new-usemame/Calibre-Web-NextGen/issues/807)).
- **Editing one book's metadata no longer floods the log with repeated "log file not found" warnings.** Saving a single metadata change could make the background cover/metadata enforcer run several times over for that one change — the first run did the work and deleted its to-do note, and every later run logged a `Log file '…' not found after 3 attempts` / `Skipping processing` warning, up to six times per save. The change detector now collapses the burst of filesystem events a single save produces into one enforcement pass (both the inotify and polling watchers share one debounce now), and the already-handled case is a single calm note instead of a stack of warnings. Thanks to @auspex for the report ([#802](https://github.com/new-usemame/Calibre-Web-NextGen/issues/802)).
- **Reading progress now carries over between the classic reader and the new UI's reader.** Turning pages in one reader and then opening the same book in the other could resume at the beginning or an old spot. Both readers now save your position to your account continuously (not just when you tap the bookmark button) and, on open, resume from the newest position the server has — so you can switch interfaces mid-book and pick up where you left off. Libraries upgraded from older versions are repaired automatically on startup, and your last-read position still restores offline. Thanks to @Glalith121 for the report ([#805](https://github.com/new-usemame/Calibre-Web-NextGen/issues/805)).
- **"Run Hardcover Auto-Fetch" now works instead of failing immediately.** Triggering the Hardcover ID auto-fetch from settings could stop right after "Found N books…" with an internal "owning session has been closed" error, because the background job set up its database connection on the wrong thread. The job now opens its connection on the thread that actually runs it, so it works through your whole library — matching books to Hardcover and queuing uncertain matches for review — without crashing. ([#821](https://github.com/new-usemame/Calibre-Web-NextGen/issues/821))
- **Automatic metadata fetching during ingest no longer crashes when Hardcover is configured.** When a book was imported through the ingest folder with automatic metadata fetching on and a Hardcover token set, the import could fail with an internal error because the ingest process read its configuration from a partial, hand-maintained list of settings — so a newer setting (here, the Hardcover token) was simply missing. The ingest process now loads the full configuration the same way the rest of the app does, closing that whole class of "missing setting" crash. ([#819](https://github.com/new-usemame/Calibre-Web-NextGen/issues/819))
- **Two people on the same server can now share one Hardcover token.** Saving a Hardcover token that another account already used could fail with an internal server error, because each token had to be unique to a single user. Sharing one token across accounts is now allowed, and existing libraries are migrated automatically on upgrade.
- **The library's default newest-first order now has regression coverage across the API and new UI.** The default uses Calibre's added/modified timestamp (not publication date), and fresh catalog/filter mounts replace stale accumulated pages before rendering. ([#753](https://github.com/new-usemame/Calibre-Web-NextGen/issues/753))
- **Four remaining Russian interface strings are now complete in the new UI.** Suggestions, library refresh, highlight removal, and returning to the new interface no longer appear untranslated or use a fuzzy mistranslation. ([#656](https://github.com/new-usemame/Calibre-Web-NextGen/issues/656))
- **KOReader sync no longer loses the furthest reading position when another device is behind.** A later push from a different device on an earlier page could overwrite the server's further position, and device-clock differences could then classify a real forward sync as backwards. The server now keeps the highest known percentage across devices and file digests, while still accepting deliberate rewinds from the same device; marking a finished book unread also clears its KOReader server position so it can be restarted. The bundled KOReader plugin uses percentage—not clock order—to decide whether a remote position is ahead. Thanks to @Glalith121 and @mueslimak3r for the detailed cross-device reports. ([#633](https://github.com/new-usemame/Calibre-Web-NextGen/issues/633))
- **The new UI's send-to-e-reader dialog now shows your saved e-reader address instead of an empty recipient field.** The recipient box was blank with only a "blank = your e-reader email" hint, so it looked like the address you'd saved in your account had been lost — even though sending still worked. The field is now prefilled with your saved address; type a different one to override it for that send, or clear it to fall back to the saved address. ([#715](https://github.com/new-usemame/Calibre-Web-NextGen/issues/715))
- **Reporting an issue from the new UI's Help menu now opens the bug-report form instead of a blank issue.** The "Report Issue on GitHub" link pointed at the blank-issue URL, so reporters landed on an empty textarea rather than the Bug report / Feature request templates defined in the repo. It now opens the issue-template chooser. Thanks to @auspex for the report ([#799](https://github.com/new-usemame/Calibre-Web-NextGen/issues/799)).
- **The edit pencil on a book card can now be opened in a new tab.** In the new UI, the hover edit pencil on a book card was a button rather than a real link, so ⌘/ctrl-click (or middle-click) didn't open the editor in a new tab the way real links do — there was no `href` for the browser to open. The pencil is now a true link: a plain click still opens the editor in place (no full page reload), and a modified click opens it in a new tab. Thanks to @chloeroform for the report ([#798](https://github.com/new-usemame/Calibre-Web-NextGen/issues/798)).
- **Hardcover metadata is fetched again when a book is auto-ingested.** After the v4.1.9 change that centralised how the Hardcover token is read, the automatic fetch that runs on ingest aborted with an internal error and skipped Hardcover — even with a `HARDCOVER_TOKEN` set — while manual "Fetch Metadata" kept working. The token is now read safely in the background ingest process, so a `HARDCOVER_TOKEN` (or `HARDCOVER_TOKEN_FILE`) in the environment is applied during ingest again. Thanks to @ghub3297 and @Glalith121 for the reports ([#819](https://github.com/new-usemame/Calibre-Web-NextGen/issues/819)).
- **Saving a cover for a PDF-only or other non-EPUB/AZW3 book no longer ends with a false enforcement error.** The metadata enforcer now preserves the successful cover save, refreshes the format-independent `metadata.opf` backup, and logs an informational note that only in-file embedding was skipped for the unsupported format (#797).
- **Author-sort mismatch warnings now name the affected book and link straight to where you fix it.** The warning previously omitted the book title/ID and pointed at an author-admin screen that doesn't exist, so there was no clear way to act on it. It now names the book and gives the direct edit link (`/admin/book/<id>`): opening that page and re-saving the book's Authors field regenerates its author sort and clears the warning (or you can correct the book in Calibre). Thanks to @auspex for the report (#801).
- **The classic book page's read checkbox now matches the book's actual state.** Unread books previously showed a checked box beside the “Mark As Read” action, while read books showed an empty box. The checkbox is now empty for unread and checked for read; its tooltip continues to describe what clicking will do (#771).
- **OPDS readers now have a dedicated “Currently Reading” feed.** The OPDS root previously offered only Read and Unread, forcing in-progress books into the broad not-finished group. Signed-in users can now open a feed containing exactly books in the canonical in-progress state, with the same visibility and selected-shelf restrictions as the rest of their OPDS catalog (#672).

## [v4.1.9] - 2026-07-11

### Added

- **The new UI now has a Refresh library button, so a manual re-scan is one click away again.** The redesigned library page shipped without any equivalent of the classic header's "Refresh Library" action, so after dropping new files into the ingest folder there was no way to trigger a scan from the new UI — the books just didn't appear until the next automatic sweep. The library toolbar now has a refresh button that starts the background scan, shows a status line while it runs, and refreshes the grid so newly-added books show up. Thanks to the reporters (#780, #665). ([#780](https://github.com/new-usemame/Calibre-Web-NextGen/issues/780), [#665](https://github.com/new-usemame/Calibre-Web-NextGen/issues/665))
- **The new UI's book editor suggests existing tags, authors, series, publishers, and languages as you type again.** When you edit a book's metadata in the redesigned interface, each of these fields now offers a dropdown of values already in your library, so a typo no longer quietly creates a near-duplicate tag (`sci-fi` vs `scifi`) or series. Pick from the list, or keep typing to enter a brand-new value — the classic editor's autocomplete is back. Thanks to @magdalar for the report. ([#741](https://github.com/new-usemame/Calibre-Web-NextGen/issues/741), [#778](https://github.com/new-usemame/Calibre-Web-NextGen/issues/778), [#689](https://github.com/new-usemame/Calibre-Web-NextGen/issues/689))

### Fixed

- **The startup log no longer prints a scary "desktop integration failed" warning.** On first container start, Calibre's installer tried to register desktop menus and MIME types — pointless in a headless server image — and printed a WARNING with a traceback that made healthy startups look broken. The standard directories the step expects now exist, so it completes silently. Thanks to @darkmatterpelican for the report ([#769](https://github.com/new-usemame/Calibre-Web-NextGen/issues/769)).
- **The `HARDCOVER_TOKEN` environment variable now works everywhere.** Setting the Hardcover API token via the environment used to be honored by some features but ignored by others — most visibly, the Fetch Metadata panel told you to "set a Hardcover API key" even though your env token worked, and the admin page gave no hint a token was active. All features now resolve the token the same way, the admin page shows a note when an environment token is in use, and you can keep the secret out of your compose file entirely with the new `HARDCOVER_TOKEN_FILE` (docker-secrets style). Thanks to @KucharczykL for the report ([#743](https://github.com/new-usemame/Calibre-Web-NextGen/issues/743)).
- **OPDS feeds now show which letter or item you drilled into.** Following up on the per-feed titles added in v4.1.8: an alphabetical sub-list now shows its letter ("Alphabetical Books (U)", "Authors (V)"), and opening a specific author, category, series, publisher, rating, file format or language names it in the feed title ("Categories: Fantasy", "Ratings: 4.5 Stars", "Languages: German"). The author/category/series letter lists — which still showed only the bare server name — are fixed too. Thanks to @chloeroform for the suggestion ([#758](https://github.com/new-usemame/Calibre-Web-NextGen/pull/758)).
- **The Admin page's configuration buttons now open in the same tab.** In the new UI, everything under "More server configuration" (Basic configuration, UI settings, Logs, Scheduled tasks, …) opened a new browser tab, piling up windows and making it look like the app had forgotten you switched to the new UI. These are in-app pages and now navigate normally. Thanks to @auspex for the report ([#738](https://github.com/new-usemame/Calibre-Web-NextGen/issues/738)).
- **Choosing the new UI now sticks.** Once you switch to the redesigned interface, the choice is remembered on that browser — opening the library, following a bookmark, or opening the page in a new tab lands you back in the new UI instead of silently dropping you into the classic view and showing the "Try the new UI" banner again. Switch back to classic (from the new UI's account menu) and that choice sticks too. The preference is per-browser, so other devices and other people on the server are unaffected. Thanks to @auspex for the report ([#739](https://github.com/new-usemame/Calibre-Web-NextGen/issues/739)).
- **New UI reader: highlights can now be removed (and recolored).** In the redesigned in-browser EPUB reader you could create a colored highlight by selecting text, but there was no way to delete one — tapping an existing highlight did nothing, so unwanted highlights piled up with no way to clear them. Tapping a highlight now opens a small menu: pick a different swatch to recolor it, or choose "Remove highlight" to delete it. Thanks to @hayvan96 for the report (#782).
- **New UI: opening a shelf no longer shows a blank screen.** On v4.1.8, clicking any shelf — a manual shelf or a smart (magic) shelf — in the new UI left the page blank, and refreshing the browser did not recover it. The main book list, authors, series and other pages were unaffected. Rolling back to v4.1.7 was the only workaround. This is fixed; shelves open and list their books again. Thanks to @mrfearless and @Gauva1n for the reports (#784).
- **Half-star ratings no longer draw a tiny star floating inside the outline.** In the new UI, a book with a half-star rating (3.5, 4.5, …) showed the fractional star as a shrunken miniature star sitting inside the empty outline on the book page. The partial star now fills cleanly from the left edge. Thanks to @KucharczykL for the report ([#776](https://github.com/new-usemame/Calibre-Web-NextGen/issues/776)).
- The new UI could keep showing outdated interface translations after you upgraded — for example the French read button reverting to English "Read now" and the "mark as read" toggle showing the wrong wording, even though the fix had already shipped. The interface-text file the new UI loads now refreshes whenever it changes, so an upgrade always shows the current translations (a hard browser refresh clears any that were already cached). ([#615](https://github.com/new-usemame/Calibre-Web-NextGen/issues/615))

### Changed

- The new UI now uses the readable System font by default for both headings and body text, instead of the bookish serif some readers found hard to read. If you prefer the old look, "Bookish Serif" is still one click away under Account → UI display/body font (it's now offered for headings too). ([#641](https://github.com/new-usemame/Calibre-Web-NextGen/issues/641))
- **German interface: 19 strings that showed in English now appear in German.** The OPDS catalog descriptions (for example "Books sorted by series" and "Popular publications from this catalog based on rating") and the duplicate-scan progress messages were untranslated, so German users saw English there while the rest of the UI was translated. Filled in from pending German translations contributed upstream. Thanks to @djalexz85 and @fucx (Calibre-Web-Automated) and @ManuelDrescher (calibre-web).
- **Ukrainian interface: 141 more strings now appear in Ukrainian.** Error messages, the metadata review queue, the cover/thumbnail cache tools and other panels that previously showed English for Ukrainian users are now translated. Filled in from pending Ukrainian translations contributed upstream. Thanks to @Demelja (Calibre-Web-Automated).

## [v4.1.8] - 2026-07-10

### Added
- **Book pages now show when you started reading and when your progress last
  synced.** If you read with Kobo or KOReader, the book page now shows both
  dates so you can see how long a book has been in progress and whether its
  reading position is current. Thanks to @Kyraminol for the contribution (#763).
- **The table view can now show a Tags column.** In the redesigned interface's
  table view, each book's tags now appear as their own column, next to Series —
  handy when you're skimming or editing metadata and want to see genres and
  subjects at a glance. Use the "Columns" button to hide it if you'd rather not.
  Thanks to @mrdynamo and the original reporter (#725).

### Fixed
- **Russian interface translation updated** with another round of corrections.
  Thanks to @standhaftsohnsergius (#740).
- **Downloads failed with a server error for apps and scripts that don't send a
  browser identifier.** Some OPDS readers, download managers, and command-line
  tools (`curl`, scripts) omit the User-Agent header. Those requests hit a
  500 error instead of the book — the download and OPDS-download endpoints
  assumed the header was always present. They now handle its absence and serve
  the file normally. Thanks to @AshayK003, who reported and fixed the same crash
  upstream (janeczku/calibre-web#3668).
- **The "duplicates found" notice no longer nags about books you've archived.**
  If you archived one book of a duplicate pair, the duplicates page correctly
  showed nothing — but the sidebar badge and the pop-up notice kept counting it,
  so clicking through led to an empty page and the notice came back on every
  refresh. The count now respects the same archived and hidden books the
  duplicates page does, so the badge and the page agree. Reported by @auspex (#737).
- **OPDS feeds now each show their own name instead of all reading as your
  library's name.** In an OPDS reader, "Read Books," "Unread Books," each shelf,
  the author and series lists, and search results all appeared with the same
  title — your instance name — so the feed list was a wall of identical entries.
  Every feed now shows "Instance - Feed Name" (a shelf shows its own name, a
  search shows the query), so readers that list feeds by title can tell them
  apart. Thanks to @chloeroform for the report (#750).
- **Parts of the new interface stayed in English even when your language was
  fully translated.** Menu items like "Table view" and "Smart shelves," and whole
  screens such as the admin settings, cover picker, advanced search, and the book
  editor, showed English in the redesigned interface while the classic view
  translated them correctly. Those strings were never being collected for
  translation, so no locale could pick them up. They are now, so they translate
  into your language as each locale's translation is filled in. Thanks to
  @standhaftsohnsergius for the detailed report (#719).
- **Author names with a comma (like "William H. Keith, Jr.") now show the comma,
  not a pipe.** In the redesigned interface, an author whose name contains a
  comma appeared under book titles as "William H. Keith| Jr." — a raw `|` where
  the comma should be. Calibre stores those commas internally as a pipe, and the
  new interface was showing the stored form instead of the display form. Book
  cards on the Library and author pages, and the book detail page, now render the
  comma correctly. Reported on Discord by neontapir (#730).
- **Automatic Hardcover matching finds the right book more often.** When
  auto-fetching Hardcover metadata, the matcher only scored the first 10 of the
  up-to-50 results the search returns, so a correct edition ranked lower down
  (Hardcover puts author-in-title hits first) could be thrown away before it was
  ever considered. It now scores the whole result set, and the manual-review
  screen's "Top N" heading matches the candidates it actually shows. Thanks to
  @Schmavery for the fix (#729).

### Changed
- **Book lists load as you scroll instead of behind a "Load more" button.** The
  Library grid, Table view, shelves, smart shelves, and advanced-search results
  now fetch and append the next page automatically as you near the bottom, so
  browsing a large library is one continuous scroll. Thanks to @kurtlieber for
  the contribution (#735).
- **Reordering your sidebar sections now feels smooth and physical.** In the
  Customize panel (the left rail's **Customize** control), dragging a section used
  to snap the other rows around with no sense of motion and could jitter or stick
  at row edges. Now the row you grab lifts and tracks your pointer while the
  others glide aside to open a gap, then it settles into its slot when you release
  — the same on mouse, touch, and pen. Keyboard reordering (focus a section's
  drag handle, then use the arrow keys) and hiding/restoring sections animate
  through the same motion, and everything falls back to instant when your system
  is set to reduce motion.

## [v4.1.7] - 2026-07-08

### Added
- **Book pages now show star ratings and more from the same author.** In the
  redesigned interface, a book's page now displays its star rating (matching the
  classic view), and — below the details — a "More by this author" row of other
  books by that author, so a book page is a place to keep browsing instead of a
  dead end. Books with no cover art or description no longer leave the page
  looking half-empty.

### Fixed
- **Admins can find the Admin page in the new interface again.** In the
  redesigned UI the Admin/Settings entry lived only in the left sidebar rail, so
  admins who looked in the account (avatar) menu — the usual home for
  "Settings/Admin" — saw only *My account*, *Back to the classic view* and *Sign
  out*, and some switched back to the classic interface because they couldn't
  find admin. The account menu now shows an **Admin** link (for admin accounts
  only) that opens the in-app admin page. Reported through the in-app feedback
  form (#659).
- **The bulk-edit toolbar no longer shows a raw code placeholder.** In the new
  interface, selecting several books and choosing merge or bulk-apply showed
  literal text like "Merge %(n)s books…" and "Apply to %(n)s books" instead of
  the actual count. Both now read correctly (e.g. "Merge 3 books…"). The same
  underlying issue was corrected on the book page's tag controls.
- **Downloading a book on an iPhone no longer strands you.** In the new
  interface, tapping a format to download it used to navigate Safari away from
  the app to a page it couldn't show — leaving iPhone users stuck until they
  force-restarted the app to get back. Downloads now open in a separate tab, so
  the app stays put and you land right back where you were. Reported by
  @Arjan61 (#716). Also applies to the download buttons on the edit-book screen
  and the annotation exports.
- **Russian translation corrected in the new interface.** The font-setting
  labels in the redesigned UI were untranslated, and a few strings showed the
  wrong text (the "System Sans-Serif" font option read «Статистика системы»).
  Russian now reads correctly throughout those settings. Contributed by
  @standhaftsohnsergius (#718).
- **Auto-adding metadata during import no longer skips the cover on some setups.**
  On libraries that store book files separately from `metadata.db` (the "split
  library" option), fetching metadata during ingest failed to save the downloaded
  cover and logged an internal error. Covers now apply correctly during import.
  Reported by @maraken (#709).
- **Marking a book "unread" now fully resets it.** After opening a book "just to
  test it", marking it unread cleared the reading percentage but the book could
  stay flagged as *Currently reading*. Unread now clears that state too, so the
  book reads as untouched everywhere. Reported by @uschi1 (#683).
- **Converting from formats that need a Calibre plugin works again.** Converting
  e.g. KFX→EPUB failed with "No plugin to handle input format" even with the
  plugin installed, because the converter wasn't looking in your Calibre plugins
  folder. It now does. Reported by @jhazan-jpg (#724).
- **Changing a book's cover now updates the cover inside the file.** Picking a new
  cover updated it in the library but downloads (and the "Currently embedded"
  preview) kept the old image. The new cover is now embedded into the book file.
  Reported by @GustavPersson (#707).
- **Removing a duplicate now tells your Kobo to drop the old copy.** When the
  duplicate-scanner replaced an older copy of a book with a newer one, the server
  never told a synced Kobo that the old copy was gone, so it could linger as a
  duplicate. The server now sends the removal to the device on its next sync.
  (Some Kobo devices may still keep a removed sideloaded book until it's archived
  on the device — we're improving that in a follow-up.) Reported by
  @Chronosmage-alt (#708).
- **Uploading a new format to a book no longer creates a duplicate on very long
  filenames.** An over-long uploaded filename could be imported as a separate book
  instead of being added as a format to the existing one. Reported by @jrhedman
  (#690).

## [v4.1.6] - 2026-07-07

### Added
- **Pick the right Hardcover edition when fetching metadata (new interface).**
  On a Hardcover search result you can now click **Editions** to drill into that
  book's individual editions (paperback, e-book, translations…) and apply the one
  you want — so the correct edition ISBN and Hardcover edition id land on your
  book, which is what Hardcover reading-progress sync needs to match the right
  copy. Every result also gets a **⋯ (View all details)** button that opens the
  full record — complete description, every tag, and each identifier on its own
  line — as a popup on desktop or a bottom sheet on mobile, so nothing is hidden
  behind the truncated preview. Requested on Discord (mgrimace, Wasabi).
- **A "What's New" page, so you can see what changed without reading a
  changelog.** The Help menu (the "?" in the top bar) now has a What's New entry
  that opens a plain-English log of recent features and fixes — newest first,
  grouped by release, each with a "Try it" link straight to the thing it
  describes. A small dot on the Help menu points it out once after an update and
  clears the moment you open it.
- **Customize your sidebar from the new interface.** A **Customize** capsule at
  the top of the left rail turns the sidebar into an editable list: drag sections
  into the order you want (for example, move **Shelves** to the top so you don't
  have to scroll) and tap the ✕ to hide the ones you don't use. Reordering works
  with the mouse, on touch, and with the keyboard, and your layout is saved to
  your account. Earlier (v4.1.4) the new UI started respecting the visibility
  settings from the classic interface; now you set both visibility and order
  without leaving the new UI. Requested by @Glennza1962 and @alva-seal (#585).

- **Choose the interface font in the new UI.** Account settings now has **UI
  body font** and **UI display font** pickers — pick System Sans-Serif, a
  bookish serif, or monospace instead of the defaults. Each option previews in
  its own font, the choice is saved to your account so it follows you across
  devices and browsers, and "Default" always returns to the theme font.
  Contributed by @kurtlieber (#701).

### Changed
- **New browser-tab icon that matches the app.** The favicon is now the amber
  book mark from the refreshed interface, on the app's dark background — so the
  tab, bookmark, and home-screen icon read as Calibre-Web NextGen instead of the
  inherited upstream icon.

### Fixed
- **Removed the stray number next to "User administration" in the new
  interface.** The admin page showed a bare, unlabeled count (e.g. "1") beside
  the title that read as a glitch rather than information. It's gone; the user
  count is already clear from the list itself. Reported by @chloeroform (#669),
  patch by @chloeroform.
- **KOReader reading position now syncs between two devices even after a book
  is re-uploaded or edited.** If one reader was ahead (say 80%) and the other
  behind (67%), the second device could refuse to jump forward — a manual pull
  just said "already synced". This happened when the two devices held slightly
  different files for the same book (a re-download after a metadata edit, a
  sideloaded copy, or a format the server didn't embed metadata into), so the
  server couldn't tell they were the same book and kept each device's position
  separate. The server now registers the fingerprint of every file it hands out
  (not only metadata-embedded downloads) and unifies a book's reading position
  across all of a book's known files, so the furthest position wins on every
  device. Reported by @Glalith121 (#633).
- **Your profile picture now shows in the new interface.** If you set a profile
  picture in the classic account settings, the new interface didn't use it — the
  account button in the top bar and the account page both showed a generic
  silhouette. Both now display your picture, and fall back to the silhouette only
  when you haven't set one. Reported by @chloeroform (#668).
- **Marking a book "unread" now clears its reading progress.** If you opened a
  book just to peek at it, it could stick at something like "0.6% read" with no
  way to reset it — the read/unread switch flipped the status but left the
  percentage behind. Marking a book unread now also resets its progress to zero
  (and clears where the in-browser reader would resume), so an unread book reads
  as untouched everywhere. Marking a book read is unchanged. Reported by
  @uschi1 (#683).
- **The new interface now hides the smart shelves you turned off.** If you
  unticked some entries under "Magic Shelves Visibility" in your account
  settings, the new UI sidebar still listed every smart shelf — the setting only
  worked in the classic view. The sidebar now honours it, so hidden smart
  shelves stay hidden in both interfaces. Reported by @chloeroform (#667).
- **Fixed a startup crash-loop on servers that had synced annotations to
  Hardcover.** If your library had ever synced highlights to Hardcover, an
  upgrade could get stuck restarting over and over, never finishing boot. A
  one-time database migration was refusing to run because it double-counted
  sync records the app had written during normal use. The migration now checks
  the right thing and completes, so the server starts normally again — no data
  is lost and no manual steps are needed. Reported by @PulsarFTW (#684).
- **The "Currently reading" badge now shows on the new-UI book page.** A book
  you're partway through on KOReader/Kobo showed the "Currently reading" marker
  on the classic book page but nothing on the new UI. The new-UI book page now
  displays the same marker — with the synced percentage when it's known — while
  unread and finished books still don't show it. Reported by @iroQuai (#634).
- **Fetch Metadata no longer shows the same cover for every volume of a
  series.** Searching for one volume of a series could return results where
  Vol.1, Vol.2, and Vol.3 all carried an identical cover — and applying
  metadata then saved that wrong cover onto the book. The cover-upgrade step
  now refuses to swap in artwork whose volume number disagrees with the
  book's title, and Kobo search results keep their ISBN so the exact-edition
  cover sources can be used in the first place. Reported by @boegill (#638).
- **Your shelves are listed under the SHELVES heading in the sidebar again.** In
  the new UI the sidebar showed a SHELVES heading with Tasks and About directly
  beneath it, while your actual shelves were pushed to the very bottom of the
  menu, off the end of the drawer. Shelves now appear right under the SHELVES
  heading, with Tasks and About moved to the bottom where they belong.
- **The "Contribute here!" link on the translation banner works again.** When
  your language is only partly translated, the banner offering to help now points
  at the wiki page that exists instead of a renamed one that returned a "page not
  found".

## [v4.1.5] - 2026-07-03

### Fixed
- **"Currently Reading" now shows the right books for libraries that use a
  Calibre "Read" column.** If your admin settings link read status to a
  custom Calibre column, the Currently Reading smart shelf listed every book
  you'd marked read and never the book you were actually partway through —
  and the "reading now" badge on a book's page never appeared, even with
  KOReader progress synced. In-progress state (which comes from KOReader and
  Kobo sync) is now read from the sync tracker regardless of the configured
  column, and finished books stay out of the shelf. "Yet to Read" also now
  counts books you've never touched, instead of only books explicitly marked
  unread. Reported by @alva-seal, seconded by @iroQuai.
- **KOReader sync now works when your reader matches books by filename.**
  KOReader's sync plugin (and apps like Crossink) can identify a book by a
  hash of its filename instead of its file contents. The server only ever
  knew the content hash, so filename-mode devices always got "no book found"
  and progress never linked up. The server now registers a filename digest
  for every book — on download and, for your existing library, automatically
  at startup. This also gives devices holding older copies of a book (from
  before an update, or side-loaded) a way back into sync without re-sending
  every file: switch the KOReader sync plugin's document-matching method to
  "filename". Reported by @natabat, seconded by @Metamatam; also relevant to
  reports from @uschi1 and @Glalith121.
- **A single problem PDF can no longer hang the whole server on download.**
  Downloading certain PDFs (UI or OPDS) triggered a metadata-embedding step
  that could hang inside Calibre's PDF writer, pinning a CPU core, eating
  memory, and leaving the request stuck until a 504. The embed step is now
  bounded (90 seconds by default, tunable with `CWA_EMBED_TIMEOUT`), the hung
  Calibre process tree is cleaned up, and the download falls back to serving
  the original file from your library — you get your book instead of a dead
  server. Send-to-eReader and Kepub conversion degrade the same way instead
  of failing. Reported by @darkmatterpelican.
- **The library's view-settings (gear) menu no longer opens offscreen on
  phones.** On narrow screens the toolbar wraps, and when the gear ended up on
  the left side its menu opened toward the left and slid off the edge of the
  screen. The menu now drops below the toolbar and stays fully visible at any
  width. Reported by @iroQuai.

## [v4.1.4] - 2026-07-02

### Added
- **Quick-edit shortcuts are back in the new UI.** Two things the old interface
  had returned: hovering a book in your library or search results now shows a
  small pencil that drops you straight into that book's edit page — no need to
  open the book first. And on a book's page you can now add or remove individual
  tags right there (each tag has an × to remove it, plus an "Add tag" box) rather
  than opening the full editor and hand-editing a long comma-separated list. Both
  only appear if you have edit permission. Larger batch-editing improvements are
  still on the way. Reported by @magdalar.
- **The new UI's sidebar now respects which sections you've turned off.** Just
  like the classic UI, if an admin (or a per-user setting) has hidden sections
  such as Hot, Top Rated, Discover, Categories, Series, Authors, Publishers,
  Languages, Ratings, Formats, Archived, Favorites, Table view, or Duplicates,
  those entries no longer appear in the new-UI sidebar — it follows your
  configured Visibility settings instead of always showing everything. Nothing
  changes if you never hid anything. Reordering sidebar entries is still on the
  list for a later update. Requested by @Glennza1962.

### Fixed
- **The new UI can now sort a series by its reading order, and shows each book's
  position.** Opening a series in the new UI listed its books newest-first with
  no way to order them 1, 2, 3, and the series position never appeared on the
  covers unless you'd baked it into the titles. A series now opens in ascending
  series order by default, the sort menu gains "Series order" (and its reverse)
  while you're inside a series, and every cover shows its number. Reported by
  @magdalar.
- **Admin config links now work behind a reverse proxy on a sub-path.** In the
  new UI, the "More server configuration" cards on the Admin page (Basic
  configuration, Database & library path, Scheduled tasks, Logs, and the rest)
  pointed at the domain root instead of inside your mount, so on a setup served
  at something like `https://host/cwa/` they broke out of the app and landed on
  a 404. They now stay inside the sub-path like the rest of the interface.
  Installs mounted at the domain root are unaffected. Reported by @chloeroform.
- **Opening a "More server configuration" page no longer throws you out of the
  new UI.** Those cards on the Admin page link to the deep, classic
  configuration screens (database path, scheduled tasks, logs, and the like).
  Clicking one used to replace the whole new interface with the old page, so it
  felt like the app had reverted to the old UI. They now open in a new browser
  tab, so the new UI stays exactly where it was and you can close the tab to
  come back. The full native rebuild of those config screens is still on the
  roadmap. Reported by @Glennza1962.
- **The new UI now shows your site's name.** If you set a custom title under
  Admin → Basic Configuration, the new UI ignored it — the top bar, the login
  screen, and the browser tab always said "Calibre-Web NextGen". All three now
  follow your configured title; installs that never changed the title look
  exactly the same as before. Reported by @Glennza1962, confirmed by @iroQuai.
- **French (and 16 other languages) no longer offer "mark as unread" on a book
  you haven't read.** The new UI's read toggle said "Marquer comme non lu"
  (mark as unread) on unread books because the translation for "Mark as read"
  carried the opposite meaning — the same copy-paste slip existed in Arabic,
  Czech, Greek, Spanish, Finnish, Galician, Indonesian, Portuguese, Slovak,
  Slovenian, Swedish, Turkish, Ukrainian, Vietnamese, and both Chinese
  variants. All 17 are fixed. The classic detail page's big read button also
  said "Lu" (has been read) in French where it meant "open the reader" — it
  now says "Lire", and the status badge keeps "Lu" where that's correct.
  Reported by @hayvan96.
- **You can log out again on mobile in the classic view.** With the caliBlur
  theme on a phone, tapping your username in the menu did nothing — an
  invisible upload control was swallowing the tap, so the account submenu
  with Logout never opened, and the drawer's profile area rendered squashed
  with overlapping text. The profile block now sits in its own space again
  and tapping your name reliably opens the menu. Reported by @iroQuai.
- **Switching shelves no longer mixes both shelves' books.** In the new UI,
  going from one shelf straight to another kept the first shelf's books on
  screen and drew the next shelf's books after them — and removing one of the
  leftover books actually removed it from the shelf you were now on. Each
  shelf (and smart shelf) now shows only its own books, and the page counter
  resets when you switch. Reported by @mstewart14.
- **Table view covers are no longer squished.** In the new UI's Table view,
  cover thumbnails rendered as narrow 32px slivers that cropped the sides off
  the artwork. They now display at a proper book-cover shape (48×72), and on
  desktop a title made of one long unbreakable string (common for auto-ingested
  filenames) wraps inside its cell instead of forcing the whole table to scroll
  sideways. Reported by @blahblah57.
- **The library now keeps your scroll position when you scroll the first page
  and go Back from a book.** The scroll-restore added in v4.1.1 worked once you'd
  loaded more pages, but if you only scrolled the first screen of books, opened
  one, and came back, the list jumped to the top. Reported by @KucharczykL.
- **App passwords now work with the KOReader plugin.** KOReader sync only
  accepted your main Calibre-Web password, so OAuth- or LDAP-only accounts (which
  have no local password) got "Invalid password" and a 401. KOReader progress and
  annotation sync now accept per-user app passwords, the same as OPDS already
  does. Reported by @alva-seal.

## [v4.1.3] - 2026-07-01

Corrective release: if you're on v4.1.1 or v4.1.2, update — those versions show
a stuck popup over the classic view.

### Fixed
- **The classic view no longer shows a feedback popup you can't close.** In
  v4.1.1 and v4.1.2, the optional "what made you switch back?" prompt appeared
  on every classic page — not just after switching from the new UI — and none of
  its buttons could dismiss it (on phones it didn't even fit the screen). It now
  stays hidden unless you've just switched back from the new interface, every
  button closes it, and it fits and scrolls on small screens. Reported by
  @iroQuai (#576).

## [v4.1.2] - 2026-07-01

This release carries exactly the same fixes as v4.1.1, re-published under a new
version number so the in-app "update available" prompt reaches everyone. If you
updated to v4.1.1 in the short window right after it first went out, you may have
landed on an earlier build of it; moving to v4.1.2 guarantees you're on the
corrected version. Nothing else changed — the full list of what's fixed is in the
v4.1.1 notes below.

## [v4.1.1] - 2026-07-01

### Added
- **The new-UI edit page can now edit identifiers, and you choose which fetched
  values to apply.** Editing a book in the new interface now has an Identifiers
  table — add, change or remove ISBN, ASIN/Amazon, Google, DOI and the rest — and
  when you fetch metadata from the web, each result has a "Choose fields" checklist
  so you apply just the title, cover, description, identifiers (or whatever you
  pick) instead of overwriting everything. Reported by @uschi1 (#580).
- **Switching back to the classic view now asks (optionally) what made you
  switch.** The new interface's user menu has a "Back to the classic view" item;
  when you use it, the classic page shows a short, two-step prompt — pick what
  didn't work and add a note if you like. It's completely optional and anonymous:
  no account, name, IP address, version or device info is sent or stored (it's
  sent over HTTPS and saved as just your feedback, like unmarked mail). It only
  appears right after you switch back, and won't nag you again.

### Fixed
- **The new UI's book page now shows your KOReader/Kobo reading progress.** If
  you sync progress from KOReader or a Kobo, the book page again shows "KOReader
  progress: X%" (it was only on the classic page before — the synced progress was
  never lost). Reported by @alva-seal (#587).
- **Dutch: the new UI's book buttons read correctly.** The button that opens the
  reader said "Gelezen" ("has been read") instead of a "read now" verb, and the
  already-read marker showed the English word "Read". The reader button now says
  "Nu lezen" and the marker shows "Gelezen ✓". (Under the hood the reader action
  and the read-status label are now separate strings, so this collision can't
  recur in other languages either.) Reported in #577.
- **Book identifiers are clickable links again in the new UI.** On a book's page,
  identifiers like Goodreads, StoryGraph, Hardcover, Amazon and ISBN now link out
  to the book on that site (as they did in the classic UI) instead of showing as
  plain text. Reported by @alva-seal (#582).
- **The new UI now keeps your place in the library when you go back from a book.**
  Scrolling down, opening a book, then pressing Back used to jump you to the top
  of the library (losing loaded pages) — annoying when opening several books in a
  row. It now restores your scroll position and the books you'd already loaded.
  Reported in #578.
- **The mobile menu drawer in the new UI is now solid and scrolls properly.** On
  phones, opening the navigation menu showed a see-through panel that couldn't be
  scrolled — trying to scroll it moved the page behind instead, so lower items
  (like Magic Shelves) were unreachable. The drawer now has a solid background and
  scrolls on its own. Reported in #576.
- **The new UI now shows the Calibre-Web favicon in the browser tab.** The
  redesigned interface had a blank tab icon; it now uses the same favicon as the
  classic UI (and it works behind a reverse-proxy subpath too). Reported in #574.
- **The new UI now works behind a reverse proxy with a path prefix.** If you
  serve Calibre-Web NextGen under a subpath (e.g. `https://host/cwa/` via nginx,
  Traefik or similar), the new interface showed a blank white page because its
  scripts, styles, API calls, covers and downloads were requested without the
  prefix and 404'd. Everything now honours the mount prefix automatically, so the
  new UI loads and works the same behind a subpath as at the domain root. Reported
  by @chloeroform (#571).
- **The read/unread checkmark shows again in the new UI when read status is
  linked to a Calibre column.** If you set Admin → View Configuration → "Link
  Read/Unread Status to Calibre Column" to a custom column, the new interface
  showed every book as unread (no checkmark) and the read/unread filters returned
  everything. The new UI now reads that column, so finished books get their badge
  and the Read/Unread/Discover filters work again. The built-in read status is
  unchanged. Reported by @uschi1 (#579).

## [v4.1.0] - 2026-06-30

### Changed
- **The new interface is now offered to everyone — opt in when you're ready.**
  After updating, a dismissible bar invites you to try the redesigned interface;
  your classic view stays the default until you tap "Try the new UI" (or the
  "Switch to New UI" button in the top bar). Dismiss it and it stays gone until
  the next update, when it gently reminds you again. You can still turn the new
  interface off entirely by setting `CWNG_SPA=0`. (Previously the new UI was
  hidden unless an admin opted the whole instance in.)

### Added
- **A redesigned "Change cover" screen in the new UI.** Picking a new cover now
  opens a polished page instead of the old one: your current cover with a one-tap
  **lock** (so a metadata refresh can't overwrite it), a grid of candidates from
  every source we search (plus the cover embedded in the book), and tabs to paste
  a URL or upload your own. If you use a Kobo, flip on **E-reader preview** to see
  how each candidate looks padded for your device before you choose. You can reach
  it straight from a book — hover or tap its cover and choose **Change cover** —
  or from the edit page. Keyboard- and screen-reader-friendly throughout.
- **A "Discover" shelf of random picks on your library home (new UI).** The
  redesigned library now opens with a set-apart "Discover" box — a row of random
  books from your collection to stumble onto something to read. Hit the shuffle
  button for a fresh set, dismiss it with the × in its corner, and bring it back
  any time from the new gear (View settings → "Show Discover section"). Your
  choice is remembered on that device.
- **"Remember me" and a show-password toggle are back on the new sign-in screen.**
  The redesigned login page now has the "Remember me" checkbox (on by default, so
  you stay signed in) and an eye button to reveal what you typed — matching the
  classic login.
- **Magic-link sign-in now has a polished page in the new UI.** Choosing "Log in
  with a magic link" opens a redesigned screen with the QR code, a one-tap copy of
  the verification link, a live "waiting…" indicator and an expiry countdown. Scan
  or open it on a device you're already signed in on and the waiting device logs in
  automatically. (Previously this dropped you onto the old-style page.)
- **The version number on the Admin page links to its release notes.** The
  "Calibre-Web NextGen" version in the Version Information table (Admin page) is
  now a link to that release's notes on GitHub, so you can see what changed in
  the build you're running. Dev/canary builds link to the releases list instead.
  Requested by @chloeroform.
- **Email your users straight from the admin area.** A new "Email Your Users"
  page (Admin → Email Your Users) lets you write a message and send it by email
  to everyone — or just the people you pick. Handy for announcing new books or
  server updates to the people sharing your library. It uses the same mail
  server you already set up for password resets, formats with HTML (links,
  bold) with an automatic plain-text fallback, can pull in your announcement
  banner text with one click, and has a "Send test to me" button so you can
  preview before sending. Messages send in the background — check Tasks for
  delivery. Requested by @froggybottomboys.

### Fixed
- **Uploading a book with a very long filename no longer fails.** A file whose
  name ran past the filesystem limit (~255 characters) used to fail to import
  with an unhelpful "Failed to queue for processing" message. The temporary
  staging name is now trimmed to fit (the file is renamed from its metadata on
  import anyway), so the upload succeeds. Normal filenames are untouched.
  Reported by @chloeroform (#553).
- **Bulk actions and drag-to-merge now work behind a reverse proxy on a
  sub-path.** If you run NextGen under a proxy mounted at something like
  `example.com/books/`, marking books read/unread, adding a selection to a
  shelf, deleting selected books, the cover badge toggle, and dragging one
  book onto another to merge all failed with a 404 — those requests went to
  the server root instead of your sub-path. They now use the correct path in
  every setup. Nothing changes if you don't use a sub-path proxy. Reported by
  @chloeroform.
- **The "Discover (Random Books)" row now actually appears.** Turning on "Show
  Random Books in Detail View" did nothing — a leftover theme rule hid the
  random-books row for everyone, so the "No. of Random Books to Display" setting
  had no visible effect. The row now shows as a "Discover (Random Books)" strip
  above your book list, on desktop and mobile. Reported by @chloeroform.
- **Changing the "Regular Expression for Title Sorting" now re-sorts your whole
  library right away.** After editing that setting (Admin → UI Configuration),
  the book order didn't change until you edited each book one by one — the new
  rule only applied to books you touched afterwards. Saving the setting now
  recomputes the sort order for every book immediately, the same way Calibre
  desktop does. Reported by @chloeroform.

## [v4.0.172] - 2026-06-25

### Added
- **Books you're partway through now show a "Currently reading" badge.** If you
  read on KOReader (or a Kobo) and your progress syncs back, the book used to
  look exactly like one you'd never opened — the web only marked books as read
  once you finished them. Now an in-progress book gets an amber "Currently
  reading" marker on its detail page and a badge on its cover in the grid,
  shelves, search and author pages, so synced reading progress is actually
  visible. Reported by @barukh27.

### Fixed
- **Sorting the Books List by Title no longer breaks the table.** In the "Books
  List" table view, clicking the Title, Title Sort, or Series ID column header
  produced an empty table and flooded the log with `no such column: title`
  errors — only Author sorting worked. The table now sorts correctly by every
  column. Reported by @Mr-Me-torn.

## [v4.0.171] - 2026-06-24

### Added
- **Choose what permissions new Generic OAuth users get.** Instead of every
  OAuth sign-up inheriting the one global default role, admins can now set a
  per-provider permission set (downloads, viewer, uploads, edit, delete, change
  password, edit public shelves) for accounts auto-created via Generic OAuth.
  Leaving it unconfigured keeps the existing global default, so upgrading
  changes nothing until you opt in. Existing users are untouched. Thanks to
  @lduesing.
- **Restrict Generic OAuth/OIDC login to specific identity-provider groups.**
  Admins can now require that a user belong to one of an allowed list of OAuth
  groups before an account is created or logged in, and can name the token claim
  that carries the group list (handy for Keycloak/Authentik, which often use a
  custom claim rather than `groups`). Membership is enforced before any account
  is provisioned, and turning the requirement on with an empty allow-list denies
  everyone rather than admitting all directory users. Thanks to @lduesing.

### Fixed
- **"Send to eReader" now shows the real reason it failed.** When your mail
  server rejected the recipient address, the send used to die with a confusing
  `TypeError` and hid the actual rejection. It now reports the address and the
  server's reason (e.g. `kid@home.net: 550 User unknown`) so you can fix it.
  Reported by @kurtlieber.
- **Beta (`:dev`) builds no longer nag about a "false" update.** If you run the
  beta image, the "update available" banner kept pointing at the latest *stable*
  release even though a beta build is actually ahead of it. Beta/unversioned
  builds are now recognised and don't show the banner.
- **Stacked notices no longer pile up into an unreadable blur.** When more than
  one pop-up notice showed at once — e.g. the duplicate-scan setup notice plus
  the update banner — they all floated to the same spot and rendered on top of
  each other. They now stack neatly in a column.

## [v4.0.170] - 2026-06-23

### Added
- **Update from a button instead of hunting for the right Docker command.** When
  a new version is available, the update banner and the Admin page now show an
  **Update now** button that gives you the exact one-line command for your setup
  — Docker Compose, `docker run`, Unraid, or Portainer/Synology — with one-click
  copy. A new **Automatic updates** section under Admin → NextGen Settings walks
  you through turning on truly hands-off updates with Watchtower, so new versions
  install themselves. (Admin only.) The README gains a matching "Updating" guide,
  including how to run NextGen under Podman.

### Fixed
- **The epub reader's Settings panel no longer sits flush against its edges.**
  After the recent settings redesign, the option labels were pressed against the
  left edge and the slider readouts ("150%", "0px") were clipped at the right.
  The panel now insets its content again, and the "Settings" title keeps its
  full-width bar across the top. Reported by @sambong.
- **The Duplicate Books page works again behind a reverse proxy on a sub-path.**
  Behind a proxy mounted on a sub-path, the cover placeholder kept requesting
  `generic_cover.svg` in an endless loop, and dismissing or resolving a duplicate
  group failed with "Failed to update duplicate group." Both came from page URLs
  that dropped the proxy's sub-path prefix; they now carry it. Reported by
  @chloeroform.

## [v4.0.169] - 2026-06-22

### Changed
- Simplified Chinese (`zh_Hans`): more of the interface now appears in Chinese —
  279 menu, button and message strings that previously fell back to English are
  translated. Thanks to @GSAlex.
- Spanish (`es`): 76 strings that were showing in English — or, in a few cases,
  the wrong Spanish phrase — now read correctly. This covers the duplicate-book
  tools, OAuth sign-in messages and several admin labels. Thanks to @HaruIjima-kun.

### Fixed
- **Kobo no longer re-downloads your whole magic shelf on every sync.** If you
  synced magic (smart) shelves to a Kobo, books kept dropping back to
  "Download"/"Unread" and losing your place — every sync unless you synced twice
  back-to-back. The shelf's membership cache was being re-stamped with a new
  timestamp every 30 minutes even when nothing changed, which made the sync
  re-send the entire shelf. It now only re-sends when the shelf's contents
  actually change. Reported by @Glennza1962 and @bigbold1023.
- **Right-click on an image in the epub reader now offers "Save image as"
  again.** The reader was swallowing the right-click (and Android long-press)
  menu on everything so the in-app highlight popup could be the way you select
  text — but that also blocked the browser's own menu on illustrations, so you
  couldn't save a picture. Images now get their native menu back (including the
  iOS long-press "Save Image"), while right-clicking text still opens the
  highlight popup. Reported by @sambong.
- **The epub reader's Settings panel no longer gets cut off on short browser
  windows.** On a window shorter than the panel — common on a NAS admin tab — the
  Theme row at the top and the Font, Spread and Reflow options at the bottom were
  clipped off-screen with no way to scroll to them. The panel now caps its height
  and scrolls internally at every window size, so every setting stays reachable.
  Reported by @sambong.

## [v4.0.168] - 2026-06-19

### Fixed
- **Archiving a book now updates the shelf count in the sidebar.** The badge
  next to a shelf name counted archived books even though opening the shelf
  already hid them, so the number stayed too high. It now matches what you see
  inside the shelf. Reported by @jasonxbergman.

## [v4.0.167] - 2026-06-18

### Added
- You can now **support Calibre-Web NextGen's development** directly — the
  project has its own [Ko-fi](https://ko-fi.com/calibrewebnextgen), linked from the
  README and the GitHub "Sponsor" button. (The upstream project it builds on is
  still credited and linked too.)

### Fixed
- **Hardcover metadata search no longer fails when one of your saved API tokens
  is stale.** If you have both a per-account token and a global one, search used
  the per-account token and gave up if it was expired — even when the global
  token was valid. It now tries each configured token until one is accepted, and
  trims a stray `Bearer ` prefix or whitespace from a pasted token. Thanks to
  @WasabiBurns for diagnosing the precedence.
- On a Kobo that syncs **by shelves**, a book you're currently reading no longer
  gets **removed from the device and forced to re-download** because of a
  momentary database hiccup while the server works out which books belong on
  your sync shelves. If it can't read that list reliably, the sync now leaves
  your books in place and reconciles on the next sync, instead of treating the
  failure as "this shelf is empty." Reported by @Glennza1962 and @bigbold1023.

## [v4.0.166] - 2026-06-17

### Added
- The **KOReader sync plugin can now be kept up to date with the Updates
  Manager plugin** (updatesmanager.koplugin) instead of hand-copying files onto
  your device. The plugin now reports its version where Updates Manager looks
  for it, and every release ships a ready-to-install `cwasync.koplugin.zip` on
  the GitHub release page — extract it into KOReader's `plugins` folder, or
  point Updates Manager at this repository to install updates from the KOReader
  menu. Requested by @filiporlo.
- **Tap the left or right side of the page to turn pages in the web reader.**
  The page is split down the middle — tap (or click) the right half to go
  forward, the left half to go back. Swiping left/right still works. Two
  annoyances are fixed along the way: a stray finger-wobble no longer flips the
  page, and selecting text to highlight no longer turns the page out from under
  you.
- **Your reader display settings now follow you across devices.** Theme, font,
  font size, page layout and the new text-margin setting are saved to your
  account, so a book you open on your phone looks the way you set it on your
  laptop — previously these lived only in one browser and didn't travel.
- **Adjustable text margins in the reader.** A new slider in the reader's
  Settings trims the side whitespace to fit more text per line, or widens it —
  whatever's comfortable to read.
- You can now **star your favorite books**. Tap the star on a book's page — or
  the star on its cover anywhere in the grid — to favorite it; favorited books
  show a gold star on the cover. Use the new **Favorites** entry in the sidebar
  to see just your starred books. Favorites are private to your own account.
- The **Published Date** field now accepts just a **year**. When you edit a
  book you can type `2020` (or `2020-05`) instead of clicking through the date
  picker for the full day — the missing month and day default to January 1st.
  Handy for the many books that only carry a publication year. Thanks to
  @huperaisan for the suggestion.

### Changed
- On the **main books list**, your **starred books now float to the top**, so
  your favorites are the first thing you see in the full library (the Favorites
  sidebar entry still shows them on their own). Within the starred group your
  chosen sort order still applies. Only the main list is affected — author,
  series, category and search views keep their usual order.
- **Duplicate detection catches more real duplicates.** Books that differ only
  by accents (Café vs Cafe) or punctuation (The Book! vs The Book) are now
  recognized as the same. It stays deliberately careful not to merge genuinely
  different books — "Dune" vs "Dune: Messiah" and "Volume 1" vs "Volume 2" stay
  separate — so nothing distinct gets wrongly flagged for removal.
- The **Magic Shelf editor** is easier to use on a phone. The rule builder and
  the Kobo-sync / OPDS / public option cards were being squeezed into a narrow
  strip with big wasted margins; they now use much more of the screen width, and
  each rule's field/operator/value controls stack full-width instead of
  clustering. A typo that mis-sized the rule's field dropdown is fixed too
  (helps desktop).
- In the **web reader**, long-pressing or right-clicking text no longer pops up
  the browser's own menu competing with the highlight popup — the in-app
  highlight menu is the one that shows. (On iOS Safari, Apple's built-in
  text-selection menu still appears alongside it; that one can't be switched off
  from a web page.)
- The **reader's Settings panel** has a cleaner layout — clearly labelled
  sections, live value readouts on the font-size and margin sliders, and bigger
  touch targets that fit comfortably on a phone.
- The **book details page** is easier to read, especially on phones. The big
  empty margins that boxed in the cover and info are gone — you get noticeably
  more width for the title, tags and description — and the page is tidier
  overall. The star rating now shows clean stars instead of a stray white box.
  On wider screens the cover and details sit side-by-side from 1024px up
  (previously only above 1400px), so desktops and landscape tablets use the
  width instead of stranding the cover alone in the middle.
- The book details page now has a clear **Read** button right under the cover —
  the full width of the cover — as the obvious way to start reading in your
  browser. The small "read" icon was removed from the row of action buttons so
  that row is less cluttered.

### Fixed
- Books that a download client adds by **hardlink** into a subfolder of the
  ingest directory are now picked up automatically. Apps like Readarr and
  Bookshelf (via qBittorrent) hardlink completed downloads into per-author
  subfolders; a hardlink fires only a "create" filesystem event, never the
  "close-write" the ingest watcher waited for, so those books were silently
  skipped until you moved them to the ingest root by hand. The watcher now also
  acts on a completed hardlink (a file that already has its full contents),
  while still leaving an in-progress download to finish writing before it is
  ingested. Reported by @stuhby.
- A book you're in the middle of reading no longer **disappears from the
  "Currently Reading" shelf** just because it has no language set. If you've
  picked a preferred language in your account, the shelf used to silently hide
  any in-progress book missing language metadata — which often hit PDFs while
  EPUBs (which usually carry a language) stayed visible, even though the book's
  own page still showed your reading progress. The progress shelves now ignore
  the language preference, so everything you're actually reading shows up. Your
  other library filters (hidden, archived, tag restrictions) are unaffected.
  Thanks to @chloeroform for the report.
- On a phone, tapping the **Search** box (or other text fields) no longer zooms
  the page in. iOS Safari zooms toward any field whose text is smaller than 16px;
  the inputs are now sized so that doesn't happen, while pinch-to-zoom still works
  normally.
- The cover editor's **Back** link now returns you to wherever you opened it from —
  the book's page when you tapped its cover, or the edit screen when you came from
  there — instead of always jumping to the edit screen. The **Edit metadata** screen
  also gained a clear **Back to book** link at the top, and on a phone its form is no
  longer pushed off-screen.
- On a phone, the **Edit Metadata** page now shows the book's details form first —
  you can edit the title, author and tags straight away instead of scrolling past
  the cover. On wider screens the cover and form still sit side-by-side. (Builds on
  the earlier off-screen-form fix; replaces a brittle fixed-offset layout.)

## [v4.0.165] - 2026-06-16

### Fixed
- On a desktop browser, the **Fetch Metadata** popup on the Edit Book page no
  longer runs off the bottom of the screen when a search returns a long list of
  results — the "Close" button at the bottom stays on screen. Previously the
  popup grew taller than the window and the only way to dismiss it was the small
  "X" in the corner; zooming the page out to 80% was the usual workaround.
  Reported by @sltvtr.

### Changed
- The Custom CSS and server announcement banner options moved to the **UI
  Configuration** admin page. They were previously on Basic Configuration,
  tucked inside the "Logfile Configuration" section next to the log level — an
  unintuitive spot that also disagreed with the documentation. They now sit in
  their own "Site Customization" section on the UI Configuration page. Existing
  values are preserved; nothing about how custom CSS or the banner behaves has
  changed, only where you set them. Reported by @Andrew-H2O.

## [v4.0.164] - 2026-06-15

### Fixed
- Editing a book whose author shares a name with another author after accents
  are stripped (for example "George Pólya" alongside "George Polya", or two
  Chinese names that romanize the same way) no longer fails with a database
  error. Previously any metadata change to such a book — even just adding a
  cover — was rejected. Reported on Calibre-Web by @annProg, @apetresc and
  @wnmurphy.
- On the caliBlur theme, the read-status quick-action button that appears when
  you hover a book cover now shows the right icon and tooltip the moment a page
  loads. In book lists like Read Books, search results and author pages, an
  already-read book used to show "Mark As Read" until you clicked it once;
  it now correctly shows "Mark As Unread" straight away. (Reported by @droM4X
  on #319)

## [v4.0.163] - 2026-06-14

### Fixed

- French (`fr`): the Hardcover integration labels no longer translate the service name "Hardcover" as "livres reliés" (hardback books). "Run Hardcover Auto-Fetch", "Hardcover Token Required" and "Enable Hardcover Auto-Fetch" now keep the Hardcover name so the buttons match the feature. Thanks to @Korri.
- Spanish (`es`): the "Invalid request" error now reads "Petición inválida" instead of the incorrect "Rol inválido" (Invalid role), and punctuation is tidied across 36 shared interface strings to match the source text. Thanks to @pablo-alcaniz.
- German (`de`): fixed 11 interface strings that showed the wrong text — the cover-size limit read "5-120 Minuten" (minutes) instead of "1–200 MB", "Failed to update shelves" showed the tags message, and "KOReader Sync is disabled" showed the default-login message. OIDC, logfile, email and Magic Shelves labels are corrected too. Thanks to @futurelook.

## [v4.0.162] - 2026-06-13

### Added
- You can now write your own message for the emails the server sends with a
  book. Edit Email Server Settings has a new "Email Message Body" box; whatever
  you type there replaces the default "This Email has been sent via
  Calibre-Web NextGen." on books sent to an eReader and on test emails. Write
  it in any language, add a link to your library, keep it short — leave the box
  blank to keep the original wording. (Requested by @iroQuai in #428)
- Admins can now style their instance with their own CSS. A new Custom CSS
  box under Admin → Edit UI Configuration injects your rules into every page
  as the last stylesheet, so they override the built-in themes — recolor the
  navbar, tweak spacing, adjust for your screen, all without editing source,
  and it survives upgrades because it lives in the database. The box is
  admin-only and can't accidentally break the page layout. (Issue #323 by
  @olskar)
- Magic Shelves can now filter on your Calibre custom columns. The rule
  builder lists every queryable custom column — text, numbers, yes/no,
  dates, ratings, and fixed-choice columns (which get a proper dropdown of
  their allowed values) — so shelves like "Mood is cozy" or "Page Count
  over 400" just work, including the "is empty / is not empty" operators.
  (PR #387 by @8bitgentleman)
- You can now open the same library in Calibre desktop while the server is
  running. Set `NETWORK_SHARE_MODE=true` plus the new
  `DESKTOP_COMPAT_MODE=true` and the server releases its database lock
  between web requests, so Calibre desktop opens the library instead of
  crashing or hanging; edits you make there show up in the web UI on the
  next page load. Occasional desktop use is the intent — heavy simultaneous
  use of both slows the web UI rather than corrupting anything. See the
  README's "Calibre desktop coexistence" section for trade-offs.
  (PR #386 by @8bitgentleman)

### Changed
- Loading spinners are crisp at any size and follow your theme's color. The
  old animated GIFs (admin Restart/Status dialogs, settings save flashes, the
  book reader, and the PDF viewer) rendered pixelated and ignored your theme;
  they're replaced by a smooth CSS ring that matches the theme's primary
  color, centers correctly everywhere, and slows down rather than freezing
  when your system asks for reduced motion. (PR #384 by @jbelascoain)

### Fixed
- The hover button for marking a book read/unread in the library grid now
  uses the same checkbox icons as the book page, instead of an eye symbol
  that looked like a hide/show control. The icon also tells you what the
  click will do — checkbox for "mark as read", unchecked box for "mark as
  unread" — and updates after each click. (#319 follow-up, reported by
  @droM4X)
- Turning on DEBUG logging no longer fills docker logs with repeating Magic
  Shelf messages. The "Found N total magic shelves", per-shelf "Hiding...",
  and "Filtered to N visible" lines fired on every request — an open browser
  tab meant the same block every ~3 seconds. They're now a single line that
  only appears when your shelf setup actually changes, with the hidden
  shelves named in it. (Fix by @KucharczykL in #443; reported by @SpookyUSAF
  in #445 and on CWA as #1060)
- Hardcover progress sync now survives Hardcover deleting or merging a book.
  If your book's saved Hardcover ID no longer exists ("We weren't able to
  find that book. Was it deleted?" in the logs), the sync looks up the
  book's current ID from its edition or slug and retries instead of
  giving up. When nothing can be looked up, the log now tells you the fix
  (refresh the book's metadata) instead of only the raw API error.
  (Follow-up to #433, reported by @SpookyUSAF)
- Calibre plugin and configuration loading is now reliable when you opt in
  with `CWA_CALIBRE_USER_PLUGINS=true`. The image used to set a misspelled
  environment variable (`CALIBRE_CONFIG_DIR`) that Calibre simply ignores, so
  Calibre invocations could fall back to a nonexistent home directory and
  miss plugins installed under `/config/.config/calibre/plugins`. The opt-in
  now sets Calibre's documented `CALIBRE_CONFIG_DIRECTORY` on every Calibre
  subprocess it covers (ingest, conversion, cover enforcement, metadata
  embed). Plugin loading stays off unless you opt in. (Diagnosed by
  @jasonobrien in #434)
- **LubimyCzytac.pl metadata search returned "no results" for every book.**
  The Polish catalog redesigned its site, so the provider's search and book-page
  parsing no longer matched anything — searches came back empty even though the
  site was reachable. Search now finds books again, and publisher, description,
  language, rating and publication date populate correctly on the metadata
  screen. Reported by @sltvtr (#431).
- Dropping an Adobe `.acsm` file into the ingest folder now explains what
  actually went wrong. An `.acsm` is a download ticket, not a book, so
  conversion fails — but the log only showed Calibre's cryptic "No plugin to
  handle input format: acsm" (followed by a stray "None"). The ingest log now
  spells out the two ways forward: install the ACSM Input plugin via
  `CWA_CALIBRE_USER_PLUGINS`, or fulfill the ticket in Adobe Digital Editions
  or Calibre desktop and ingest the downloaded book. Failure mode surfaced by
  @jbelascoain in #388 (#448).

## [v4.0.161] - 2026-06-12

### Fixed
- Hardcover progress sync no longer dies on books without a chosen edition.
  Reading on a KOReader/Kobo device synced progress to the library fine, but
  the push to Hardcover failed every time with `'NoneType' object has no
  attribute 'get'` — typically when the book's entry on Hardcover has no
  edition picked, or when Hardcover rejects a status change. The sync now
  handles those responses, logs Hardcover-side errors with a full traceback,
  and tells you when a book needs an edition selected on Hardcover for
  page-based progress. (#433, reported by @SpookyUSAF)
- Search now opens on phones. Tapping the search icon in the top bar did
  nothing on mobile (most visibly in Safari on iOS) — the icon was covered by
  the header bar, so the tap never reached the search box, and the box never
  appeared. Tapping the icon now opens the search field as expected. Desktop is
  unchanged. (#425, reported by @getthething)
- On phones, the book detail page no longer shows an oversized, off-center
  cover. The cover used to render wider than its column and sit left of center
  (on the caliBlur theme), pushing the description far down the page. It now
  caps to its column and centers, and the title/spacing on narrow screens are
  tightened so the description sits closer to the top. (#288, reported with a
  screenshot by @iroQuai)

## [v4.0.160] - 2026-06-10

### Security
- Closed a cross-site scripting hole in the comic (CBR/CBZ) reader. The reader
  ran your saved page bookmark through JavaScript's `eval()`, so a bookmark
  value that contained code — which any logged-in account could store for a
  comic — would execute when the reader page opened. Bookmarks are now read
  strictly as a page number.

### Fixed
- The metadata search dialog now lists providers in the order you set under
  Settings, instead of alphabetically. Whatever provider order you configure
  for automatic metadata fetching is now also the order the search popup shows
  and ranks results in, so your preferred source appears first.
- Adding several books at once to a Kobo-synced shelf now syncs them to
  Hardcover, just like adding one book does. Before, only single adds reached
  Hardcover — "add all" from search results, multi-select adds, and
  add-series-to-shelf silently skipped it. The sync now runs as a background
  task (visible under Tasks, cancellable), so adding a long series doesn't
  hold the page open on an external service — and single adds respond faster
  for the same reason.
- The experimental "SQL" duplicate-scan mode no longer produces different (and
  sometimes wrong) results than the default mode. It grouped co-authored books
  into multiple duplicate groups at once and skipped a title normalization the
  default scan applies, so the same library showed different duplicates
  depending on an admin toggle. That mode now uses the same single grouping
  engine as everything else, keeping SQL only as the fast candidate prefilter.
- Books you've hidden no longer show up in your duplicate scan. The Duplicates
  page respected your language, tag, and archive filters but not your hidden
  list, so hidden books reappeared there and could even be swept into
  duplicate auto-resolution.
- Duplicate detection now catches copies whose titles differ only in unicode
  form or spacing. A "Café" imported from a Mac (decomposed accents) and a
  "Café" typed by hand, or "The  Book" with a double space, counted as
  different books and never showed up as duplicates. All duplicate matching
  now normalizes accents and whitespace first; the duplicate index rebuilds
  itself on first scan after the update, and your existing dismissals carry
  over automatically.
- Dismissed duplicate groups stay dismissed. Adding another copy of a book or
  editing its title changed the group's internal label, so groups you had
  dismissed popped back onto the Duplicates page (and could re-enter
  auto-resolve). Dismissals are now tied to the group's stable identity and
  survive new ingests and metadata edits; existing dismissals are upgraded
  automatically the first time they match. Two different groups that happened
  to share a display title also no longer share one dismissal.
- Merging duplicate books can no longer overwrite one of the kept book's
  files. If a file with the merge target's name was already on disk (from an
  earlier partial failure or a manual edit), the merge silently copied over
  it; it now refuses that group with a clear error and leaves every file
  untouched. A merge that fails partway also cleans up after itself instead
  of leaving stray copied files or phantom format entries behind.
- Finishing a book in KOReader now marks it read on the website when you use a
  custom "read" column. If your admin set a Calibre custom column as the read
  marker (a stock option under Feature Configuration), KOReader completions
  only wrote the built-in read list, so the book page checkmark stayed empty.
  The sync now also sets the custom column — and only ever sets it: re-opening
  a finished book never silently un-reads it.
- Automatic metadata fetch now actually downloads covers. The "update cover"
  option existed but did nothing — books imported with auto-fetch on never got
  their cover updated. Covers now download through the same safe path as the
  manual editor (size limits, image checks, server-side request protections),
  respect the per-book cover lock, and in "smart application" mode only fill in
  a missing cover, never replace one you have. (#404, confirmed by @beanscg)
- Downloading a cover by URL (manual editor and auto-fetch alike) no longer
  destroys the existing cover when the server misbehaves: a redirect stub or an
  error page served with an image content-type used to get saved as the cover
  file. The download now follows redirects properly (cover CDNs like Open
  Library's redirect every image) and verifies the bytes are really an image
  before anything is overwritten.
- Shelf reorder: the giant white sort icon (a down arrow with lines) that sat on
  top of the first covers on wide screens is gone. It was a leftover decoration
  from the old list-style reorder page — the theme drew it in what used to be
  empty space, and the new cover grid now fills that space. The wider your
  browser window, the bigger the icon got. (#320, reported with screenshots by
  @SpookyUSAF — the covers themselves were already the right size; this was the
  last piece.)
- Resolving duplicate books no longer loses your highlights, notes, reading
  progress, or shelf placement. When duplicates were merged or resolved, only
  the book files moved to the kept copy — anything you'd done on the removed
  copy (annotations, read status, Kobo reading position, shelf membership)
  silently disappeared. All of it now follows the kept book, whichever
  resolve strategy you use. Deleting a book and deleting a user also clean up
  everything that belongs to them now (deleted accounts previously left their
  annotations and annotation-backup files behind).
- Deleting a book no longer risks leaving a broken "ghost" entry if something
  fails partway through. Previously the book's files were removed before the
  library database was updated, so an error in between could leave an entry that
  still shows in your library but won't open. The database is now updated first
  and the files removed last, so a failure leaves the book fully intact. (Mirrors
  the same data-safety fix already made for duplicate resolution.)
- Shelf reorder covers: the stylesheet that keeps the covers at the normal
  thumbnail size now loads from the page head, alongside every other stylesheet,
  instead of from the page body. A body-loaded stylesheet link can be dropped by
  some reverse proxies, which left the covers oversized on an otherwise-correct
  page — the case @SpookyUSAF kept hitting on caliBlur even after v4.0.158/159.
  (#320 follow-up, reported by @SpookyUSAF)
- Automatic metadata fetch (the admin "auto metadata fetch" option, off by
  default) no longer overwrites a book's correct author, ISBN, series,
  publication date or rating with a wrong match's. Previously, with auto-fetch
  on, importing a book could silently replace good metadata with a random
  foreign edition's — and the "smart application" mode that's meant to only fill
  gaps didn't actually protect those fields. Now it prefers the edition whose
  ISBN matches your book, and smart mode never overwrites a value you already
  have (it only fills what's missing). Open Library is also now part of the
  default provider order.

## [v4.0.159] – 2026-06-09

### Added
- You can now add books to a shelf right from the shelf page. A new **Add Books**
  button opens a searchable picker — type to find books in your library, tick the
  ones you want, and add them all at once. Books already on the shelf show as
  "Already on this shelf" so you can't add duplicates, and it works on phone and
  desktop. Especially handy for filling a brand-new empty shelf.

### Fixed
- Resolving duplicate books no longer risks leaving a book in a broken,
  half-deleted state if something fails partway through. Previously the files
  were removed before the library database was updated, so an error in between
  could leave a "ghost" book that still showed in your library but wouldn't open.
  The database is now updated first and the files removed last, so a failure
  leaves the book fully intact and the duplicate is simply re-resolved next time.
- Resolving duplicate books is now safe even if a duplicate scan happens to run
  at the same moment. Before, the two could collide — deleting the same book
  twice, leaving a duplicate only half-removed, or throwing a brief error that
  left the library inconsistent. Now only one resolution runs at a time and the
  other steps aside, so your books stay intact.
- Duplicate detection no longer treats books that are *missing* a title or
  author as duplicates of each other. Two unrelated books that both happen to
  have no title (or no author) used to collapse together as a "duplicate" — and
  could then be offered up for deletion. They're now kept separate; only books
  with real matching metadata are grouped.
- Resolving duplicate books is more reliable: the resolver no longer closes a
  shared database connection mid-operation, which could cause errors or a
  half-finished cleanup when the library was being used at the same time.
- The shelf reorder screen's cover-size fix now reaches more setups: the covers
  were still showing oversized for some users on v4.0.158 (e.g. behind certain
  reverse proxies). The styling moved out of the page into a regular stylesheet
  and now sizes covers on its own, so they stay at the normal thumbnail size
  regardless of theme or proxy. (#320 follow-up, reported by @SpookyUSAF)
- On phones, the menu (hamburger) button is now on the **left**, the same side
  the navigation drawer slides out from — so the button and the menu it opens
  line up. Tapping it opens the menu; tapping outside still closes it.
- On phones, the select and settings buttons above a book list now sit on the
  right (matching the desktop layout), so tapping the gear opens its menu on
  screen instead of off the left edge where it was getting cut off.
- Pages no longer occasionally fall back to the old, deprecated light theme —
  including error pages. That fallback could happen when a request hit a snag
  while loading, and it was the underlying cause of display glitches like the
  oversized shelf-reorder covers (#320). The dark theme is now enforced even on
  error pages and requests that are interrupted before they finish loading.

## [v4.0.158] – 2026-06-08

### Fixed
- The shelf reorder screen now shows covers at the normal thumbnail size
  instead of blown-up "large icon" size, and the Back button lines up under
  the covers with proper spacing above it. (#320 follow-up, reported by
  @droM4X and @SpookyUSAF)

## [v4.0.157] – 2026-06-07

### Added
- You can now add a whole series to a shelf in one click: series pages have an
  "Add Series to Shelf" button that adds every book in series order, skipping
  ones already on the shelf. (#334, requested by @Glennza1962)
- The book detail and edit pages now show the filename a book was imported
  with ("Imported as: …"). Ingest renames files to match their metadata —
  including wrong auto-matches — so the original name is the one stable
  reference for recognizing misidentified books while you fix their tags.
  Captured automatically for new imports from this version on. (#346,
  requested by @BakaPhoenix and @magdalar)

### Changed
- Rearranging a shelf now happens in the same cover grid as the regular shelf
  view — drag a cover where it belongs, on desktop or phone (long-press to
  lift), or move it with the keyboard arrows. The order saves by itself on
  every change; the old cramped list and its Save button are gone. A shelf
  that changed in another tab no longer breaks saving. (#320, requested by
  @SpookyUSAF with design input from @droM4X)
- Series pages now list books in series order by default (1, 2, 3…) instead of
  newest-first — matching what the OPDS feed always did. Choosing a different
  sort still sticks for next time.
- On phones, the menu button now looks like one: a standard hamburger icon
  replaces the round profile-head glyph, which nobody recognized as the way
  to open the sidebar. Same spot (top right), same tap target; your profile
  options are inside the menu it opens, where they always were.

### Fixed
- On phones, opening the sidebar no longer dead-ends the page: tapping
  anywhere outside the menu now closes it (it used to do nothing, and the
  menu button itself became untappable behind the overlay — the page was
  stuck until a reload).
- Fixed a rare freeze where the whole app could lock up — pages never loading
  until the container was restarted — when a background task (thumbnail
  generation, metadata backup, duplicate scan…) hit the database at the same
  moment as a page load. Database access is now coordinated so the standoff
  can't happen.
- Kobo sync no longer fails behind reverse proxies with default buffer sizes
  (Synology DSM, stock nginx). The sync token header could exceed nginx's 4K
  default when Kobo store proxying was on; it's now compressed to roughly
  half the size, with older tokens still accepted — no device reconfiguration
  needed. If you added `proxy_buffer_size` overrides for this, they can stay
  (harmless) or go. (#331, reported by @Gusdezup)
- "Reload Metadata" now also reloads authors, tags, and series (with series
  number) from the book file — previously only title, description, publisher,
  publish date, and languages came through. Author changes also rename the
  book's folder and file to match, the same way editing in the web UI does.
  A file that's missing its author or tags fields leaves your existing data
  alone instead of wiping it. (#218, reported by @yodatak)
- Adding a single book to a Kobo-synced shelf without JavaScript now syncs it
  to Hardcover the same way the normal button does.
- Bulk shelf adds no longer claim books were added when a database error
  actually rolled everything back.

## [v4.0.156] – 2026-06-06

### Fixed
- **Magic Shelves marked for Kobo sync now actually reach your Kobo** — books
  deliver and the shelf appears as a collection on the device. Previously a
  global setting (off by default) silently swallowed the per-shelf "Enable Kobo
  sync" checkbox; if you'd ever ticked that checkbox, the upgrade enables the
  global setting for you automatically. The checkbox now also tells you when the
  global setting is off instead of silently doing nothing. (#359, reported with
  excellent diagnostics by @recruiterguy)

### Security
- `POST /duplicates/invalidate-cache` now requires authentication — previously
  it accepted unauthenticated requests on internet-facing deployments (limited
  impact: it could only force a duplicate-scan refresh). (#370, found and fixed
  by @8bitgentleman)

### Added
- A `:dev` docker channel: `ghcr.io/new-usemame/calibre-web-nextgen:dev` gets
  every merge as it lands — it's what we run at home. Versioned releases now
  batch to at most one per day, so release notifications get quieter.

## [v4.0.155] – 2026-06-06

### Fixed
- Kobo sync: after a Magic Shelf cache rebuild, the per-shelf delivery cursor
  could silently revert to a stale value, leaving newly-added low-numbered books
  undelivered until the next shelf change. (#368 follow-up)

## [v4.0.154] – 2026-06-06

### Fixed
- Kobo sync: adding a book to a Magic Shelf between syncs now reliably delivers
  it — the sync cursor detects the cache rebuild and re-walks the shelf. (#367
  follow-up)

## [v4.0.153] – 2026-06-06

### Fixed
- Kobo sync: Magic Shelves with more than 100 books no longer re-send the same
  first 100 books forever — delivery now pages through the whole shelf. (#366
  follow-up)

## [v4.0.152] – 2026-06-06

### Fixed
- Kobo sync: when more than 100 books were pending at once alongside a Magic
  Shelf refresh, some regular books could be skipped permanently. Nothing is
  dropped anymore. (#361 follow-up)

## [v4.0.151] – 2026-06-06

### Fixed
- Kobo sync: Magic Shelf delivery and cache refresh now work in
  sync-entire-library mode, not just "selected shelves only" mode. (#359)

## [v4.0.150] – 2026-06-05

### Changed
- Read/unread toggle on the book detail page now shows the action you're about
  to take (checkmark = "mark as read") instead of the current state, and the
  read badge uses a consistent checkmark icon everywhere. (#319)

## [v4.0.149] – 2026-06-05

### Added
- "Reload Metadata" button on the book detail page — re-reads title, author,
  and other metadata from the book file on disk after you've changed it
  externally (e.g. in Calibre desktop). (#218, requested by @yodatak)

## [v4.0.148] – 2026-06-05

### Fixed
- Sorting the Hidden Books page no longer dumps you into the unfiltered
  library. (#319, reported by @SethMilliken)

## [v4.0.147] – 2026-06-05

### Fixed
- Kobo sync: libraries with thousands of books imported in one batch (all
  sharing one timestamp) now sync completely — previously the device could loop
  on the same batch or skip the remainder. (#347, reported by @andree392)
- Kobo sync: first delivery pass for Magic Shelf membership. (#359)

---

Older releases: see the [GitHub releases page](https://github.com/new-usemame/Calibre-Web-NextGen/releases).
