### Fixed

- **Higher-resolution Kindle covers:** The cover picker and cover-resolution upgrade now try Amazon's original `MAIN._SCRM_` image before the existing `SL2000` variant. This matches the higher-resolution image used by Calibre's Kindle High-res Covers plugin while keeping the prior image available as a fallback and preserving the Amazon-CDN kill switch.
