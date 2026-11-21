/* Calibre-Web Automated - fork of Calibre-Web
 * Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Support pack / debug pack download feedback for the classic Admin page
 * (fork issue #2728).
 *
 * Building a pack takes a while, and a plain link gives no sign that a click is
 * doing anything, so people click again. This keeps the normal download but
 * shows "Preparing..." until it starts, and stops a second click.
 *
 * How it knows the download started, without holding the file in memory (saving
 * from a blob: URL is blocked by this app's CSP in Firefox, #2710): the page
 * makes up a random token, requests the normal download URL in a hidden iframe
 * with ?dl_token=<token>, and watches for the cwng_pack_token cookie that the
 * server sets, with that token, only on a successful download. An iframe means
 * a failure (busy, 500, a proxy 504) loads into the hidden frame and cannot
 * replace the page; its text says why. Without JavaScript the link still works.
 */
(function (root) {
  "use strict";

  var COOKIE = "cwng_pack_token";
  var POLL_MS = 250;
  var GRACE_MS = 400;         // after the frame loads, wait for the cookie to land
  var GIVE_UP_MS = 150000;
  var FRAME_REMOVE_MS = 60000; // never pull the frame out from under a running download
  var DONE_MESSAGE_MS = 8000;
  var TOKEN_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-";

  function cookieValue(cookieString, name) {
    var parts = String(cookieString || "").split(";");
    for (var i = 0; i < parts.length; i += 1) {
      var piece = parts[i].replace(/^\s+/, "");
      if (piece.indexOf(name + "=") === 0) {
        return piece.slice(name.length + 1);
      }
    }
    return null;
  }

  // A correlation id, not a secret: 22 URL-safe characters (the server accepts 8-64).
  function makeToken(random) {
    var token = "";
    for (var i = 0; i < 22; i += 1) {
      token += TOKEN_ALPHABET.charAt(Math.floor(random() * TOKEN_ALPHABET.length));
    }
    return token;
  }

  function withToken(url, token) {
    return url + (url.indexOf("?") === -1 ? "?" : "&") + "dl_token=" + token;
  }

  // What the hidden frame loaded when no download started: the server's 429 JSON
  // ("already being prepared"), or anything else (500, a proxy's 504 page, a login page).
  function interpretFrame(text) {
    try {
      var data = JSON.parse(text);
      if (data && data.busy === true) {
        return { kind: "busy", message: data.message || null };
      }
    } catch (ignored) {
      // not JSON: an HTML error page, or nothing at all
    }
    return { kind: "failed", message: null };
  }

  /* The protocol, with every browser dependency passed in so it can be tested.
   * env: token, cookies(), clearCookie(name), setInterval, clearInterval,
   *      setTimeout, clearTimeout, openFrame(src, onLoad) -> {text()},
   *      removeFrameLater(frame)
   * handlers: onDone(), onError(kind, message) with kind busy | failed | timeout */
  function startPackDownload(env, url, handlers) {
    var finished = false;
    var poll = null;
    var giveUp = null;
    var frame = null;

    function signalled() {
      return cookieValue(env.cookies(), COOKIE) === env.token;
    }
    function stop() {
      finished = true;
      env.clearInterval(poll);
      env.clearTimeout(giveUp);
      env.clearCookie(COOKIE);
      if (frame) {
        env.removeFrameLater(frame);
      }
    }
    function done() {
      if (finished) { return; }
      stop();
      handlers.onDone();
    }
    function fail(kind, message) {
      if (finished) { return; }
      stop();
      handlers.onError(kind, message);
    }

    poll = env.setInterval(function () {
      if (!finished && signalled()) { done(); }
    }, POLL_MS);
    giveUp = env.setTimeout(function () { fail("timeout", null); }, GIVE_UP_MS);
    frame = env.openFrame(withToken(url, env.token), function () {
      // A successful attachment download normally never fires "load". If it does
      // (some browsers), the cookie, set with the same response, decides.
      env.setTimeout(function () {
        if (finished) { return; }
        if (signalled()) { done(); return; }
        var verdict = interpretFrame(frame.text());
        fail(verdict.kind, verdict.message);
      }, GRACE_MS);
    });
  }

  function init(doc, win) {
    var status = doc.getElementById("pack_status");
    var buttons = Array.prototype.slice.call(doc.querySelectorAll("[data-pack-download]"));
    if (!status || !buttons.length) { return; }
    var running = false;
    var clearTimer = null;

    function say(text, level) {
      status.textContent = text;
      status.className = level ? "pack-status text-" + level : "pack-status";
    }
    function lock(on) {
      buttons.forEach(function (button) {
        if (on) { button.classList.add("disabled"); } else { button.classList.remove("disabled"); }
        button.setAttribute("aria-disabled", on ? "true" : "false");
      });
    }
    function message(name) {
      return status.getAttribute("data-" + name) || "";
    }
    function randomSource() {
      var cryptoApi = win.crypto;
      if (cryptoApi && cryptoApi.getRandomValues) {
        return function () { return cryptoApi.getRandomValues(new Uint32Array(1))[0] / 4294967296; };
      }
      return Math.random;
    }
    function buildEnv() {
      return {
        token: makeToken(randomSource()),
        cookies: function () { return doc.cookie; },
        clearCookie: function (name) { doc.cookie = name + "=; Max-Age=0; path=/"; },
        setInterval: function (fn, ms) { return win.setInterval(fn, ms); },
        clearInterval: function (id) { win.clearInterval(id); },
        setTimeout: function (fn, ms) { return win.setTimeout(fn, ms); },
        clearTimeout: function (id) { win.clearTimeout(id); },
        openFrame: function (src, onLoad) {
          var node = doc.createElement("iframe");
          node.setAttribute("aria-hidden", "true");
          node.setAttribute("tabindex", "-1");
          node.style.display = "none";
          node.addEventListener("load", onLoad);
          node.src = src;
          doc.body.appendChild(node);
          return {
            node: node,
            text: function () {
              try { return node.contentDocument.body.textContent || ""; } catch (ignored) { return ""; }
            }
          };
        },
        removeFrameLater: function (frame) {
          win.setTimeout(function () {
            if (frame.node.parentNode) { frame.node.parentNode.removeChild(frame.node); }
          }, FRAME_REMOVE_MS);
        }
      };
    }

    buttons.forEach(function (button) {
      button.addEventListener("click", function (event) {
        // Let "open in new tab" and friends do what they always did.
        if (event.defaultPrevented || event.button > 0 || event.metaKey || event.ctrlKey ||
            event.shiftKey || event.altKey) {
          return;
        }
        event.preventDefault();
        if (running) { return; }
        running = true;
        win.clearTimeout(clearTimer);
        lock(true);
        say(message("preparing"), "info");
        startPackDownload(buildEnv(), button.getAttribute("href"), {
          onDone: function () {
            running = false;
            lock(false);
            say(message("done"), "success");
            clearTimer = win.setTimeout(function () { say("", ""); }, DONE_MESSAGE_MS);
          },
          onError: function (kind) {
            running = false;
            lock(false);
            say(message(kind === "busy" ? "busy" : kind === "timeout" ? "timeout" : "failed"), "danger");
          }
        });
      });
    });
  }

  var api = {
    cookieValue: cookieValue,
    makeToken: makeToken,
    withToken: withToken,
    interpretFrame: interpretFrame,
    startPackDownload: startPackDownload,
    init: init
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (typeof document !== "undefined") {
    root.CwngSupportPack = api;
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () { init(document, root); });
    } else {
      init(document, root);
    }
  }
})(typeof window !== "undefined" ? window : this);
