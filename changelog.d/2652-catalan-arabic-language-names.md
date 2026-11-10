### Fixed

- **Book languages show their names in the Catalan and Arabic interface.** Both interfaces shipped without a table of language names, so every book's language read "Unknown" on the book page and in the Languages list. They now have complete tables, translated wherever iso-codes has a translation (Català, Espanyol, Francès; العربية) and in English otherwise, and a test fails if a new interface language is ever added without one. Thanks @Tofax for finding the cause. (#2652)
