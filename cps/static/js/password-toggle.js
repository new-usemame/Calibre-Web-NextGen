/* Adds a show/hide button to every password input (issue #2632).
 * Opt out per field with data-pw-toggle="off". Disabled/readonly fields are
 * skipped. Fields added to the DOM later are picked up via MutationObserver. */
(function () {
    "use strict";

    var EYE = '<svg class="cwng-pw-eye" viewBox="0 0 24 24" aria-hidden="true">' +
        '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>' +
        '<svg class="cwng-pw-eye-off" viewBox="0 0 24 24" aria-hidden="true">' +
        '<path d="M17.94 17.94A10.94 10.94 0 0 1 12 20C5 20 1 12 1 12a18.5 18.5 0 0 1 5.06-5.94"/>' +
        '<path d="M9.9 4.24A9.1 9.1 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>' +
        '<path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

    function labels() {
        var t = window.cwngPasswordToggleLabels || {};
        return { show: t.show || "Show password", hide: t.hide || "Hide password" };
    }

    function enhance(input) {
        if (input.dataset.cwngPw === "1" || input.dataset.pwToggle === "off" ||
            input.disabled || input.readOnly || !input.parentNode) {
            return;
        }
        input.dataset.cwngPw = "1";
        var wrap = document.createElement("div");
        wrap.className = "cwng-pw-wrap";
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        input.classList.add("cwng-pw-input");

        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "cwng-pw-toggle";
        btn.setAttribute("aria-pressed", "false");
        btn.setAttribute("aria-label", labels().show);
        btn.title = labels().show;
        btn.innerHTML = EYE;
        btn.addEventListener("click", function () {
            var show = input.type === "password";
            input.type = show ? "text" : "password";
            btn.setAttribute("aria-pressed", show ? "true" : "false");
            var l = show ? labels().hide : labels().show;
            btn.setAttribute("aria-label", l);
            btn.title = l;
        });
        wrap.appendChild(btn);
    }

    function scan(root) {
        var list = (root || document).querySelectorAll('input[type="password"]');
        for (var i = 0; i < list.length; i++) { enhance(list[i]); }
    }

    function init() {
        scan(document);
        if (window.MutationObserver) {
            new MutationObserver(function (muts) {
                for (var i = 0; i < muts.length; i++) {
                    var added = muts[i].addedNodes;
                    for (var j = 0; j < added.length; j++) {
                        var n = added[j];
                        if (n.nodeType !== 1) { continue; }
                        if (n.matches && n.matches('input[type="password"]')) { enhance(n); }
                        else if (n.querySelectorAll) { scan(n); }
                    }
                }
            }).observe(document.body, { childList: true, subtree: true });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
