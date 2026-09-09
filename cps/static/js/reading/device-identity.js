/* Browser identity shared with the SPA deviceIdentity.ts storage contract.
 * Only reading-data writes call this helper. Storage denial keeps the existing
 * unidentified-source fallback; visiting the reader does not register a device.
 */
(function () {
    "use strict";
    var key = "cwng.webreader.installation-id.v1";
    var uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
    window.webreaderDeviceHeaders = function (base) {
        var headers = new Headers(base);
        try {
            var id = window.localStorage.getItem(key);
            if (!id || !uuid.test(id)) {
                if (!window.crypto || typeof window.crypto.randomUUID !== "function") { return headers; }
                id = window.crypto.randomUUID();
                window.localStorage.setItem(key, id);
            }
            headers.set("X-CWNG-Webreader-Installation-Id", id);
        } catch (e) { /* Storage unavailable: preserve the reading-data write. */ }
        return headers;
    };
})();
