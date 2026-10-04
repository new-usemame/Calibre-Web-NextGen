### Fixed

- **EPUB stylesheet text keeps the correct encoding after Kindle repair.** When the fixer writes a stylesheet in UTF-8, its leading CSS encoding declaration now agrees with those bytes. Accented generated text stays readable; repeated repairs leave an already-correct book unchanged.
