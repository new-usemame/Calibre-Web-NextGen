/* File scans only suggest; the existing metadata form owns persistence. */
(function () {
  "use strict";
  function init() {
    var box = document.getElementById("file-isbn-suggestions");
    if (!box) return;
    var scan = box.querySelector(".file-isbn-scan");
    var status = box.querySelector(".file-isbn-status");
    var results = box.querySelector(".file-isbn-results");
    function text(key, value, name) {
      return (box.dataset[key] || "").replace("{" + (name || "formats") + "}", value || "");
    }
    function paragraph(value, parent) {
      var p = document.createElement("p"); p.textContent = value;
      (parent || results).appendChild(p); return p;
    }
    function apply(isbn) {
      var aliases = ["isbn", "isbn10", "isbn_10", "isbn13", "isbn_13"];
      var rows = Array.from(document.querySelectorAll("#identifier-table tbody tr"));
      var isbnRows = rows.filter(function (row) {
        var input = row.querySelector(".identifier-type");
        return input && aliases.indexOf(input.value.trim().toLowerCase()) !== -1;
      });
      var row = isbnRows[0];
      if (!row) {
        document.getElementById("add-identifier-line").click();
        row = document.querySelector("#identifier-table tbody tr:last-child");
      }
      if (!row) return;
      isbnRows.slice(1).forEach(function (extra) { extra.remove(); });
      row.querySelector(".identifier-type").value = "isbn";
      var value = row.querySelector(".identifier-val"); value.value = isbn;
      value.dispatchEvent(new Event("input", { bubbles: true }));
      status.textContent = text("added");
    }
    scan.addEventListener("click", async function () {
      scan.disabled = true; box.setAttribute("aria-busy", "true");
      results.replaceChildren(); status.textContent = text("loading");
      try {
        var response = await cwaFetch(box.dataset.url, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        var data = await response.json();
        if (!response.ok) {
          var code = data.error && data.error.code;
          status.textContent = text(code === "unsupported_storage" ? "storage" : code === "unsupported_platform" ? "platform" : "error");
          return;
        }
        status.textContent = data.candidates.length ? text("found", String(data.candidates.length), "n") : text(data.available ? "empty" : "unavailable");
        [["scanned", data.scanned_formats], ["unsupported", data.unsupported_formats], ["missing", data.unavailable_formats], ["failed", data.failed_formats]].forEach(function (item) {
          if (item[1] && item[1].length) paragraph(text(item[0], item[1].join(", ")));
        });
        if (data.truncated) paragraph(text("truncated"));
        data.candidates.forEach(function (candidate) {
          var entry = document.createElement("div"); entry.className = "file-isbn-candidate";
          var number = document.createElement("strong"); number.textContent = candidate.isbn + " · " + candidate.format; entry.appendChild(number);
          paragraph(candidate.context, entry).dir = "auto";
          var use = document.createElement("button"); use.type = "button"; use.className = "btn btn-default";
          use.textContent = text("use", candidate.isbn, "isbn");
          use.addEventListener("click", function () { apply(candidate.isbn); });
          entry.appendChild(use); results.appendChild(entry);
        });
      } catch (_) { status.textContent = text("error"); }
      finally { scan.disabled = false; box.removeAttribute("aria-busy"); }
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
}());
