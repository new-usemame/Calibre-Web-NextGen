### Added
- **Design a cover.** Books that arrive without one no longer have to stay a grey placeholder. The
  cover picker gains a "Design a cover" panel that builds a typographic cover from the book's own
  title, series and author: five colour schemes, two lettering styles and three arrangements, with
  named presets and a live preview. Covers are rendered on the server — by Calibre's own cover
  generator where Calibre is installed, and by a built-in renderer otherwise — so the preview and
  the cover that gets saved are the same picture. Personal covers offer the same designs.
- **Automatic covers for coverless imports.** Admin → Basic Configuration gains a default cover
  design, and an opt-in "Design a cover for books that arrive without one" that gives newly imported
  coverless books a real cover during ingest and enforcement. Books that already have a cover are
  never touched.
