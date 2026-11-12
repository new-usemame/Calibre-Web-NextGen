### Fixed

- **The translation banner counts real gaps only.** Entries with plural
  forms (for example zh_Hans_CN "Last %(count)s day") keep their
  translations outside the field the banner checked, so fully translated
  plural entries were reported as "still need translating" and the banner
  never cleared. Plural entries are now judged by their first plural form.
