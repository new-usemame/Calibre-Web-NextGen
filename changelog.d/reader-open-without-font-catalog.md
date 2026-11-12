### Fixed

- **A book set to a built-in font opens straight away in the web reader.** The reader used to wait for the list of reader fonts before showing the first page. When that list was slow or failed to load, every book sat on a blank reader for several seconds. Built-in fonts need nothing from that list, so the book now opens without it. A book set to an uploaded font still waits, so that font is there on the first page.
