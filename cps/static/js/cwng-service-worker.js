/* Calibre-Web NextGen service worker: keeps the new UI's app shell available
 * offline so the installed web app opens without a network connection.
 *
 * Served by cps/spa.py at <prefix>/app/sw.js, which prepends
 * `self.__CWNG_SW__ = {prefix, build, precache}`. Loading this file directly
 * from /static does nothing useful: a service worker's scope cannot exceed its
 * own path, and the shell lives under /app.
 *
 * What it handles, and nothing else:
 *   - GET <prefix>/static/app/... (the Vite bundle): cache first. Bundle file
 *     names are content-hashed and immutable, so a cached copy is always right.
 *   - Navigations to <prefix>/app and <prefix>/app/...: network first, falling
 *     back to the shell cached at install time when the server is unreachable,
 *     slow, or returns a 5xx from a reverse proxy.
 *
 * Every other request (API, covers, book files, /login, OAuth/OIDC routes,
 * /logout, Kobo, OPDS) is not intercepted at all, so sign-in redirects and API
 * semantics are exactly what they are without a service worker.
 */
'use strict';

(function (self) {
  var CONFIG = self.__CWNG_SW__;
  var CACHE_PREFIX = 'cwng-app-';
  var CACHE = CACHE_PREFIX + CONFIG.build;
  var APP_PATH = CONFIG.prefix + '/app';
  var SHELL_URL = APP_PATH + '/';
  var ASSET_PATH = CONFIG.prefix + '/static/app/';
  // A navigation that takes longer than this on a bad connection gets the
  // cached shell instead of a spinner. The shell then loads its data through
  // the API, which has its own error handling.
  var NAVIGATION_TIMEOUT_MS = 4000;

  function isShellResponse(response) {
    var type = response.headers.get('Content-Type') || '';
    return response.ok && !response.redirected && type.indexOf('text/html') === 0;
  }

  function isAppNavigation(request, url) {
    if (request.mode !== 'navigate') return false;
    var path = url.pathname;
    if (path !== APP_PATH && path.indexOf(SHELL_URL) !== 0) return false;
    // The worker script and manifest are never navigations in practice, but
    // keep them out explicitly so a stale shell can never stand in for them.
    return path !== APP_PATH + '/sw.js' && path !== APP_PATH + '/manifest.webmanifest';
  }

  function precache() {
    return caches.open(CACHE).then(function (cache) {
      // Everything the shell can lazy-load is fetched now, including the reader
      // chunks, so a page never visited online still opens offline.
      return cache.addAll(CONFIG.precache).then(function () {
        return fetch(SHELL_URL, { credentials: 'same-origin', cache: 'no-store' });
      }).then(function (response) {
        if (!isShellResponse(response)) {
          throw new Error('CWNG app shell was not cacheable: HTTP ' + response.status);
        }
        return cache.put(SHELL_URL, response);
      });
    });
  }

  function cachedShell() {
    return caches.open(CACHE).then(function (cache) {
      return cache.match(SHELL_URL);
    });
  }

  function cacheFirst(request) {
    return caches.open(CACHE).then(function (cache) {
      return cache.match(request).then(function (hit) {
        if (hit) return hit;
        return fetch(request).then(function (response) {
          if (response.ok && response.type === 'basic') {
            cache.put(request, response.clone());
          }
          return response;
        });
      });
    });
  }

  function networkFirstShell(request) {
    return new Promise(function (resolve, reject) {
      var settled = false;
      function finish(response) {
        if (!settled) {
          settled = true;
          resolve(response);
        }
      }
      function fallback(error) {
        return cachedShell().then(function (shell) {
          if (shell) {
            finish(shell);
          } else if (error) {
            if (!settled) {
              settled = true;
              reject(error);
            }
          }
          return shell;
        });
      }

      var network = fetch(request);
      var timer = setTimeout(function () {
        // Too slow: answer from cache if we can. If there is no cached shell,
        // keep waiting for the network rather than failing early.
        fallback(null);
      }, NAVIGATION_TIMEOUT_MS);

      network.then(function (response) {
        clearTimeout(timer);
        // A reverse proxy answers 502/503/504 while CWNG itself is down; that
        // is "offline" as far as the reader is concerned. Redirects (e.g. to
        // sign-in) and 4xx are passed through untouched.
        if (response.status >= 500) {
          return fallback(null).then(function (shell) {
            if (!shell) finish(response);
          });
        }
        finish(response);
      }, function (error) {
        clearTimeout(timer);
        return fallback(error);
      });
    });
  }

  self.addEventListener('install', function (event) {
    event.waitUntil(precache().then(function () {
      // Take over at once: an installed web app is rarely fully closed, so
      // waiting for every client to go away would pin an old build for days.
      return self.skipWaiting();
    }));
  });

  self.addEventListener('activate', function (event) {
    event.waitUntil(caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (key) {
        return key.indexOf(CACHE_PREFIX) === 0 && key !== CACHE;
      }).map(function (key) {
        return caches.delete(key);
      }));
    }).then(function () {
      return self.clients.claim();
    }));
  });

  self.addEventListener('fetch', function (event) {
    var request = event.request;
    if (request.method !== 'GET') return;
    var url = new URL(request.url);
    if (url.origin !== self.location.origin) return;
    if (url.pathname.indexOf(ASSET_PATH) === 0) {
      event.respondWith(cacheFirst(request));
    } else if (isAppNavigation(request, url)) {
      event.respondWith(networkFirstShell(request));
    }
  });
})(self);
