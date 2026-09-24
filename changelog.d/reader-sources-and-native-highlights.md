### Fixed

- **Kobo highlights can open in the web reader without changing your book format.** Native chapter paths now resolve correctly for nested EPUB packages and escaped filenames. The reader verifies the saved native location in the file it opened, or uses a unique exact passage when the original EPUB lacks Kobo spans. Existing EPUB reading positions and original Kobo annotation data are preserved. Uncertain locations keep their saved text and show “Location unavailable”.
- **Highlights from different devices remain separate when they cover the same passage.** Choose an entry in the highlights drawer to edit that annotation. Deleting one redraws any remaining highlight at the same location.
- **Browser reading sources are clearly separated from physical e-readers.** Source totals and assigned annotation totals are labeled separately, historical untyped annotations remain accessible, and an unidentified browser source is identified explicitly. A device that has never reported its inventory no longer looks like a device that reported zero books.

- **The classic web reader opens its Annotations tab and jumps to the selected passage on the first click.** Both web interfaces reuse the same browser identity for highlights and reading progress, preventing an extra unidentified source merely from switching interfaces.

- **Selecting text opens highlight controls in Safari in both web readers.** A shared parent-side selection observer handles sandboxed books while keeping embedded book scripts disabled and avoiding duplicate controls in other browsers. Canceling and immediately selecting the same passage again also reopens the controls.
