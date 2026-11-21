### Fixed

- **Kobo sync could fail after a token refresh with the store proxy turned off.** Calibre-Web-NextGen now answers the device's UserStorage metadata read locally, so Kobo can't reject it with a 401. Thanks to @JohanSF for testing the response on a Kobo Libra Colour. ([#2958](https://github.com/new-usemame/Calibre-Web-NextGen/issues/2958))
