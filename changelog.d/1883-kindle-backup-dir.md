### Fixed

- **The Kindle EPUB Fixer's backup of an original file no longer collapses into a single overwritten file** when its `processed_books/fixed_originals` folder does not exist yet. The destination directory is created before the copy, so every retained original is kept under its own name.
