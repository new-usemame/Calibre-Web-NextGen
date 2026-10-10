# Amazon markers in Kindle EPUB repairs

The existing Kindle EPUB Fixer now removes `data-AmznRemoved` attributes from well-formed HTML, XHTML, HTM and SVG entries it has decoded, including uppercase or mixed-case filename extensions. This Amazon conversion marker can cause EPUBCheck `HTM_061`. Removal runs with the other normal repairs, without enabling aggressive mode or changing the existing enable setting. Existing single-book, library-wide, ingest and EPUB conversion callers all use that processor.

The repair removes the attribute rather than the element: punctuation, images, child elements, CSS classes, namespaces and other attributes remain. Known marker spelling is matched without ASCII case sensitivity. It does not rename or remove unrelated custom data attributes, scan CSS/plain text, rewrite entity definitions, or fetch external entities. Start tags are identified by an XML parser and edits are applied to the original decoded source, without serializing a DOM. Malformed XML, including unbound namespace prefixes, is left untouched by this pass and a warning names the entry. Existing uncertain-encoding refusal still applies.

For the four marker-bearing markup extensions (.html/.xhtml/.htm/.svg), filename recognition follows the same case-insensitive policy when reading and fixing declarations, so recoding cannot leave an uppercase chapter with an obsolete encoding declaration. An existing XML declaration is updated even when XHTML uses an HTML/HTM filename. The charset pass retains an existing meta tag's attributes and self-closing structure. A missing inserted charset tag is self-closing so it remains valid XHTML. A leading BOM no longer hides an existing XML declaration and triggers a duplicate declaration.

Backups, checksum updates, run history and repeat-run behavior retain the existing policy. Only changed books are rewritten. Fixer logs report the marker count per changed entry. This repairs a concrete EPUB validation error; it does not guarantee Amazon acceptance of every book or change email delivery.

Single-book runs also save their history after a repair or an unchanged repeat run. The command's output path is stored as text at the history boundary, so a filesystem path object cannot cause the command to fail after it has already repaired the book.

Single-book manual rewrites require an administrator and a valid CSRF token, matching the EPUB Fixer service page. The existing page sends that token through its normal request helper. Anonymous browsing and ordinary reading permissions do not authorize a shared library rewrite.
