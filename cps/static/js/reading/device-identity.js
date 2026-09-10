/* Compatibility for cached reader pages. Browser attribution is account-wide. */
(function () {
    "use strict";
    window.webreaderDeviceHeaders = function (base) { return new Headers(base); };
})();
