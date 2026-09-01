/* CWNG #60 — new-UI fallback feedback popup controller.
 *
 * Shows a short, two-step, fully anonymous feedback prompt on the classic page
 * when the SPA shell's capability fallback navigates here with
 * ?cwng_feedback=newui. Only { type, reasons, comment } is POSTed to
 * the feedback endpoint, which stores nothing identifying. See the partial
 * cwng_feedback_popup.html and notes/FEEDBACK-SERVER-DESIGN.md.
 *
 * Vanilla JS, no dependencies — self-contained so it can't collide with caliBlur
 * or the classic page's jQuery/plugins.
 */
(function () {
  "use strict";

  var MARKER = "cwng_feedback"; // ?cwng_feedback=newui
  var overlay = document.getElementById("cwng-fb-overlay");
  if (!overlay) return;

  // ── Should we show it? Only when the switch marker is present. ───────────
  var params;
  try { params = new URLSearchParams(window.location.search); } catch (e) { return; }
  var trigger = params.get(MARKER);
  if (!trigger) return;

  // Per-version suppression: once a user has answered (or declined) for this
  // running version, don't prompt again after subsequent switches — same shape
  // as the "Try the new UI" banner's per-version dismiss.
  var version = overlay.getAttribute("data-version") || "";
  var suppressKey = "cwng_fb_answered_" + version;

  // Always strip the marker from the URL so a refresh/bookmark won't re-trigger.
  function stripMarker() {
    try {
      params.delete(MARKER);
      var qs = params.toString();
      var url = window.location.pathname + (qs ? "?" + qs : "") + window.location.hash;
      window.history.replaceState(null, "", url);
    } catch (e) { /* history unavailable — harmless */ }
  }
  stripMarker();

  try {
    if (window.localStorage && localStorage.getItem(suppressKey) === "1") return;
  } catch (e) { /* storage blocked — just proceed to show once */ }

  var endpoint = overlay.getAttribute("data-endpoint");
  var type = overlay.getAttribute("data-type") || "new_version_feedback";

  var panels = {};
  overlay.querySelectorAll("[data-cwng-fb-step]").forEach(function (el) {
    panels[el.getAttribute("data-cwng-fb-step")] = el;
  });
  var footer = overlay.querySelector("[data-cwng-fb-footer]");
  var anonBox = overlay.querySelector("#cwng-fb-anon");
  var optinNote = overlay.querySelector("[data-cwng-fb-optin-note]");
  var commentEl = overlay.querySelector(".cwng-fb-comment");
  var submitBtn = overlay.querySelector("[data-cwng-fb-submit]");

  var lastFocus = null;

  function showPanel(name) {
    Object.keys(panels).forEach(function (k) { panels[k].hidden = k !== name; });
    // The anonymity footer belongs to the input steps, not to done/error.
    if (footer) footer.hidden = !(name === "reasons" || name === "comment");
  }

  function open() {
    lastFocus = document.activeElement;
    overlay.hidden = false;
    document.body.style.overflow = "hidden";
    showPanel("reasons");
    // Focus the dialog for screen readers / Escape handling.
    var first = overlay.querySelector(".cwng-fb-reason input");
    if (first) { try { first.focus(); } catch (e) {} }
  }

  function markAnswered() {
    try { if (window.localStorage) localStorage.setItem(suppressKey, "1"); } catch (e) {}
  }

  function close() {
    overlay.hidden = true;
    document.body.style.overflow = "";
    // Suppress future prompts for this version only if the user actually engaged
    // (answered, or declined from an input step). Do NOT suppress when closing the
    // error panel — a transient network/Worker failure means no feedback was sent,
    // so the prompt should reappear next time they switch back.
    var errored = panels.error && !panels.error.hidden;
    if (!errored) markAnswered();
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
  }

  function collectReasons() {
    var out = [];
    overlay.querySelectorAll('[data-cwng-fb-step="reasons"] input[type="checkbox"]:checked')
      .forEach(function (c) { out.push(c.value); });
    return out;
  }

  function send() {
    var reasons = collectReasons();
    var comment = (commentEl && commentEl.value ? commentEl.value : "").trim();

    // Honesty about the anonymize toggle: when the user turns it OFF, we append
    // the app version (non-identifying but useful for reproducing issues) to the
    // comment they can see. Nothing else — no name, account, IP, or device — is
    // ever attached, and the server stores only { type, reasons, comment }.
    if (anonBox && !anonBox.checked && version) {
      comment = (comment ? comment + "\n\n" : "") + "[CWNG " + version + "]";
    }

    if (submitBtn) { submitBtn.disabled = true; }

    var payload = JSON.stringify({ type: type, reasons: reasons, comment: comment });
    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      // Anonymity: never attach cookies/credentials to the cross-origin call.
      credentials: "omit",
      mode: "cors",
      keepalive: true
    }).then(function (r) {
      if (submitBtn) { submitBtn.disabled = false; }
      if (r && r.ok) { markAnswered(); showPanel("done"); }
      else { showPanel("error"); }
    }).catch(function () {
      if (submitBtn) { submitBtn.disabled = false; }
      showPanel("error");
    });
  }

  // ── Wiring ──────────────────────────────────────────────────────────────
  overlay.querySelectorAll("[data-cwng-fb-dismiss]").forEach(function (b) {
    b.addEventListener("click", close);
  });
  var nextBtn = overlay.querySelector("[data-cwng-fb-next]");
  if (nextBtn) nextBtn.addEventListener("click", function () {
    showPanel("comment");
    if (commentEl) { try { commentEl.focus(); } catch (e) {} }
  });
  var backBtn = overlay.querySelector("[data-cwng-fb-back]");
  if (backBtn) backBtn.addEventListener("click", function () { showPanel("reasons"); });
  if (submitBtn) submitBtn.addEventListener("click", send);
  var retryBtn = overlay.querySelector("[data-cwng-fb-retry]");
  if (retryBtn) retryBtn.addEventListener("click", send);

  if (anonBox && optinNote) {
    anonBox.addEventListener("change", function () { optinNote.hidden = anonBox.checked; });
  }

  // Backdrop click (outside the card) + Escape close.
  overlay.addEventListener("mousedown", function (e) {
    if (e.target === overlay) close();
  });
  document.addEventListener("keydown", function (e) {
    if (!overlay.hidden && (e.key === "Escape" || e.keyCode === 27)) close();
  });

  open();
})();
