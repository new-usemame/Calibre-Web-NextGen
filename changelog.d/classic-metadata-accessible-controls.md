### Fixed

- **Classic metadata and search controls have clear accessible names.** The Author field, icon actions, bulk-edit fields and custom date ranges have associated or contextual labels. Localized date displays stay out of keyboard navigation and reveal the editable value while typing. Rich-text breadcrumb buttons retain block selection with supported ARIA.

  Classic rich-text comments also initialize and reinitialize without unload-policy errors when Chromium denies the deprecated unload event. The iframe selector cache now resets on pagehide, while form submission and the separate unsaved-edit lifecycle remain unchanged.
