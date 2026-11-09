### Security

- **The web reader's bundled XML parser is updated.** The EPUB reader library (epubjs) ships with an old copy of `@xmldom/xmldom` (0.7.13) that has several published advisories. Browsers use their own built-in XML parser, so that copy is not normally run, but it is still bundled. CWNG now pins it to 0.8.15, the maintained fix line, so the bundle no longer carries the flagged version and `npm audit` stops reporting it. (#1655)
