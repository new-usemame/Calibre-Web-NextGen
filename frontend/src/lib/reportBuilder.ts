/*
 * Zero-egress report builder.
 *
 * THE CONTRACT, AND THE WHOLE POINT OF THIS MODULE: the app never transmits a
 * report anywhere. It composes one locally and hands the *user* a URL they may
 * choose to open. The only network request is theirs, from their own browser,
 * to a service they already use (GitHub / Discord). No IP, no payload and no
 * fact about the instance ever reaches us unless the user personally posts it.
 *
 * That is a deliberate rejection of telemetry, not an incidental property, so
 * everything here is written to keep it true:
 *
 *  1. NOTHING IN THIS FILE PERFORMS I/O. No fetch, no XHR, no beacon, no image
 *     ping, no dynamic import. Adding one silently converts this feature into
 *     the thing it replaced. The unit tests assert the built bundle contains no
 *     transmit call, because a code review will not reliably catch a re-added
 *     one.
 *  2. DIAGNOSTICS ARE AN ALLOWLIST, NEVER A REDACTION PASS. We enumerate the
 *     handful of fields that are safe by construction and derive them from
 *     known-safe inputs. We never take a rich object and try to strip the bad
 *     parts out of it — "sanitize until it looks clean" fails open the first
 *     time an unanticipated field appears, and this payload is bound for a
 *     PUBLIC issue tracker.
 *  3. THE USER SEES THE FINAL TEXT. Everything auto-included is something we
 *     decided to publish on someone's behalf, so the composed body is rendered
 *     verbatim for review and is editable before it goes anywhere.
 *
 * Deliberately dependency-free (hard rule 6) and free of framework imports, so
 * it is callable from the ErrorBoundary — which by design reads no app data and
 * runs when the React tree above it has already died.
 */

/** Which channel a report belongs in. "it needs to be the right channel." */
export type ReportKind = 'bug' | 'feature' | 'question';

/**
 * The complete set of facts a report may carry. This type IS the privacy
 * boundary: if a field is not on here, it cannot reach a report body, and
 * adding one is a deliberate act with a review attached.
 *
 * Every field is non-identifying by construction — see the note on each. In
 * particular there is no URL, no host, no path, no username, no library or file
 * name, and no user agent string.
 */
export interface ReportContext {
  /** Our own published release string (e.g. "v4.1.31"), injected by the shell
   *  from `constants.INSTALLED_VERSION`. Identifies OUR BUILD, not the user. */
  version: string;
  /** The route SHAPE, with every dynamic segment replaced ("/book/:id"). Never
   *  the URL: a real one carries the instance host, and the ids identify the
   *  user's own library contents. */
  routePattern: string;
  /** Coarse engine + platform ("Safari on macOS"). Derived through a fixed
   *  allowlist of families, never the raw UA — a full UA string is a
   *  fingerprinting surface and pins a patch level. */
  browser: string;
  /** Theme and viewport bucket: reproduction-relevant and non-identifying. */
  theme?: string;
  /** Bucketed viewport ("narrow (<600px)"), never the exact pixel size, which
   *  is a fingerprinting vector when combined with the other fields. */
  viewport?: string;
  /** An error message, when a crash is what prompted the report.
   *  ⚠️ THE ONE FREE-TEXT FIELD, AND THE ONLY ONE NOT SAFE BY CONSTRUCTION:
   *  it originates in library code and can quote a file path, a book title or a
   *  URL. It is passed through `scrubFreeText` AND shown to the user under an
   *  explicit warning. Defence in depth precisely because neither is complete
   *  on its own. */
  errorMessage?: string;
  /** React component stack. Component names only — safe, and the single most
   *  useful field for locating a crash. */
  componentStack?: string;
}

/** Where reports go. Bugs and features to the tracker; open questions to chat,
 *  where a conversation is the right shape and an issue would be noise. */
const CHANNELS = {
  issuesNew: 'https://github.com/new-usemame/Calibre-Web-NextGen/issues/new',
  discord: 'https://discord.gg/B8NXZmcp32',
} as const;

/**
 * GitHub 404s a request whose URL is too long, and the failure mode is awful:
 * the user clicks "Report", lands on an error page, and the report is gone. Cap
 * well under the ~8 KB practical limit and truncate the body rather than
 * risking it — a slightly short report posts, a long one does not.
 */
const MAX_URL_BYTES = 6000;

/* ── Allowlisted derivations ──────────────────────────────────────────────── */

/**
 * Reduce a concrete path to its route shape.
 *
 * "/book/1234" → "/book/:id". The id is a fact about the user's library, and
 * the shape is what actually helps reproduce. Runs on a PATH ONLY — callers
 * must pass `location.pathname`, never `href`, so the host cannot enter.
 *
 * Unrecognised segments collapse to ":seg" rather than passing through: an
 * allowlist of known-static words, with everything else treated as data. A
 * denylist would leak every route added after this was written.
 */
const STATIC_SEGMENTS = new Set([
  'app', 'book', 'author', 'series', 'tag', 'publisher', 'language', 'rating',
  'format', 'shelf', 'shelves', 'search', 'discover', 'read', 'listen',
  'settings', 'account', 'admin', 'tasks', 'about', 'whats-new', 'login',
  'logout', 'register', 'upload', 'edit', 'annotations', 'downloads', 'hot',
  'new', 'rated', 'unread', 'archived', 'category', 'me', 'help',
]);

export function routePattern(pathname: string): string {
  if (!pathname || typeof pathname !== 'string') return '/';
  const segments = pathname.split('/').filter(Boolean);
  if (segments.length === 0) return '/';
  const shaped = segments.map((raw) => {
    const seg = raw.toLowerCase();
    if (STATIC_SEGMENTS.has(seg)) return seg;
    // Numeric segments are ids; everything else unrecognised is also treated as
    // data, because a slug ("the-hobbit") names the user's book just as surely.
    return /^\d+$/.test(seg) ? ':id' : ':seg';
  });
  return '/' + shaped.join('/');
}

/**
 * Coarse browser + platform from a UA string, via a fixed family allowlist.
 *
 * Returns e.g. "Safari on macOS". Never returns any substring of the input:
 * the output is assembled purely from the literals below, so a hostile or
 * unusual UA cannot smuggle text into the report. Unknown → "Unknown browser",
 * which is a perfectly good answer.
 */
export function coarseBrowser(ua: string | undefined): string {
  if (!ua || typeof ua !== 'string') return 'Unknown browser';
  const s = ua.toLowerCase();

  // Order matters: every Chromium UA also claims "safari", Edge claims both,
  // and Chrome-on-iOS ("crios") is really Safari's engine under the hood.
  let engine = 'Unknown browser';
  if (s.includes('firefox') || s.includes('fxios')) engine = 'Firefox';
  else if (s.includes('edg/') || s.includes('edgios')) engine = 'Edge';
  else if (s.includes('opr/') || s.includes('opera')) engine = 'Opera';
  else if (s.includes('crios')) engine = 'Chrome on iOS';
  else if (s.includes('chrome') || s.includes('chromium')) engine = 'Chrome';
  else if (s.includes('safari')) engine = 'Safari';

  let platform = '';
  if (s.includes('iphone') || s.includes('ipad') || s.includes('ipod')) platform = 'iOS';
  else if (s.includes('android')) platform = 'Android';
  else if (s.includes('mac os x') || s.includes('macintosh')) platform = 'macOS';
  else if (s.includes('windows')) platform = 'Windows';
  else if (s.includes('cros')) platform = 'ChromeOS';
  else if (s.includes('linux')) platform = 'Linux';

  return platform ? `${engine} on ${platform}` : engine;
}

/**
 * Bucket a viewport width. Buckets, not pixels: an exact width is a real
 * fingerprinting contribution and adds nothing a bucket does not.
 */
export function viewportBucket(width: number | undefined): string {
  if (typeof width !== 'number' || !Number.isFinite(width)) return 'unknown';
  if (width < 600) return 'narrow (<600px)';
  if (width < 900) return 'medium (600-899px)';
  if (width < 1280) return 'wide (900-1279px)';
  return 'extra wide (>=1280px)';
}

/**
 * Best-effort scrub of the ONE free-text field.
 *
 * This is explicitly NOT the privacy mechanism — the allowlist above is, and
 * the user's own review is the backstop. This exists because an error string is
 * the one place a path or URL demonstrably does show up, and catching the
 * common shapes means the user is reviewing something that is already close to
 * clean rather than being the sole line of defence.
 *
 * Order is deliberate: URLs before Unix paths, or "https://host/a/b" gets its
 * path half eaten first and the host survives as a bare token.
 */
export function scrubFreeText(text: string | undefined): string {
  if (!text || typeof text !== 'string') return '';
  return text
    // Any absolute URL, including the instance's own origin.
    .replace(/\b[a-z][a-z0-9+.-]*:\/\/[^\s'"<>]+/gi, '[url removed]')
    // Windows paths, before the Unix rule so the drive letter goes too.
    .replace(/\b[A-Za-z]:\\[^\s'"<>]*/g, '[path removed]')
    // Unix paths under a KNOWN FILESYSTEM ROOT, consumed to the end of the
    // clause. This rule exists because the paths that matter most here contain
    // SPACES — a library path is "/books/Ursula K. Le Guin/The Dispossessed.epub",
    // naming an author and a title. A `\w`-based pattern silently fails on
    // exactly those and passes the name straight through to a public tracker.
    //
    // Anchoring on a root keeps it precise: matching "/x/y with spaces"
    // generally is unresolvable by regex (prose like "the /app/settings page and
    // try again" is indistinguishable from a path), so we only spend that
    // greediness where a filesystem root proves it really is one. Deliberately
    // excludes "app", which is this product's own ROUTE prefix, not a
    // filesystem root — it falls to the tidy-path rule below instead.
    //
    // Two passes, and the order is load-bearing. First: a rooted path that ends
    // in a FILENAME, stopping at the extension. Real paths terminate at a file,
    // so this removes the whole path and leaves the diagnostic tail intact
    // ("... not found", "... is locked") — which is the part a maintainer reads.
    // The extension must end its token (`(?=\s|$)`), or an initial-laden author
    // directory like "/books/J.R.R. Tolkien/..." would stop dead at ".RR" and
    // pass "Tolkien" through.
    .replace(
      /\/(?:books|config|calibre-library|calibre|library|home|Users|var|tmp|mnt|media|storage|opt|srv|data)\b[^\n,;:"')\]]*?\.\w{2,5}(?=\s|$)/gi,
      '[path removed]',
    )
    // Second: a rooted path with no filename (a bare directory). Nothing marks
    // where it ends, so consume to the end of the clause — over-redacting a
    // directory reference is the right way to be wrong here.
    .replace(
      /\/(?:books|config|calibre-library|calibre|library|home|Users|var|tmp|mnt|media|storage|opt|srv|data)\b[^\n,;:"')\]]*/gi,
      '[path removed]',
    )
    // Tidy (space-free) absolute paths of two or more segments — "/api/v1/book",
    // "/app/settings". A single segment is left alone so short endpoints stay
    // legible; two is where it starts to describe a location.
    .replace(/(?:\/[\w.-]+){2,}\/?/g, '[path removed]')
    // Anything shaped like an email address.
    .replace(/\b[\w.+-]+@[\w-]+\.[\w.-]+\b/g, '[email removed]')
    .trim();
}

/* ── Composition ──────────────────────────────────────────────────────────── */

/**
 * A code fence long enough to contain `content` verbatim.
 *
 * Markdown injection guard, and it is not theoretical: an error message can
 * quote library-controlled text (a book title, a filename), and a plain
 * three-backtick fence CLOSES at the first ``` inside it. Everything after that
 * renders as live Markdown in a PUBLIC issue — headings, links, and images,
 * which GitHub will fetch. Scrubbing does not help: the payload need not
 * contain a URL to break the structure.
 *
 * CommonMark allows a fence of N backticks to contain any run of fewer than N,
 * so measure the longest run and go one better.
 */
function fenceFor(content: string): string {
  let longest = 0;
  for (const run of content.match(/`+/g) || []) {
    if (run.length > longest) longest = run.length;
  }
  return '`'.repeat(Math.max(3, longest + 1));
}

const KIND_LABEL: Record<ReportKind, string> = {
  bug: 'Bug report',
  feature: 'Feature request',
  question: 'Question',
};

/**
 * Build the report body as Markdown.
 *
 * `userText` leads, because the person's own description is the useful part and
 * a wall of diagnostics above it buries the one thing a maintainer reads first.
 * Diagnostics follow in a table the user can plainly see and delete.
 */
export function buildBody(
  kind: ReportKind,
  ctx: ReportContext,
  userText: string,
): string {
  const lines: string[] = [];

  lines.push(userText.trim() || `<!-- Please describe the ${KIND_LABEL[kind].toLowerCase()} here. -->`);
  lines.push('');

  if (ctx.errorMessage) {
    const scrubbed = scrubFreeText(ctx.errorMessage);
    if (scrubbed) {
      const fence = fenceFor(scrubbed);
      lines.push('### Error');
      lines.push(fence);
      lines.push(scrubbed);
      lines.push(fence);
      lines.push('');
    }
  }

  if (ctx.componentStack) {
    const stack = ctx.componentStack.trim().split('\n').slice(0, 12).join('\n');
    if (stack) {
      const fence = fenceFor(stack);
      lines.push('<details><summary>Component stack</summary>');
      lines.push('');
      lines.push(fence);
      lines.push(stack);
      lines.push(fence);
      lines.push('');
      lines.push('</details>');
      lines.push('');
    }
  }

  lines.push('### Environment');
  lines.push('');
  lines.push('| | |');
  lines.push('|---|---|');
  lines.push(`| Version | ${ctx.version || 'unknown'} |`);
  lines.push(`| Page | \`${ctx.routePattern}\` |`);
  lines.push(`| Browser | ${ctx.browser} |`);
  if (ctx.theme) lines.push(`| Theme | ${ctx.theme} |`);
  if (ctx.viewport) lines.push(`| Viewport | ${ctx.viewport} |`);
  lines.push('');
  lines.push('<sub>Composed in your browser. Nothing was sent anywhere — posting this is entirely your choice, and you can edit or delete any of it first.</sub>');

  return lines.join('\n');
}

/** A short, greppable title. The user retitles it on GitHub before posting. */
export function buildTitle(kind: ReportKind, ctx: ReportContext): string {
  if (kind === 'bug' && ctx.errorMessage) {
    const firstLine = scrubFreeText(ctx.errorMessage).split('\n')[0].slice(0, 80);
    if (firstLine) return `[Bug] ${firstLine}`;
  }
  const where = ctx.routePattern && ctx.routePattern !== '/' ? ` on ${ctx.routePattern}` : '';
  return `[${KIND_LABEL[kind]}]${where}`;
}

/**
 * The precomposed GitHub URL. Opening it lands the user on GitHub's own new-issue
 * form with the fields prefilled — where they review, edit and decide. We never
 * auto-submit, and could not: creating an issue requires their credentials on
 * their own session, which is exactly the property that makes this zero-egress.
 *
 * Truncates the body if the encoded URL would exceed the cap, so the link always
 * works. Reports the truncation in-band rather than silently dropping context.
 */
export function githubIssueUrl(title: string, body: string, labels?: string[]): string {
  const build = (b: string) => {
    const params = new URLSearchParams({ title, body: b });
    if (labels?.length) params.set('labels', labels.join(','));
    return `${CHANNELS.issuesNew}?${params.toString()}`;
  };

  let url = build(body);
  if (url.length <= MAX_URL_BYTES) return url;

  const notice = '\n\n<sub>(Some details were trimmed to fit. Paste anything else you have.)</sub>';
  // Shrink until it fits. Binary-search would be cleverer; a body this size
  // converges in a handful of steps and the simple loop is obviously correct.
  let keep = body.length;
  while (keep > 0) {
    keep = Math.floor(keep * 0.8);
    url = build(body.slice(0, keep) + notice);
    if (url.length <= MAX_URL_BYTES) return url;
  }
  return build(notice);
}

/** Discord for questions — a conversation, not a ticket. */
export function discordUrl(): string {
  return CHANNELS.discord;
}

/**
 * Route a report to its channel. Bugs and features become a precomposed issue;
 * questions go to Discord, where the precomposed text is handed over via the
 * clipboard instead (Discord has no prefill-by-URL mechanism, and inventing one
 * would mean routing the text through a third party).
 */
export function reportTarget(
  kind: ReportKind,
  ctx: ReportContext,
  userText: string,
): { url: string; body: string; title: string; needsManualPaste: boolean } {
  const body = buildBody(kind, ctx, userText);
  const title = buildTitle(kind, ctx);
  if (kind === 'question') {
    return { url: discordUrl(), body, title, needsManualPaste: true };
  }
  return {
    url: githubIssueUrl(title, body, kind === 'bug' ? ['bug'] : ['enhancement']),
    body,
    title,
    needsManualPaste: false,
  };
}

/**
 * Gather the allowlisted context from the live document.
 *
 * Every read is individually guarded: this is called from the ErrorBoundary,
 * where the app has ALREADY crashed once, and a throw here would replace the
 * recovery UI with the blank screen that boundary exists to prevent.
 */
export function collectContext(overrides?: Partial<ReportContext>): ReportContext {
  const safe = <T,>(fn: () => T, fallback: T): T => {
    try {
      return fn();
    } catch {
      return fallback;
    }
  };

  return {
    version: safe(
      () => (window as unknown as { __CWNG_VERSION__?: string }).__CWNG_VERSION__ || 'unknown',
      'unknown',
    ),
    // pathname, never href — href carries the instance host.
    routePattern: safe(() => routePattern(window.location.pathname), '/'),
    browser: safe(() => coarseBrowser(navigator.userAgent), 'Unknown browser'),
    theme: safe(
      () => document.documentElement.getAttribute('data-theme') || undefined,
      undefined,
    ),
    viewport: safe(() => viewportBucket(window.innerWidth), undefined),
    ...overrides,
  };
}
