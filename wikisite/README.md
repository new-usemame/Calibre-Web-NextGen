# cwng-wiki — wiki.calibrewebnextgen.com

Static documentation site for **Calibre-Web NextGen**: fully static output in `dist/`,
zero third-party requests, works from `file://` and from any static host. No framework
runtime is shipped to the browser — markdown becomes HTML at build time and the only
runtime JS is a small theme/search/nav/copy script.

> ## ⚠️ CONTENT IS GENERATED — never edit it here
>
> Every content page is produced by **`repo/scripts/generate-wiki.py`** from
> `repo/wiki-src/*.md` (which mirror sections of the README and `docs/`). The build
> re-runs the generator every time into a temp dir and renders THAT; the markdown is a
> build artifact and is never copied into this tree as source-of-truth.
> **Do not hand-edit page content in `dist/`, and do not "fix" page text in this
> directory.** To change what a page says, edit `repo/wiki-src/` (or the README section
> it mirrors) and re-run the build. The only hand-authored things in `wikisite/` are
> the *site shell*: templates, CSS, JS, icons/placeholders, and the home-page layout.

## Build

```bash
cd repo/wikisite
npm ci               # once: markdown-it + highlight.js
node build.mjs       # or: npm run build
```

The build:

1. runs the generator into a fresh temp dir (the "DRIFT TRIPWIRE" advisory is logged;
   per project instructions, non-empty output is success — a genuinely empty/failed
   generation aborts the build);
2. renders all 20 content pages + a designed home page + `404.html` into `dist/`;
3. rewrites `[[Wiki Links]]` and `github.com/.../wiki/...` URLs into internal links,
   matching GitHub's anchor algorithm exactly (double hyphens and all);
4. turns blockquotes into note/tip/warning callouts, gives fenced code a language bar
   + copy button (highlight.js colors, custom diff styling), wraps tables for phones,
   and localizes every content image from the repo checkout (no hotlinking, ever);
5. emits `sitemap.xml`, `robots.txt`, `_headers` (CSP etc.), and the search index as
   `assets/search-index.js` (a `<script>` tag, not `fetch`, so search works on file://);
6. **self-checks every emitted page**: no unreplaced link tokens, no third-party
   requests (script/img/stylesheet/icon), every in-page anchor resolves.

The home-page compose snippet is **extracted from the generated Quick-Start page** at
build time — it is never hardcoded. If the generated content changes, the hero changes
with it; if it can't be extracted, the build aborts rather than inventing one.

## Run locally

```bash
npm run serve        # http://localhost:8099  (python3 http.server over dist/)
```

…or open `dist/index.html` directly. Internal links are relative, and a tiny script
points directory links at `index.html` when the protocol is `file:`, so nav, search
and both themes work with no server at all. (Over HTTP the clean `/page/` URLs stay.)

## Screenshots (the five image slots)

`dist/img/` ships lightweight JPEG placeholders (rendered from the matching `.svg`
files, same aspect ratio) for five screenshot slots. To install real screenshots,
drop JPEGs with **these exact filenames** either into `src/img/` (then re-run the
build) or straight into `dist/img/` (survives rebuilds — the cleaner preserves
`dist/img/*.jpg`). No markup changes are needed; the `<img>` tags already reference
the `.jpg` names with width/height/lazy/async wired up. Match these aspect ratios:

| file | aspect | pixels |
|---|---|---|
| `hero-library.jpg` | 16:10 | 1600×1000 |
| `book-detail.jpg` | 4:3 | 1200×900 |
| `reader.jpg` | 16:9 | 1600×900 |
| `settings.jpg` | 16:9 | 1600×900 |
| `mobile-library.jpg` | 1:2 | 780×1560 |

## Deploy (Cloudflare Workers static assets)

`wrangler.toml` defines worker `cwng-wiki`, serving `./dist` on the route
`wiki.calibrewebnextgen.com/*` (zone `calibrewebnextgen.com`), with
`not_found_handling = "404-page"` so unknown paths get the designed 404 page.

```bash
npx wrangler deploy   # from repo/wikisite — BY HAND; nothing auto-deploys this
```

Do not touch the sibling `cwng-homepage` / `cwng-feedback` workers or their config.

## Layout

```
wikisite/
├── build.mjs            # the whole pipeline + self-checks (start here)
├── src/
│   ├── templates.mjs    # HTML shell: SEO meta, JSON-LD, nav, TOC, footer, search modal
│   ├── cwng-site.css    # the design system (custom properties, both themes)
│   ├── cwng-site.js     # theme toggle, nav drawer, search, TOC spy, copy buttons
│   ├── placeholders.mjs # base64 fallback copies of the five placeholder JPEGs
│   └── img/             # logo, favicons, og card, screenshot placeholders (svg+jpg)
├── dist/                # build output (regenerated; dist/img/*.jpg is preserved)
├── package.json
├── wrangler.toml
└── README.md
```

Conventions: internal hrefs carry a per-page relative prefix (`./` at root, `../` in
page directories) via a `~ROOT~/` token replaced at emit time — never write a
root-absolute `/page/` link (except in `404.html`, which is served at arbitrary
missing paths). Colors exist only as CSS custom properties in `src/cwng-site.css`,
defined for both themes at the top of the file. Identity rule: the only names that may
appear anywhere in this site are `new-usemame` and the project name.

> Provenance note: this directory was built by autonomous agent sessions
> (2026-08-09). The parallel session's unreferenced leftovers (`src/site.css`,
> `src/site.js`, `src/render.js`, `src/templates.js`,
> `src/img/content-placeholder.svg`) were removed on 2026-08-09; everything now
> in `src/` is read by `build.mjs`.
