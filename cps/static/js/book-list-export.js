(function () {
  "use strict";
  function init() {
    document.querySelectorAll(".book-list-export").forEach(function (box) {
      if (box.dataset.bound) return;
      box.dataset.bound = "true";
      var buttons = box.querySelectorAll("[data-export-format]");
      var status = box.querySelector('[role="status"]');
      buttons.forEach(function (button) {
        button.addEventListener("click", async function () {
          buttons.forEach(function (item) { item.disabled = true; });
          box.setAttribute("aria-busy", "true"); status.textContent = box.dataset.pending;
          try {
            var payload = { format: button.dataset.exportFormat, source: box.dataset.source, params: JSON.parse(box.dataset.params) };
            if (box.dataset.id) payload.id = Number(box.dataset.id);
            var response = await cwaFetch(box.dataset.url, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
            if (!response.ok) { status.textContent = response.status === 413 ? box.dataset.limit : box.dataset.error; return; }
            var file = await response.blob(); var url = URL.createObjectURL(file);
            var match = /filename="([^"]+)"/i.exec(response.headers.get("Content-Disposition") || "");
            var link = document.createElement("a"); link.href = url;
            link.download = match ? match[1] : "calibre-web-books." + button.dataset.exportFormat;
            document.body.appendChild(link); link.click(); link.remove();
            window.setTimeout(function () { URL.revokeObjectURL(url); }, 30000);
            status.textContent = box.dataset.success;
          } catch (_) { status.textContent = box.dataset.error; }
          finally { buttons.forEach(function (item) { item.disabled = false; }); box.removeAttribute("aria-busy"); }
        });
        button.disabled = false;
      });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
}());
