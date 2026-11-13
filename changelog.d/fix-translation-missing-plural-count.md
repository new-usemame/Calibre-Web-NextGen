### Fixed

- **The "strings still need translating" banner counts what users actually see
  in English.** It used to count every entry with plural forms (for example
  zh_Hans_CN "Last %(count)s day") as missing even when translated, and it
  skipped fuzzy entries, which are left out of the compiled translations. It
  now uses the same count as the translation table in the README.
