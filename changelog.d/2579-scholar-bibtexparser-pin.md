### Fixed

- **The Google Scholar metadata source loads again.** `scholarly` needs the 1.x `bibtexparser`, but nothing capped it, so fresh installs pulled 2.x and the source silently failed to import (`No module named 'bibtexparser.bibdatabase'` in the debug log on every start and upload). `bibtexparser` is now pinned below 2.0. The message was harmless to uploads themselves. (#2579)
