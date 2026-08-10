// build.mjs — Calibre-Web NextGen wiki site builder.
//
// Pipeline:
//   1. run repo/scripts/generate-wiki.py into a temp dir (content is GENERATED, never
//      copied into this tree as source-of-truth — it is a build artifact)
//   2. render the content pages from GitHub-wiki markdown to static HTML
//      ([[Wiki Link]] rewriting, GitHub-wiki-URL internalizing, callouts, code blocks
//      with copy buttons, heading anchors, tables, repo-local screenshot figures)
//   3. emit dist/ — one directory per page, plus sitemap.xml, robots.txt, 404.html,
//      _headers, search index, assets and images.
//
// Internal hrefs are written with the token ~ROOT~/ and replaced with each page's
// relative prefix, so dist/ works from file:// AND from the site root.

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import MarkdownIt from 'markdown-it';
import hljs from 'highlight.js/lib/common';
import {
  esc, ICONS, pageShell, renderNav, renderToc, renderPrevNext,
} from './src/templates.mjs';
import { PLACEHOLDER_JPG } from './src/placeholders.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));

// Resolve the repo root from git rather than by walking up a fixed number of
// directories. The old `resolve(HERE, '..', '..')` assumed the shared-checkout
// layout `<workspace>/repo/wikisite/`, which is NOT how this builds in a
// `git worktree` — there the repo root is the worktree itself and wikisite sits
// directly beneath it. Since sessions are required to work in worktrees (four of
// them share one checkout), that assumption broke every worktree build with a
// misleading "generator exited non-zero".
const REPO = execFileSync('git', ['rev-parse', '--show-toplevel'],
  { cwd: HERE, encoding: 'utf8' }).trim();
const DIST = path.join(HERE, 'dist');
const SRC_IMG = path.join(HERE, 'src', 'img');

const SITE_URL = 'https://wiki.calibrewebnextgen.com';
const GH = 'https://github.com/new-usemame/Calibre-Web-NextGen';
const DISCORD = 'https://discord.gg/B8NXZmcp32';
const IMAGE_REF = 'ghcr.io/new-usemame/calibre-web-nextgen:latest';
const BUILD_DATE = new Date().toISOString().slice(0, 10);

/* ------------------------------------------------------------------ nav -- */

const NAV = [
  {
    group: 'Get Started',
    pages: [
      ['Home', ''],
      ['Quick Start', 'quick-start'],
      ['Installation', 'installation'],
      ['First Run', 'first-run'],
      ['Updating', 'updating'],
    ],
  },
  {
    group: 'Install',
    pages: [
      ['Docker Compose', 'install-with-docker-compose'],
      ['Synology', 'install-on-synology'],
      ['Unraid', 'install-on-unraid'],
      ['TrueNAS SCALE', 'install-on-truenas-scale'],
      ['QNAP', 'install-on-qnap'],
      ['Portainer', 'install-with-portainer'],
      ['Dockge', 'install-with-dockge'],
      ['Migrating', 'migrating'],
    ],
  },
  {
    group: 'Using it',
    pages: [
      ['Configuration', 'configuration'],
      ['Shelfmark', 'shelfmark'],
    ],
  },
  {
    group: 'Sync',
    pages: [
      ['KOReader Sync', 'koreader-sync'],
      ['Kobo Sync', 'kobo-sync'],
    ],
  },
  {
    group: 'Help',
    pages: [
      ['Troubleshooting', 'troubleshooting'],
      ['Differences from Upstream', 'differences-from-upstream'],
      ['Contributing', 'contributing'],
    ],
  },
];

/* --------------------------------------------------------- step 1: content -- */

function runGenerator() {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'cwng-wiki-gen-'));
  console.log('· Running wiki content generator…');
  try {
    const res = execFileSync(
      'python3',
      ['scripts/generate-wiki.py', '--repo', '.', '--src', 'wiki-src', '--out', out],
      { cwd: REPO, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] },
    );
    if (res && res.trim()) console.log(res.trim());
  } catch (e) {
    // A non-zero generator exit is FATAL, never a warning.
    //
    // The drift tripwire fires when a source section exists that no wiki template
    // consumes, which means the rendered set is incomplete. Historically the
    // publishing path treated that as survivable and reported 285 consecutive
    // "sync-wiki OK" while SEVEN written install guides silently never reached the
    // live wiki (F-97846a). A site that renders whatever it happens to get would
    // reproduce that bug on a new surface, which is precisely what we are here to
    // stop. Fail loudly and let a human decide where the new content belongs.
    const msg = [e.stdout, e.stderr].filter(Boolean).join('\n').trim();
    console.error('\nFATAL: wiki content generator exited non-zero.\n');
    if (msg) console.error(msg);
    console.error(
      '\nThe generated content set is not trustworthy, so no site is built. Route the\n' +
      'listed section into a repo/wiki-src/*.md page, or add it to IGNORED_SLUGS with a\n' +
      'reason, then re-run.',
    );
    process.exit(1);
  }
  const files = fs.existsSync(out) ? fs.readdirSync(out).filter((f) => f.endsWith('.md')) : [];
  if (files.length < 20) {
    console.error(`Generator produced only ${files.length} markdown files in ${out} — refusing to build on empty content.`);
    process.exit(1);
  }
  console.log(`· Generator emitted ${files.length} markdown files.`);
  return out;
}

function loadPages(genDir) {
  const pages = new Map(); // slug -> {file, raw}
  for (const f of fs.readdirSync(genDir)) {
    if (!f.endsWith('.md') || f.startsWith('_')) continue;
    const slug = f.replace(/\.md$/, '').toLowerCase();
    pages.set(slug, { file: f, raw: fs.readFileSync(path.join(genDir, f), 'utf8') });
  }
  return pages;
}

/* ------------------------------------------------------- markdown helpers -- */

function pageSlug(name) {
  return name.trim().replace(/\s+/g, '-').toLowerCase().replace(/[^a-z0-9-]/g, '');
}

// GitHub's heading-anchor algorithm. Generated content links to anchors like
// #reverse-proxy--cloudflare-tunnel (note the double hyphen), so we must match
// exactly: lowercase, drop punctuation, each space becomes a hyphen — do NOT
// collapse runs of spaces.
function githubSlug(text) {
  return text
    .toLowerCase()
    .replace(/<[^>]+>/g, '')
    .replace(/[^\p{L}\p{N} _-]/gu, '')
    .replace(/ /g, '-');
}

function stripMdInline(text) {
  return text
    .replace(/\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]/g, (m, a) => a)
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[*`_]/g, '')
    .replace(/<[^>]+>/g, '')
    .trim();
}

function rewriteWikiLinks(src) {
  return src.replace(/\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]/g, (m, a, b) => {
    const label = a.trim();
    const page = (b ? b : a).trim();
    const slug = pageSlug(page);
    return `[${label}](${slug === 'home' ? '~ROOT~/' : `~ROOT~/${slug}/`})`;
  });
}

// Drop README chrome that must never reach the site: the centered banner <p>
// and shields.io badge lines. Everything else renders.
function stripReadmeChrome(src) {
  return src
    .replace(/<p align="center">[\s\S]*?<\/p>/g, '')
    .split('\n')
    .filter((line) => !(line.trimStart().startsWith('[![') && line.includes('shields.io')))
    .join('\n');
}

function firstParagraphText(src) {
  const lines = src.split('\n');
  const para = [];
  let started = false;
  let inFence = false;
  for (const raw of lines) {
    const line = raw.trim();
    if (line.startsWith('```')) { inFence = !inFence; continue; }
    if (inFence) continue;
    if (!started) {
      if (!line || line.startsWith('#') || line.startsWith('<') || line.startsWith('[![')
        || line.startsWith('![') || line.startsWith('---') || line.startsWith('>')
        || line.startsWith('|') || line.startsWith('- ') || /^\d+\./.test(line)) continue;
      started = true;
    }
    if (started && !line) break;
    para.push(line);
  }
  return stripMdInline(para.join(' ')).replace(/\s+/g, ' ').trim();
}

function truncate(s, n) {
  if (s.length <= n) return s;
  const cut = s.slice(0, n - 1);
  const at = cut.lastIndexOf(' ');
  return `${cut.slice(0, at > 40 ? at : n - 1)}…`;
}

/* ------------------------------------------------------- image dimensions -- */

function imageSize(buf) {
  try {
    // PNG: 8-byte signature, IHDR width/height big-endian at 16/20
    if (buf.length > 24 && buf.readUInt32BE(0) === 0x89504e47) {
      return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) };
    }
    // JPEG: scan segments for SOF0/1/2
    if (buf.length > 4 && buf[0] === 0xff && buf[1] === 0xd8) {
      let off = 2;
      while (off + 9 < buf.length) {
        if (buf[off] !== 0xff) { off += 1; continue; }
        const marker = buf[off + 1];
        const len = buf.readUInt16BE(off + 2);
        if (marker >= 0xc0 && marker <= 0xc2) {
          return { h: buf.readUInt16BE(off + 5), w: buf.readUInt16BE(off + 7) };
        }
        off += 2 + len;
      }
    }
  } catch { /* fall through */ }
  return null;
}

/* ------------------------------------------------------ code block render -- */

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderCodeBlock(code, langIn) {
  const lang = (langIn || '').trim().split(/\s+/)[0] || 'text';
  let inner;
  if (lang === 'diff') {
    inner = code.split('\n').map((line) => {
      const e = escapeHtml(line);
      if (line.startsWith('+++') || line.startsWith('---')) return e;
      if (line.startsWith('+')) return `<span class="diff-add">${e}</span>`;
      if (line.startsWith('-')) return `<span class="diff-del">${e}</span>`;
      if (line.startsWith('@@')) return `<span class="diff-hunk">${e}</span>`;
      return e;
    }).join('\n');
  } else {
    try {
      inner = lang !== 'text' && hljs.getLanguage(lang)
        ? hljs.highlight(code, { language: lang, ignoreIllegals: true }).value
        : escapeHtml(code);
    } catch {
      inner = escapeHtml(code);
    }
  }
  return `<div class="codeblock" data-lang="${esc(lang)}">`
    + `<div class="codeblock-bar"><span class="codeblock-lang">${esc(lang)}</span>`
    + `<button type="button" class="copy-btn" aria-label="Copy code to clipboard">${ICONS.copy}${ICONS.check}<span class="copy-label">Copy</span></button></div>`
    + `<pre><code class="hljs language-${esc(lang)}">${inner}</code></pre></div>\n`;
}

/* ------------------------------------------------------------- callouts -- */

function calloutKind(text) {
  const t = text.slice(0, 240).toLowerCase();
  if (/\bwarning\b|\bdeleted\b|\bdon't\b|\bdo not\b|\bnever\b|\bcareful\b|\binstant undo\b/.test(t)) return 'warning';
  if (/\btip\b|\beasiest\b/.test(t) || /\?\*\*\s*$/.test(text.trim()) || t.includes('not using a terminal')) return 'tip';
  return 'note';
}

const CALLOUT_LABEL = { note: 'Note', tip: 'Tip', warning: 'Warning' };

function transformCallouts(html) {
  let out = html
    .replace(/<blockquote class="callout callout-(note|tip|warning)">/g,
      (m, kind) => `<div class="callout callout-${kind}" role="note" aria-label="${CALLOUT_LABEL[kind]}">`)
    .replace(/<\/blockquote>/g, '</div>');
  // hoist a leading <strong>…</strong> into a titled header row with an icon
  out = out.replace(
    /(<div class="callout callout-(note|tip|warning)"[^>]*>)\s*<p><strong>([\s\S]*?)<\/strong>\s*/g,
    (m, open, kind, title) => `${open}<p class="callout-title">${ICONS[kind]}${title}</p>\n<p>`,
  );
  return out;
}

/* ----------------------------------------------- repo-local screenshots -- */

// Content images reference github.com/.../raw/main/<path>; those files live in
// the local repo checkout, so we copy them into dist/img/content/ and serve them
// ourselves. If a referenced file is missing from the checkout, fall back to a
// linked placeholder figure — never an external <img>.
function transformContentImages(html, register, warn) {
  return html.replace(
    /<p><img src="(https:\/\/github\.com\/new-usemame\/Calibre-Web-NextGen\/raw\/main\/([^"]+))" alt="([^"]*)"(?:\s+title="[^"]*")?\s*>\s*<\/p>/g,
    (m, full, repoRel, alt) => {
      const local = register(repoRel);
      if (local) {
        const dim = local.size ? ` width="${local.size.w}" height="${local.size.h}"` : '';
        return `<figure class="content-shot"><img src="~ROOT~/${local.rel}" alt="${alt}"${dim} loading="lazy" decoding="async"><figcaption>${alt}</figcaption></figure>`;
      }
      warn(`repo-local copy not found for ${repoRel} — using linked placeholder`);
      return `<figure class="remote-shot">${ICONS.camera}<figcaption>${alt} `
        + `— <a href="${full}" class="ext">view the full screenshot on GitHub</a>.</figcaption></figure>`;
    },
  );
}

/* --------------------------------------------------------- renderer setup -- */

function createMd() {
  const md = new MarkdownIt({ html: true, linkify: true, typographer: false });

  // heading ids + hover anchors; collect TOC entries into env.toc
  md.core.ruler.push('heading_ids', (state) => {
    state.env.toc = state.env.toc || [];
    const seen = state.env.seenIds = state.env.seenIds || new Map();
    let h1Count = 0;
    for (let i = 0; i < state.tokens.length; i++) {
      const tok = state.tokens[i];
      if (tok.type !== 'heading_open') continue;
      const level = Number(tok.tag.slice(1));
      const inline = state.tokens[i + 1];
      if (!inline || inline.type !== 'inline') continue;
      if (level === 1) h1Count += 1;
      let slug = githubSlug(inline.content) || 'section';
      const n = seen.get(slug) || 0;
      seen.set(slug, n + 1);
      if (n > 0) slug = `${slug}-${n}`;
      tok.attrSet('id', slug);
      if (level === 2 || level === 3 || (level === 1 && h1Count > 1)) {
        state.env.toc.push({ id: slug, text: stripMdInline(inline.content), level: level === 1 ? 2 : level });
      }
      inline.children = inline.children || [];
      inline.children.unshift(Object.assign(new state.Token('html_inline', '', 0), {
        content: `<a class="h-anchor" href="#${slug}" aria-hidden="true" tabindex="-1">#</a>`,
      }));
    }
  });

  // classify blockquotes into callout kinds (tag swap happens post-render)
  md.core.ruler.push('callouts', (state) => {
    for (let i = 0; i < state.tokens.length; i++) {
      const tok = state.tokens[i];
      if (tok.type !== 'blockquote_open') continue;
      let text = '';
      for (let j = i + 1; j < state.tokens.length; j++) {
        const t = state.tokens[j];
        if (t.type === 'blockquote_close') break;
        if (t.type === 'inline') { text = t.content; break; }
      }
      tok.attrJoin('class', `callout callout-${calloutKind(text)}`);
    }
  });

  // links: internalize GitHub-wiki URLs, fix known dead fragments, mark external
  const defaultLink = md.renderer.rules.link_open
    || ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options));
  md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    const tok = tokens[idx];
    const href = tok.attrGet('href') || '';
    const wiki = href.match(/^https:\/\/github\.com\/new-usemame\/Calibre-Web-NextGen\/wiki\/([^#?]+)(#[^"]+)?$/);
    if (href === '#supporting-the-project') {
      tok.attrSet('href', `${GH}#supporting-the-project`);
      tok.attrJoin('class', 'ext');
    } else if (wiki) {
      const slug = pageSlug(decodeURIComponent(wiki[1]));
      const anchor = wiki[2] || '';
      tok.attrSet('href', slug === 'home' ? `~ROOT~/${anchor}` : `~ROOT~/${slug}/${anchor}`);
    } else if (href === '~ROOT~/home/') {
      tok.attrSet('href', '~ROOT~/');
    } else if (/^https?:\/\//.test(href)) {
      tok.attrSet('rel', 'noopener');
      tok.attrJoin('class', 'ext');
    }
    return defaultLink(tokens, idx, options, env, self);
  };

  md.renderer.rules.fence = (tokens, idx) => renderCodeBlock(tokens[idx].content, tokens[idx].info);
  md.renderer.rules.table_open = () => '<div class="table-wrap" tabindex="0" role="region" aria-label="Scrollable table"><table>';
  md.renderer.rules.table_close = () => '</table></div>';

  return md;
}

/* -------------------------------------------------------------- home page -- */

function extractFence(src, lang, mustContain) {
  const re = new RegExp('```' + lang + '\\n([\\s\\S]*?)```', 'g');
  let m;
  while ((m = re.exec(src)) !== null) {
    if (!mustContain || m[1].includes(mustContain)) return m[1].trim();
  }
  return null;
}

const SHOTS = {
  'hero-library': [1440, 600],   // real screenshot of the SPA library view, not a placeholder
  'book-detail': [1200, 900],
  reader: [1600, 900],
  settings: [1600, 900],
  'mobile-library': [390, 780],
};

function figureShot(name, caption, eager) {
  // Markup always references <name>.jpg: the shipped file is a lightweight
  // placeholder rendered from the SVG; a real screenshot dropped at the same
  // path (src/img/ then rebuild, or straight into dist/img/) replaces it with
  // no markup change. The matching .svg also ships for reference.
  const [w, h] = SHOTS[name];
  return `<figure class="shot-frame"><img src="~ROOT~/img/${name}.jpg" width="${w}" height="${h}" `
    + `alt="${esc(caption)}" ${eager ? 'fetchpriority="high"' : 'loading="lazy"'} decoding="async">`
    + `<figcaption>${esc(caption)}</figcaption></figure>`;
}

function buildHomeBody(pages, md) {
  const qs = pages.get('quick-start').raw;
  const compose = extractFence(qs, 'yaml');
  const upCmd = extractFence(qs, 'bash', 'docker compose up -d');
  if (!compose || !compose.includes(IMAGE_REF)) {
    throw new Error('Could not extract the compose snippet from generated Quick-Start.md — aborting rather than inventing one.');
  }
  if (!upCmd) throw new Error('Could not extract the up command from generated Quick-Start.md.');

  const platforms = [
    ['Synology', 'install-on-synology', 'Container Manager on DSM 7.2+, click by click.'],
    ['Unraid', 'install-on-unraid', 'The Docker tab, with the exact fields to fill.'],
    ['TrueNAS SCALE', 'install-on-truenas-scale', 'A custom app, with the dataset layout.'],
    ['QNAP', 'install-on-qnap', 'Container Station, step by step.'],
    ['Portainer', 'install-with-portainer', 'Paste the stack, deploy, done.'],
    ['Dockge', 'install-with-dockge', 'A compose stack in the Dockge UI.'],
    ['Docker Compose', 'install-with-docker-compose', 'Any host with a terminal — the baseline path.'],
  ];
  const platformCards = platforms.map(([t, s, d]) =>
    `<a class="platform-card" href="~ROOT~/${s}/"><span class="pc-title">${esc(t)} ${ICONS.arrowRight}</span><span class="pc-desc">${esc(d)}</span></a>`,
  ).join('\n      ');

  const F = {
    ingest: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 2.5v9m0 0 3.5-3.5M10 11.5 6.5 8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M3 13.5v2A1.5 1.5 0 0 0 4.5 17h11a1.5 1.5 0 0 0 1.5-1.5v-2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
    sync: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M4 10a6 6 0 0 1 10.2-4.2M16 10a6 6 0 0 1-10.2 4.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><path d="M14.5 2.5v3.3h-3.3M5.5 17.5v-3.3h3.3" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    covers: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="2.5" y="4" width="15" height="12" rx="2" stroke="currentColor" stroke-width="1.7"/><path d="M2.5 12.5 7 8.5l3.5 3 3-2.6 4 3.6" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><circle cx="7" cy="7" r="1.1" fill="currentColor"/></svg>',
    phone: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="6" y="2.5" width="8" height="15" rx="2" stroke="currentColor" stroke-width="1.7"/><path d="M9 14.5h2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
    book: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M4 16V6.5A2.5 2.5 0 0 1 6.5 4H16v11.5H6.5A2.5 2.5 0 0 0 4 18v-2Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M4 16a2.5 2.5 0 0 0 2.5 2.5H16" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
    shield: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 2.5 3.5 5.5v4.6c0 3.9 2.8 6.6 6.5 7.9 3.7-1.3 6.5-4 6.5-7.9V5.5L10 2.5Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="m7.3 10 1.9 1.9 3.5-3.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };
  const features = [
    [F.ingest, 'Drop-folder ingest', 'Drop an .epub into the ingest folder and it appears in your library seconds later, metadata and cover fetched.'],
    [F.sync, 'Progress sync', 'Two-way reading-progress sync with <a href="~ROOT~/koreader-sync/">KOReader</a> and <a href="~ROOT~/kobo-sync/">Kobo</a> e-readers, against your own server.'],
    [F.covers, 'Metadata & covers', 'Fetch metadata and high-res covers from Hardcover, Google Books, Amazon, iTunes and Open Library.'],
    [F.phone, 'A modern UI', 'A redesigned, responsive interface that works on your phone — with the classic view one tap away.'],
    [F.book, 'Reads everywhere', 'OPDS feeds for reader apps, an in-browser reader, and send-to-e-reader — everything CWA already does.'],
    [F.shield, 'Fixed & shipped', 'The upstream fixes that were stuck in the PR queue, plus fresh ones — in regular releases, byte-compatible and reversible.'],
  ];
  const featureCards = features.map(([icon, t, d]) =>
    `<div class="feature-card"><div class="feature-icon">${icon}</div><h3>${t}</h3><p>${d}</p></div>`,
  ).join('\n      ');

  const homeSrc = stripReadmeChrome(pages.get('home').raw);
  const env = {};
  let generated = md.render(rewriteWikiLinks(homeSrc), env);

  const body = `<article class="home">
  <section class="hero">
    <span class="hero-chip">Community-maintained CWA build</span>
    <h1>Calibre-Web <span class="accent">NextGen</span></h1>
    <p class="lede">Your self-hosted eBook library server, with the bug fixes you&rsquo;ve been
    waiting for — a drop-in replacement for Calibre-Web-Automated that keeps your library,
    users and settings, and is running in about two minutes.</p>
    <div class="hero-grid">
      <div class="hero-run">
        ${renderCodeBlock(compose, 'yaml')}
      </div>
      <div class="hero-sub">
        <div class="hero-after">
          <ol class="hero-steps">
            <li><span class="step-n">1</span><span>Save the block above as <code>docker-compose.yml</code>.</span></li>
            <li><span class="step-n">2</span><span>Run <code>${esc(upCmd)}</code>.</span></li>
            <li><span class="step-n">3</span><span>Open <code>http://localhost:8083</code> — log in with <code>admin</code> / <code>admin123</code> and change the password.</span></li>
          </ol>
          <div class="hero-links">
            <a class="btn btn-primary" href="~ROOT~/quick-start/">Quick Start guide ${ICONS.arrowRight}</a>
            <a class="btn btn-ghost" href="~ROOT~/installation/">All install guides</a>
            <a class="btn btn-ghost" href="${GH}">${ICONS.github} GitHub</a>
          </div>
          <p class="hero-steps" style="margin-top:1rem"><span>Just the image: <code>docker pull ${IMAGE_REF}</code></span></p>
        </div>
        <div class="hero-shot">
          ${figureShot('hero-library', 'The new library view in Calibre-Web NextGen', true)}
        </div>
      </div>
    </div>
  </section>

  <section class="home-section" id="install-guides">
    <h2>Install where you already run Docker</h2>
    <p class="section-sub">Every guide covers a fresh install, switching from stock CWA, and updating later — with the exact buttons for your platform.</p>
    <div class="platform-grid">
      ${platformCards}
    </div>
  </section>

  <section class="home-section" id="what-you-get">
    <h2>What you get</h2>
    <p class="section-sub">Everything Calibre-Web-Automated does, plus the fixes that were waiting in its pull-request queue — and a steady release cadence.</p>
    <div class="feature-grid">
      ${featureCards}
    </div>
    <div class="shot-row" style="margin-top:2rem">
      ${figureShot('book-detail', 'Book details, formats and metadata')}
      ${figureShot('reader', 'Reading in the browser, no app required')}
      ${figureShot('settings', 'Setting up KOReader progress sync')}
    </div>
  </section>

  <section class="home-section" id="on-your-phone">
    <div class="mobile-band">
      <div>
        <h2>Made for the phone, too</h2>
        <p class="section-sub">The new interface is responsive end to end — browse, search and read your library from the sofa. Sync carries your progress back to your e-reader.</p>
        <a class="btn btn-ghost" href="~ROOT~/first-run/">First-run setup ${ICONS.arrowRight}</a>
      </div>
      ${figureShot('mobile-library', 'The library on a phone')}
    </div>
  </section>

  <div class="home-body prose">
    ${generated}
  </div>
</article>`;

  const toc = [
    { id: 'install-guides', text: 'Install guides', level: 2 },
    { id: 'what-you-get', text: 'What you get', level: 2 },
    { id: 'on-your-phone', text: 'On your phone', level: 2 },
    ...(env.toc || []),
  ];
  return { body, toc };
}

/* ----------------------------------------------------------------- 404 -- */

function notFoundBody() {
  return `<article class="notfound">
  <p class="nf-code">404</p>
  <h1>This page isn&rsquo;t on the shelf.</h1>
  <p>The link may be old, or the page moved. Search the docs, or start from one of these:</p>
  <div class="nf-links">
    <a class="btn btn-primary" href="~ROOT~/">Home</a>
    <a class="btn btn-ghost" href="~ROOT~/quick-start/">Quick Start</a>
    <a class="btn btn-ghost" href="~ROOT~/installation/">Installation</a>
    <a class="btn btn-ghost" href="~ROOT~/troubleshooting/">Troubleshooting</a>
  </div>
</article>`;
}

/* --------------------------------- per-section search snippets from markdown -- */

// Full plain-text of a page body (fences/tables/headings/images/html stripped,
// lists kept) — used to synthesize a section snippet for pages with no h2/h3,
// so their content is still findable (e.g. "allow uploads" in First Run).
function plainBodyText(src) {
  const lines = stripReadmeChrome(src).split('\n');
  const out = [];
  let inFence = false;
  for (const raw of lines) {
    const line = raw.trim();
    if (line.startsWith('```')) { inFence = !inFence; continue; }
    if (inFence || line.startsWith('#') || line.startsWith('|') || line.startsWith('![') || line.startsWith('<')) continue;
    out.push(line);
  }
  return stripMdInline(out.join(' ')).replace(/\s+/g, ' ').trim();
}

function searchSections(src) {
  const clean = stripReadmeChrome(src);
  const lines = clean.split('\n');
  const secs = [];
  let current = null;
  let inFence = false;
  for (const raw of lines) {
    const line = raw.trim();
    if (line.startsWith('```')) { inFence = !inFence; continue; }
    if (inFence) continue;
    const h = raw.match(/^(#{2,3})\s+(.*)/);
    if (h) {
      current = { h: stripMdInline(h[2]), text: [] };
      secs.push(current);
      continue;
    }
    if (!current || current.text.length) continue;
    if (!line || line.startsWith('#') || line.startsWith('|') || line.startsWith('![')
      || line.startsWith('<') || line.startsWith('- ') || line.startsWith('>') || line === '---') continue;
    current.text.push(line);
  }
  const mapped = secs.map((s) => ({
    h: s.h,
    a: githubSlug(s.h),
    t: truncate(stripMdInline(s.text.join(' ')).replace(/\s+/g, ' '), 150),
  }));
  if (!mapped.length) return [{ h: '', a: '', t: truncate(plainBodyText(src), 240) }];
  return mapped;
}

/* ----------------------------------------------------------------- main -- */

function main() {
  const genDir = runGenerator();
  const pages = loadPages(genDir);

  const flatNav = NAV.flatMap((g) => g.pages.map(([title, slug]) => ({ title, slug, group: g.group })));
  const missing = flatNav.filter((p) => p.slug && !pages.has(p.slug));
  if (missing.length) {
    console.error('Nav pages missing from generated content:', missing.map((p) => p.slug).join(', '));
    process.exit(1);
  }
  const mapped = new Set(flatNav.map((p) => p.slug).filter(Boolean));
  const extra = [...pages.keys()].filter((s) => s !== 'home' && !mapped.has(s));
  if (extra.length) {
    console.log('· Unmapped generated pages added under “More”:', extra.join(', '));
    const more = extra.map((s) => [s.split('-').map((w) => w[0].toUpperCase() + w.slice(1)).join(' '), s]);
    NAV.push({ group: 'More', pages: more });
    for (const [title, slug] of more) flatNav.push({ title, slug, group: 'More' });
  }

  // Clean dist/ but PRESERVE operator-dropped real screenshots (dist/img/*.jpg):
  // humans drop replacement JPEGs at those exact paths and rebuilds must keep them.
  const preserved = new Map();
  const distImg = path.join(DIST, 'img');
  if (fs.existsSync(distImg)) {
    for (const f of fs.readdirSync(distImg)) {
      const p = path.join(distImg, f);
      if (fs.statSync(p).isFile() && f.endsWith('.jpg')) preserved.set(f, fs.readFileSync(p));
    }
  }
  fs.rmSync(DIST, { recursive: true, force: true });
  fs.mkdirSync(path.join(DIST, 'assets'), { recursive: true });
  fs.mkdirSync(path.join(DIST, 'img', 'content'), { recursive: true });

  const md = createMd();
  const searchIndex = [];
  const sitemapUrls = [];
  const problems = [];
  const warn = (m) => problems.push(m);
  const written = [];

  // repo-local content image registry: copy into dist/img/content/<basename>
  const contentImgDir = path.join(DIST, 'img', 'content');
  function registerContentImage(repoRel) {
    const srcAbs = path.join(REPO, repoRel);
    if (!fs.existsSync(srcAbs)) return null;
    const base = repoRel.split('/').pop().replace(/[^A-Za-z0-9._-]/g, '_');
    const buf = fs.readFileSync(srcAbs);
    fs.writeFileSync(path.join(contentImgDir, base), buf);
    return { rel: `img/content/${base}`, size: imageSize(buf) };
  }

  const navBySlug = new Map(flatNav.map((p) => [p.slug, p]));

  function renderContentPage(slug) {
    const raw = stripReadmeChrome(pages.get(slug).raw);
    const env = {};
    let html = md.render(rewriteWikiLinks(raw), env);
    html = transformCallouts(html);
    html = transformContentImages(html, registerContentImage, warn);
    if (/<img[^>]+src="https?:\/\//.test(html)) {
      warn(`${slug}: leftover external <img> — stripped`);
      html = html.replace(/<img[^>]+src="https?:\/\/[^"]*"[^>]*>/g, '');
    }
    return { html, toc: env.toc || [], seen: env.seenIds || new Map() };
  }

  function emit(rel, htmlOut) {
    const target = path.join(DIST, rel);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, htmlOut);
    written.push(rel);
  }

  function checkAnchors(slug, htmlOut, validIds) {
    const ids = new Set(validIds.keys ? validIds.keys() : validIds);
    ids.add('main'); // skip link target
    const hrefs = [...htmlOut.matchAll(/href="#([^"]+)"/g)].map((m) => m[1]);
    for (const a of new Set(hrefs)) {
      if (!ids.has(a)) warn(`${slug}: in-page anchor #${a} has no matching heading id`);
    }
  }

  const navHtmlCache = new Map();
  function navFor(currentSlug) {
    const key = currentSlug || '<home>';
    if (!navHtmlCache.has(key)) {
      navHtmlCache.set(key, renderNav({
        nav: NAV.map((g) => ({ group: g.group, pages: g.pages.map(([title, slug]) => ({ title, slug })) })),
        currentSlug,
      }));
    }
    return navHtmlCache.get(key);
  }

  // ----- home page -----
  const { body: homeBody, toc: homeToc } = buildHomeBody(pages, md);
  const homeDesc = 'Calibre-Web NextGen is the community-maintained, drop-in Calibre-Web-Automated image — your self-hosted eBook library with the fixes shipped. Running in about two minutes.';
  let homeHtml = pageShell({
    title: 'Calibre-Web NextGen — self-hosted eBook library, community-maintained',
    description: homeDesc,
    slug: '',
    prefix: './',
    currentSlug: '',
    nav: navFor(''),
    bodyHtml: homeBody,
    toc: renderToc(homeToc),
    prevNext: renderPrevNext(null, navBySlug.get('quick-start')),
    eyebrow: '',
    isHome: true,
    jsonLd: [
      {
        '@context': 'https://schema.org',
        '@type': 'WebSite',
        name: 'Calibre-Web NextGen docs',
        url: `${SITE_URL}/`,
      },
      {
        '@context': 'https://schema.org',
        '@type': 'SoftwareApplication',
        name: 'Calibre-Web NextGen',
        applicationCategory: 'MultimediaApplication',
        operatingSystem: 'Docker',
        offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
        license: 'https://www.gnu.org/licenses/gpl-3.0.html',
        codeRepository: GH,
        url: `${SITE_URL}/`,
        downloadUrl: `${GH}/pkgs/container/calibre-web-nextgen`,
        description: homeDesc,
      },
    ],
  });
  // home body images also go through the content-image transform
  homeHtml = transformContentImages(homeHtml, registerContentImage, warn);
  homeHtml = homeHtml.replace(/~ROOT~\//g, './');
  checkAnchors('home', homeHtml, new Map([...homeToc.map((t) => [t.id, 1])]));
  emit('index.html', homeHtml);
  sitemapUrls.push({ loc: `${SITE_URL}/`, priority: '1.0' });

  // ----- content pages -----
  for (const page of flatNav) {
    if (!page.slug) continue;
    const { html, toc, seen } = renderContentPage(page.slug);
    const desc = truncate(firstParagraphText(pages.get(page.slug).raw), 168);
    const idx = flatNav.findIndex((p) => p.slug === page.slug);
    const prev = idx > 0 ? flatNav[idx - 1] : null;
    const next = idx < flatNav.length - 1 ? flatNav[idx + 1] : null;
    const canonical = `${SITE_URL}/${page.slug}/`;
    let out = pageShell({
      title: page.title,
      description: desc,
      slug: page.slug,
      prefix: '../',
      currentSlug: page.slug,
      nav: navFor(page.slug),
      bodyHtml: `<article class="prose">${html}</article>`,
      toc: renderToc(toc),
      prevNext: renderPrevNext(
        prev ? { title: prev.title, slug: prev.slug } : null,
        next ? { title: next.title, slug: next.slug } : null,
      ),
      eyebrow: page.group,
      isHome: false,
      canonical,
      jsonLd: {
        '@context': 'https://schema.org',
        '@type': 'TechArticle',
        headline: page.title,
        description: desc,
        url: canonical,
        dateModified: BUILD_DATE,
        inLanguage: 'en',
        isAccessibleForFree: true,
        author: { '@type': 'Organization', name: 'Calibre-Web NextGen', url: GH },
        publisher: { '@type': 'Organization', name: 'Calibre-Web NextGen', url: GH },
        isPartOf: { '@type': 'WebSite', name: 'Calibre-Web NextGen docs', url: `${SITE_URL}/` },
      },
    });
    out = out.replace(/~ROOT~\//g, '../');
    checkAnchors(page.slug, out, seen);
    emit(`${page.slug}/index.html`, out);
    sitemapUrls.push({ loc: canonical, priority: ['quick-start', 'installation'].includes(page.slug) ? '0.9' : '0.7' });

    searchIndex.push({
      p: page.title,
      s: page.slug,
      d: truncate(firstParagraphText(pages.get(page.slug).raw), 150),
      secs: searchSections(pages.get(page.slug).raw),
    });
  }

  // ----- 404 (root-absolute paths: served at arbitrary missing URLs) -----
  let nf = pageShell({
    title: 'Page not found',
    description: 'This page is not on the shelf — search the docs or start from the home page.',
    slug: '404',
    prefix: '/',
    currentSlug: '__404__',
    nav: navFor('__404__'),
    bodyHtml: notFoundBody(),
    toc: { aside: '', mobile: '' },
    prevNext: '',
    eyebrow: '',
    isHome: false,
    canonical: `${SITE_URL}/404.html`,
    jsonLd: null,
  });
  nf = nf.replace(/~ROOT~\//g, '/');
  emit('404.html', nf);

  // ----- search index as a script (works from file://, unlike fetch of JSON) -----
  const homeEntry = {
    p: 'Home',
    s: '',
    d: truncate(homeDesc, 150),
    secs: [
      { h: 'Install guides', a: 'install-guides', t: 'Synology, Unraid, TrueNAS, QNAP, Portainer, Dockge, plain Compose.' },
      { h: 'What you get', a: 'what-you-get', t: 'Drop-folder ingest, progress sync, metadata, a modern UI.' },
    ],
  };
  fs.writeFileSync(
    path.join(DIST, 'assets', 'search-index.js'),
    `window.__CWNG_SEARCH=${JSON.stringify([homeEntry, ...searchIndex])};\n`,
  );

  // ----- static assets -----
  fs.copyFileSync(path.join(HERE, 'src', 'cwng-site.css'), path.join(DIST, 'assets', 'site.css'));
  fs.copyFileSync(path.join(HERE, 'src', 'cwng-site.js'), path.join(DIST, 'assets', 'site.js'));
  for (const f of fs.readdirSync(SRC_IMG)) {
    fs.copyFileSync(path.join(SRC_IMG, f), path.join(DIST, 'img', f));
  }
  // restore preserved operator screenshots over any placeholder
  for (const [name, buf] of preserved) fs.writeFileSync(path.join(DIST, 'img', name), buf);
  // guarantee the five screenshot slots always exist: if neither src/img nor a
  // preserved operator file provided <name>.jpg, write the embedded placeholder.
  // A real screenshot dropped at dist/img/<name>.jpg is never overwritten.
  for (const [name, b64] of Object.entries(PLACEHOLDER_JPG)) {
    const target = path.join(DIST, 'img', `${name}.jpg`);
    if (!fs.existsSync(target)) fs.writeFileSync(target, Buffer.from(b64, 'base64'));
  }
  for (const f of ['favicon.svg', 'favicon-32.png', 'apple-touch-icon.png']) {
    fs.copyFileSync(path.join(SRC_IMG, f), path.join(DIST, f));
  }

  // ----- sitemap / robots / headers -----
  const sitemap = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${sitemapUrls.map((u) => `  <url><loc>${u.loc}</loc><lastmod>${BUILD_DATE}</lastmod><changefreq>weekly</changefreq><priority>${u.priority}</priority></url>`).join('\n')}
</urlset>
`;
  fs.writeFileSync(path.join(DIST, 'sitemap.xml'), sitemap);
  // robots.txt must carry the SAME policy Cloudflare's managed robots.txt currently
  // injects zone-wide, because this file is what serves once that injection is turned
  // off — and the injection is the only thing expressing the opt-out today. Turning it
  // off before this file matched would silently drop the operator's ai-train=no.
  //
  // The policy is "search=yes, ai-train=no": every AI *search / answer* crawler
  // (OAI-SearchBot, PerplexityBot, Claude-SearchBot, DuckAssistBot, ChatGPT-User,
  // Perplexity-User, Googlebot, Bingbot) is deliberately absent below, so it falls under
  // `*` and is allowed. Only *training* crawlers get a named group. Per the robots.txt
  // spec a crawler that finds a group naming it ignores `*` entirely, which is exactly
  // the mechanism relied on here — so do not add a search crawler to this list, and do
  // not "tidy" it into a single `*` group.
  const AI_TRAINING_CRAWLERS = [
    'Amazonbot', 'Applebot-Extended', 'Bytespider', 'CCBot', 'ClaudeBot',
    'CloudflareBrowserRenderingCrawler', 'Google-Extended', 'GPTBot', 'meta-externalagent',
  ];
  const robots = [
    '# Search and AI-answer crawlers are welcome: this is documentation, and people',
    '# finding it through search or an assistant is the point.',
    '# Model *training* crawlers are opted out below, matching the Content-Signal.',
    '',
    'User-agent: *',
    'Content-Signal: search=yes,ai-train=no,use=reference',
    'Allow: /',
    '',
    ...AI_TRAINING_CRAWLERS.flatMap((ua) => [`User-agent: ${ua}`, 'Disallow: /', '']),
    `Sitemap: ${SITE_URL}/sitemap.xml`,
    '',
  ].join('\n');
  fs.writeFileSync(path.join(DIST, 'robots.txt'), robots);
  fs.writeFileSync(path.join(DIST, '_headers'),
    `/*
  Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'none'; frame-ancestors 'self'
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
/assets/*
  Cache-Control: public, max-age=86400
/img/*
  Cache-Control: public, max-age=86400
`);

  // ----- self-checks on emitted pages -----
  const thirdParty = new RegExp(
    '<(?:script|img)[^>]+src="https?://'
    + '|<link[^>]+rel="(?:stylesheet|icon|apple-touch-icon|preconnect|dns-prefetch|preload)"[^>]*href="https?://'
    + '|url\\(\\s*[\'"]?https?://',
  );
  for (const rel of written) {
    const content = fs.readFileSync(path.join(DIST, rel), 'utf8');
    if (content.includes('~ROOT~')) warn(`${rel}: unreplaced ~ROOT~ token`);
    if (thirdParty.test(content)) warn(`${rel}: third-party request reference found`);
  }

  // ----- IDENTITY GATE: fatal, never a warning -----
  //
  // This is a public site. The operator's identity tokens must never reach it, and a
  // review pass is the wrong instrument because it only runs when someone remembers.
  // A leak here is not undoable once crawled, so this EXITS NON-ZERO rather than
  // adding to `problems` (which only prints).
  //
  // Note what is NOT in this file: the operator's legal name and home address are
  // themselves sensitive, so hardcoding them into a public repo would BE the leak.
  // Literal tokens are supplied out-of-band via the PII_DENYLIST env var
  // (newline- or comma-separated) or a gitignored .pii-denylist file. The structural
  // patterns below always run and need no secrets.
  const structuralPii = [
    [/\/Users\/[A-Za-z0-9._-]+/g, 'local macOS home path'],
    [/tel:\+?[0-9()\s.-]{7,}/gi, 'tel: link'],
    [/(?:\+[0-9]{1,3}[ .-]?)?\(?[0-9]{3}\)?[ .-][0-9]{3}[ .-][0-9]{4}\b/g, 'phone-shaped string'],
    [/[A-Za-z0-9._%+-]+@privaterelay\.appleid\.com/gi, 'Apple private-relay address'],
  ];
  let denylist = (process.env.PII_DENYLIST || '').split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
  const denyFile = path.join(HERE, '.pii-denylist');
  if (fs.existsSync(denyFile)) {
    denylist = denylist.concat(
      fs.readFileSync(denyFile, 'utf8').split('\n').map((s) => s.trim())
        .filter((s) => s && !s.startsWith('#')),
    );
  }

  const leaks = [];
  const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const fp = path.join(dir, e.name);
    return e.isDirectory() ? walk(fp) : [fp];
  });
  for (const fp of walk(DIST)) {
    // read as latin1 so image bytes are scannable too — EXIF and embedded strings count
    const buf = fs.readFileSync(fp, 'latin1');
    const where = path.relative(DIST, fp);
    for (const [re, label] of structuralPii) {
      const m = buf.match(re);
      if (m) leaks.push(`${where}: ${label} -> ${[...new Set(m)].slice(0, 3).join(', ')}`);
    }
    for (const tok of denylist) {
      if (buf.toLowerCase().includes(tok.toLowerCase())) leaks.push(`${where}: denylisted token "${tok}"`);
    }
  }
  if (leaks.length) {
    console.error('\nFATAL: identity gate failed — personal information found in build output.\n');
    for (const l of [...new Set(leaks)]) console.error('  ' + l);
    console.error('\nNothing is published. Remove the content (or re-shoot the screenshot) and rebuild.');
    process.exit(1);
  }
  console.log(`· Identity gate: clean (${walk(DIST).length} files scanned incl. image bytes`
    + `${denylist.length ? `, ${denylist.length} denylisted tokens` : ', no denylist supplied'}).`);

  console.log(`· Wrote ${written.length} HTML pages + assets to dist/`);
  if (problems.length) {
    console.log('! Build warnings:');
    for (const p of [...new Set(problems)]) console.log('  - ' + p);
  } else {
    console.log('· Self-checks clean: no token leaks, no third-party requests, all in-page anchors resolve.');
  }
  console.log('Done.');
}

main();
