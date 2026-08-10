/* Calibre-Web NextGen wiki — runtime: theme, nav drawer, search, scrollspy, copy buttons.
   No dependencies, no network calls. The search index arrives as window.__CWNG_SEARCH
   via a plain <script> tag so everything also works from file://. */
(function () {
  'use strict';

  var root = document.documentElement;
  var PREFIX = root.dataset.prefix || './';
  var FILE_PROTOCOL = location.protocol === 'file:';

  function $(sel, ctx) { return (ctx || document).querySelector(sel); }
  function $all(sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); }

  /* Over file://, directory URLs show a listing instead of index.html, so point
     directory links at index.html explicitly. Over HTTP the clean /page/ URLs stay. */
  if (FILE_PROTOCOL) {
    $all('a[href$="/"]').forEach(function (a) {
      var href = a.getAttribute('href');
      if (!href || /^https?:/i.test(href)) return;
      a.setAttribute('href', href + 'index.html');
    });
  }

  /* ---------------- theme toggle ---------------- */

  var themeBtn = $('#theme-toggle');
  function syncThemeButton() {
    if (!themeBtn) return;
    var dark = root.dataset.theme === 'dark';
    themeBtn.setAttribute('aria-pressed', dark ? 'true' : 'false');
    themeBtn.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    var color = dark ? '#11151b' : '#f7f4ee';
    $all('meta[name="theme-color"]').forEach(function (m) { m.setAttribute('content', color); });
  }
  if (themeBtn) {
    themeBtn.addEventListener('click', function () {
      var next = root.dataset.theme === 'dark' ? 'light' : 'dark';
      root.dataset.theme = next;
      try { localStorage.setItem('cwng-theme', next); } catch (e) { /* private mode */ }
      syncThemeButton();
    });
    syncThemeButton();
  }

  /* ---------------- nav drawer (mobile) ---------------- */

  var drawer = $('#site-nav');
  var drawerBtn = $('#nav-toggle');
  var drawerClose = $('#nav-close');
  var backdrop = $('#nav-backdrop');
  var lastFocus = null;

  function setDrawer(open) {
    if (!drawer || !drawerBtn) return;
    root.classList.toggle('nav-open', open);
    drawerBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      lastFocus = document.activeElement;
      var first = drawer.querySelector('summary, a');
      if (first) first.focus();
    } else if (lastFocus) {
      lastFocus.focus();
      lastFocus = null;
    }
  }
  if (drawerBtn && drawer) {
    drawerBtn.addEventListener('click', function () {
      setDrawer(!root.classList.contains('nav-open'));
    });
    if (drawerClose) drawerClose.addEventListener('click', function () { setDrawer(false); });
    if (backdrop) backdrop.addEventListener('click', function () { setDrawer(false); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && root.classList.contains('nav-open')) setDrawer(false);
    });
    drawer.addEventListener('click', function (e) {
      var a = e.target.closest ? e.target.closest('a') : null;
      if (a && window.matchMedia('(max-width: 899px)').matches) setDrawer(false);
    });
  }

  /* On phone widths, collapse every nav group except the one holding the current page. */
  if (window.matchMedia('(max-width: 899px)').matches && drawer) {
    $all('details.nav-group', drawer).forEach(function (d) {
      if (!d.querySelector('[aria-current="page"]')) d.removeAttribute('open');
    });
  }

  /* ---------------- copy buttons ---------------- */

  function copyText(text, btn) {
    function done(ok) {
      btn.classList.add('copied');
      var label = btn.querySelector('.copy-label');
      if (label) label.textContent = ok ? 'Copied' : 'Failed';
      setTimeout(function () {
        btn.classList.remove('copied');
        if (label) label.textContent = 'Copy';
      }, 1600);
    }
    function legacy() {
      try {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        done(true);
      } catch (e) { done(false); }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, legacy);
    } else { legacy(); }
  }
  document.addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('.copy-btn') : null;
    if (!btn) return;
    var block = btn.closest('.codeblock');
    var code = block ? block.querySelector('pre code') : null;
    if (code) copyText(code.textContent.replace(/\n$/, ''), btn);
  });

  /* ---------------- on-this-page scrollspy ---------------- */

  var toc = $('#on-this-page');
  if (toc && 'IntersectionObserver' in window) {
    var tocLinks = $all('a[href^="#"]', toc);
    var byId = {};
    var heads = [];
    tocLinks.forEach(function (a) {
      var id = decodeURIComponent(a.getAttribute('href').slice(1));
      var h = document.getElementById(id);
      if (h) { byId[id] = a; heads.push(h); }
    });
    if (heads.length) {
      var current = null;
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) {
            if (current) current.removeAttribute('aria-current');
            current = byId[en.target.id];
            if (current) current.setAttribute('aria-current', 'true');
          }
        });
      }, { rootMargin: '-76px 0px -72% 0px', threshold: 0 });
      heads.forEach(function (h) { io.observe(h); });
    }
  }

  /* ---------------- search ---------------- */

  var overlay = $('#search-overlay');
  var input = $('#search-input');
  var resultsEl = $('#search-results');
  var searchBtn = $('#search-button');
  var INDEX = window.__CWNG_SEARCH || [];
  var flat = [];
  var activeIdx = -1;

  INDEX.forEach(function (p) {
    flat.push({ page: p.p, slug: p.s, heading: '', anchor: '', text: p.d || '', isPage: true });
    (p.secs || []).forEach(function (s) {
      flat.push({ page: p.p, slug: p.s, heading: s.h, anchor: s.a, text: s.t || '', isPage: false });
    });
  });

  var POPULAR_SLUGS = ['quick-start', 'installation', 'first-run', 'updating', 'configuration', 'troubleshooting'];
  var POPULAR = POPULAR_SLUGS.map(function (s) {
    return flat.filter(function (e) { return e.slug === s && e.isPage; })[0];
  }).filter(Boolean);

  function score(entry, tokens) {
    var page = entry.page.toLowerCase();
    var head = entry.heading.toLowerCase();
    var text = entry.text.toLowerCase();
    var total = 0;
    for (var i = 0; i < tokens.length; i++) {
      var t = tokens[i];
      var best = 0;
      if (page === t) best = 40;
      else if (page.indexOf(t) === 0) best = 28;
      else if (page.indexOf(t) !== -1) best = 18;
      if (head) {
        if (head === t) best = Math.max(best, 30);
        else if (head.indexOf(t) === 0) best = Math.max(best, 22);
        else if (head.indexOf(t) !== -1) best = Math.max(best, 12);
      }
      if (text.indexOf(t) !== -1) best = Math.max(best, 4);
      if (!best) return 0; // every token must hit somewhere
      total += best;
    }
    return total + (entry.isPage ? 2 : 0);
  }

  function entryHref(e) {
    var base = e.slug ? PREFIX + e.slug + '/' : PREFIX;
    if (FILE_PROTOCOL) base += 'index.html';
    return e.anchor ? base + '#' + e.anchor : base;
  }

  function renderResults(list, query) {
    activeIdx = -1;
    while (resultsEl.firstChild) resultsEl.removeChild(resultsEl.firstChild);
    if (!list.length) {
      var empty = document.createElement('li');
      empty.className = 'search-empty';
      empty.textContent = query ? 'No results for “' + query + '”.' : 'Type to search the docs.';
      resultsEl.appendChild(empty);
      return;
    }
    list.forEach(function (e, i) {
      var li = document.createElement('li');
      li.setAttribute('role', 'option');
      li.id = 'sr-' + i;
      li.className = 'search-item';
      var a = document.createElement('a');
      a.href = entryHref(e);
      a.tabIndex = -1;
      var crumb = document.createElement('span');
      crumb.className = 'search-item-crumb';
      crumb.textContent = e.heading ? e.page : 'Page';
      var title = document.createElement('span');
      title.className = 'search-item-title';
      title.textContent = e.heading ? e.heading : e.page;
      a.appendChild(crumb);
      a.appendChild(title);
      if (e.text) {
        var snippet = document.createElement('span');
        snippet.className = 'search-item-text';
        snippet.textContent = e.text;
        a.appendChild(snippet);
      }
      li.appendChild(a);
      resultsEl.appendChild(li);
    });
  }

  function runSearch() {
    var q = input.value.trim().toLowerCase();
    if (!q) { renderResults(POPULAR, ''); return; }
    var tokens = q.split(/\s+/).filter(Boolean);
    var scored = [];
    flat.forEach(function (e) {
      var s = score(e, tokens);
      if (s > 0) scored.push([s, e]);
    });
    scored.sort(function (a, b) { return b[0] - a[0]; });
    renderResults(scored.slice(0, 14).map(function (x) { return x[1]; }), input.value.trim());
  }

  function setActive(i) {
    var items = $all('.search-item', resultsEl);
    if (!items.length) return;
    activeIdx = (i + items.length) % items.length;
    items.forEach(function (li, j) {
      li.classList.toggle('active', j === activeIdx);
      if (j === activeIdx) {
        input.setAttribute('aria-activedescendant', li.id);
        li.scrollIntoView({ block: 'nearest' });
      }
    });
  }

  function searchKeys(e) {
    if (e.key === 'Escape') { e.preventDefault(); closeSearch(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setActive(activeIdx + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(activeIdx - 1); }
    else if (e.key === 'Enter') {
      var items = $all('.search-item a', resultsEl);
      var target = items[activeIdx] || items[0];
      if (target) { window.location.href = target.href; }
    }
  }

  function openSearch() {
    if (!overlay) return;
    overlay.hidden = false;
    root.classList.add('search-open');
    input.value = '';
    renderResults(POPULAR, '');
    setTimeout(function () { input.focus(); }, 0);
    document.addEventListener('keydown', searchKeys);
  }
  function closeSearch() {
    if (!overlay || overlay.hidden) return;
    overlay.hidden = true;
    root.classList.remove('search-open');
    document.removeEventListener('keydown', searchKeys);
    if (searchBtn) searchBtn.focus();
  }

  if (searchBtn && overlay && input && resultsEl) {
    searchBtn.addEventListener('click', openSearch);
    input.addEventListener('input', runSearch);
    overlay.addEventListener('click', function (e) {
      if (e.target.closest && e.target.closest('[data-close-search]')) closeSearch();
    });
    document.addEventListener('keydown', function (e) {
      var el = document.activeElement;
      var typing = el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
      if ((e.key === 'k' && (e.metaKey || e.ctrlKey)) || (e.key === '/' && !typing)) {
        e.preventDefault();
        if (overlay.hidden) openSearch(); else closeSearch();
      }
    });
  }

  /* ---------------- footer year ---------------- */
  var yr = $('#footer-year');
  if (yr) yr.textContent = String(new Date().getFullYear());
})();
