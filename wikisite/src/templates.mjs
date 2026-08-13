// templates.mjs — HTML shell + components for the CWNG wiki site.
// All internal hrefs use the token ~ROOT~/ which build.mjs replaces with each
// page's relative prefix, so dist/ works from file:// and from the site root.

export function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export const THEME_INIT = `try{var t=localStorage.getItem('cwng-theme');if(t!=='light'&&t!=='dark'){t=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}document.documentElement.dataset.theme=t}catch(e){document.documentElement.dataset.theme='light'}`;

export const ICONS = {
  logo: '<svg class="brand-logo" viewBox="0 0 32 32" aria-hidden="true"><rect x="4" y="5" width="6.5" height="22" rx="1.8" fill="currentColor" opacity="0.55"/><rect x="12.75" y="5" width="6.5" height="22" rx="1.8" fill="currentColor"/><rect x="21.5" y="5" width="6.5" height="22" rx="1.8" fill="currentColor" opacity="0.8" transform="rotate(9 24.75 27)"/></svg>',
  search: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="9" cy="9" r="6.2" stroke="currentColor" stroke-width="1.8"/><path d="M13.6 13.6 17.5 17.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  menu: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M3 5.5h14M3 10h14M3 14.5h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  close: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  sun: '<svg class="sun" viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="10" cy="10" r="4" stroke="currentColor" stroke-width="1.7"/><path d="M10 1.8v2M10 16.2v2M1.8 10h2M16.2 10h2M4.2 4.2l1.4 1.4M14.4 14.4l1.4 1.4M15.8 4.2l-1.4 1.4M5.6 14.4l-1.4 1.4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  moon: '<svg class="moon" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M17.2 12.2A7.5 7.5 0 0 1 7.8 2.8a7.5 7.5 0 1 0 9.4 9.4Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>',
  github: '<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0a8 8 0 0 0-2.53 15.59c.4.07.55-.17.55-.38l-.01-1.49c-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.42 7.42 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48l-.01 2.2c0 .21.15.46.55.38A8 8 0 0 0 8 0Z"/></svg>',
  discord: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M20.32 4.37a19.8 19.8 0 0 0-4.93-1.51 13.8 13.8 0 0 0-.64 1.28 18.3 18.3 0 0 0-5.5 0 13.8 13.8 0 0 0-.64-1.28c-1.71.29-3.37.8-4.93 1.51A20.3 20.3 0 0 0 .1 18.06a19.9 19.9 0 0 0 6.07 3.03c.49-.66.93-1.37 1.3-2.1a12.9 12.9 0 0 1-2.05-.98c.17-.12.34-.25.5-.38a14.2 14.2 0 0 0 12.16 0c.16.13.33.26.5.38-.65.39-1.34.72-2.05.98.37.73.81 1.44 1.3 2.1a19.9 19.9 0 0 0 6.07-3.03 20.3 20.3 0 0 0-3.58-13.69ZM8.02 15.33c-1.18 0-2.16-1.08-2.16-2.42s.95-2.42 2.16-2.42 2.18 1.09 2.16 2.42c0 1.34-.95 2.42-2.16 2.42Zm7.96 0c-1.18 0-2.16-1.08-2.16-2.42s.95-2.42 2.16-2.42 2.18 1.09 2.16 2.42c0 1.34-.95 2.42-2.16 2.42Z"/></svg>',
  copy: '<svg class="copy-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5" stroke="currentColor" stroke-width="1.5"/><path d="M10.5 3.5v-1A1.5 1.5 0 0 0 9 1H4a1.5 1.5 0 0 0-1.5 1.5v5A1.5 1.5 0 0 0 4 9h1" stroke="currentColor" stroke-width="1.5"/></svg>',
  check: '<svg class="copy-check" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2.5 8.5 6 12 13.5 4.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  chevron: '<svg class="chev" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  arrowLeft: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M10 3 5 8l5 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  arrowRight: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m6 3 5 5-5 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  note: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="1.7"/><path d="M10 9.2v4.4" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><circle cx="10" cy="6.2" r="1.15" fill="currentColor"/></svg>',
  tip: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 2.5a5.5 5.5 0 0 1 3.2 9.96c-.5.39-.7.8-.7 1.54H7.5c0-.74-.2-1.15-.7-1.54A5.5 5.5 0 0 1 10 2.5Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M7.5 16.5h5M8.5 18.5h3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  warning: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 2.5 18.5 17h-17L10 2.5Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M10 7.5v4.2" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><circle cx="10" cy="14.3" r="1.1" fill="currentColor"/></svg>',
  camera: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="2.5" y="6" width="19" height="14" rx="2.5" stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="13" r="3.6" stroke="currentColor" stroke-width="1.6"/><path d="M8.5 6 10 3.5h4L15.5 6" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
};

const GH = 'https://github.com/new-usemame/Calibre-Web-NextGen';
const DISCORD = 'https://discord.gg/B8NXZmcp32';

export function renderNav({ nav, currentSlug }) {
  const groups = nav.map((g) => {
    const items = g.pages.map((p) => {
      const href = p.slug ? `~ROOT~/${p.slug}/` : '~ROOT~/';
      const cur = p.slug === currentSlug ? ' aria-current="page"' : '';
      return `<li><a href="${href}"${cur}>${esc(p.title)}</a></li>`;
    }).join('\n        ');
    return `<details class="nav-group" open>
      <summary>${ICONS.chevron}<span>${esc(g.group)}</span></summary>
      <ul class="nav-list">
        ${items}
      </ul>
    </details>`;
  }).join('\n    ');

  return `<div class="nav-drawer-head">
      <span class="nav-drawer-title">Docs</span>
      <button type="button" class="icon-btn" id="nav-close" aria-label="Close navigation">${ICONS.close}</button>
    </div>
    ${groups}
    <div class="nav-externals">
      <a href="${GH}">${ICONS.github} GitHub</a>
      <a href="${GH}/releases">Releases</a>
      <a href="${GH}/issues">Issues</a>
      <a href="${DISCORD}">${ICONS.discord} Discord</a>
    </div>`;
}

export function renderToc(items) {
  if (!items.length) return { aside: '', mobile: '' };
  const lis = items.map((h) =>
    `<li${h.level === 3 ? ' class="toc-sub"' : ''}><a href="#${h.id}">${esc(h.text)}</a></li>`
  ).join('\n        ');
  const aside = `<nav class="toc" id="on-this-page" aria-label="On this page">
      <p class="toc-title">On this page</p>
      <ul>
        ${lis}
      </ul>
    </nav>`;
  const mobile = `<details class="toc-mobile">
    <summary>On this page</summary>
    <ul>
      ${lis}
    </ul>
  </details>`;
  return { aside, mobile };
}

export function renderPrevNext(prev, next) {
  if (!prev && !next) return '';
  const card = (p, cls, label, icon) => p
    ? `<a class="pn-card ${cls}" href="${p.slug ? `~ROOT~/${p.slug}/` : '~ROOT~/'}"><span class="pn-label">${icon}${label}</span><span class="pn-title">${esc(p.title)}</span></a>`
    : '<span></span>';
  return `<nav class="prev-next" aria-label="Previous and next pages">
    ${card(prev, 'pn-prev', 'Previous', ICONS.arrowLeft)}
    ${card(next, 'pn-next', 'Next', ICONS.arrowRight)}
  </nav>`;
}

export function searchModal() {
  return `<div id="search-overlay" hidden>
  <div class="search-backdrop" data-close-search></div>
  <div class="search-modal" role="dialog" aria-modal="true" aria-label="Search the docs">
    <div class="search-box">
      ${ICONS.search}
      <input id="search-input" type="search" placeholder="Search the docs…" autocomplete="off"
        spellcheck="false" role="combobox" aria-expanded="true" aria-controls="search-results"
        aria-label="Search the docs">
      <kbd>Esc</kbd>
    </div>
    <ul id="search-results" role="listbox" aria-label="Search results"></ul>
    <div class="search-foot">
      <span><kbd>↑</kbd><kbd>↓</kbd> move</span>
      <span><kbd>Enter</kbd> open</span>
      <span><kbd>Esc</kbd> close</span>
    </div>
  </div>
</div>`;
}

export function renderFooter() {
  return `<footer class="site-footer">
  <div class="footer-inner">
    <div class="footer-brand">
      <a class="brand" href="~ROOT~/">${ICONS.logo}<span class="brand-name">Calibre-Web <b>NextGen</b></span></a>
      <p>The community-maintained build of Calibre-Web-Automated — upstream fixes shipped, new bugs fixed, one Docker image.</p>
    </div>
    <nav class="footer-col" aria-label="Project links">
      <h2>Project</h2>
      <ul>
        <li><a href="${GH}">GitHub</a></li>
        <li><a href="${GH}/releases">Releases</a></li>
        <li><a href="${GH}/issues">Issue tracker</a></li>
        <li><a href="${GH}/blob/main/CHANGES-vs-upstream.md">What&rsquo;s different</a></li>
      </ul>
    </nav>
    <nav class="footer-col" aria-label="Community links">
      <h2>Community</h2>
      <ul>
        <li><a href="${DISCORD}">Discord server</a></li>
        <li><a href="https://ko-fi.com/calibrewebnextgen">Ko-fi</a></li>
        <li><a href="https://github.com/sponsors/new-usemame">Sponsor on GitHub</a></li>
      </ul>
    </nav>
  </div>
  <div class="footer-legal">
    <span>Community-maintained by <a href="https://github.com/new-usemame">new-usemame</a> and contributors. Not affiliated with the original Calibre-Web or Calibre-Web-Automated authors.</span>
    <span>License: GPL-3.0-or-later</span>
    <span>&copy; <span id="footer-year">2026</span> the Calibre-Web-NextGen contributors</span>
  </div>
</footer>`;
}

export function pageShell({
  title, description, slug, prefix, currentSlug, nav, bodyHtml, toc, prevNext,
  jsonLd, isHome, eyebrow, buildDate, canonical,
}) {
  const fullTitle = isHome ? title : `${title} — Calibre-Web NextGen docs`;
  const canonicalUrl = canonical || (slug ? `https://wiki.calibrewebnextgen.com/${slug}/` : 'https://wiki.calibrewebnextgen.com/');
  const jsonLdScript = jsonLd ? `<script type="application/ld+json">${JSON.stringify(jsonLd).replace(/<\//g, '<\\/')}</script>` : '';

  return `<!DOCTYPE html>
<html lang="en" data-theme="light" data-prefix="${prefix}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(fullTitle)}</title>
<meta name="description" content="${esc(description)}">
<link rel="canonical" href="${canonicalUrl}">
<meta name="theme-color" media="(prefers-color-scheme: light)" content="#f7f4ee">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#11151b">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Calibre-Web NextGen docs">
<meta property="og:title" content="${esc(fullTitle)}">
<meta property="og:description" content="${esc(description)}">
<meta property="og:url" content="${canonicalUrl}">
<meta property="og:image" content="https://wiki.calibrewebnextgen.com/img/og.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${esc(fullTitle)}">
<meta name="twitter:description" content="${esc(description)}">
<meta name="twitter:image" content="https://wiki.calibrewebnextgen.com/img/og.png">
<link rel="icon" href="~ROOT~/favicon.svg" type="image/svg+xml">
<link rel="icon" href="~ROOT~/favicon-32.png" type="image/png" sizes="32x32">
<link rel="apple-touch-icon" href="~ROOT~/apple-touch-icon.png">
<script>${THEME_INIT}</script>
<link rel="stylesheet" href="~ROOT~/assets/site.css">
<script src="~ROOT~/assets/search-index.js" defer></script>
<script src="~ROOT~/assets/site.js" defer></script>
${jsonLdScript}
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<header class="site-header">
  <div class="header-inner">
    <button type="button" class="icon-btn" id="nav-toggle" aria-label="Open navigation" aria-expanded="false" aria-controls="site-nav">${ICONS.menu}</button>
    <a class="brand" href="~ROOT~/" aria-label="Calibre-Web NextGen docs home">${ICONS.logo}<span class="brand-name">Calibre-Web <b>NextGen</b></span><span class="brand-tag">Docs</span></a>
    <span class="header-spacer"></span>
    <button type="button" class="search-btn" id="search-button" aria-label="Search the docs">${ICONS.search}<span class="search-btn-text">Search</span><kbd class="search-btn-kbd">⌘K</kbd></button>
    <a class="icon-btn" href="${GH}" aria-label="GitHub repository">${ICONS.github}</a>
    <button type="button" class="icon-btn" id="theme-toggle" aria-pressed="false" aria-label="Switch to dark theme">${ICONS.sun}${ICONS.moon}</button>
  </div>
</header>
<div id="nav-backdrop"></div>
<div class="layout">
  <nav class="site-nav" id="site-nav" aria-label="Documentation">
    ${nav}
  </nav>
  <main id="main">
    ${eyebrow ? `<p class="page-eyebrow">${esc(eyebrow)}</p>` : ''}
    ${toc.mobile}
    ${bodyHtml}
    ${prevNext || ''}
  </main>
  ${toc.aside}
</div>
${renderFooter()}
${searchModal()}
</body>
</html>`;
}
