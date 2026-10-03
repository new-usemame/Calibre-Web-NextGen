# Amazon markers in Kindle EPUB repairs

The existing Kindle EPUB Fixer now removes `data-AmznRemoved` attributes from well-formed HTML, XHTML, HTM and SVG entries it has decoded. This Amazon conversion marker can cause EPUBCheck `HTM_061`. Removal runs with the other normal repairs, without enabling aggressive mode or changing the existing enable setting. Existing single-book, library-wide, ingest and EPUB conversion callers all use that processor.

The repair removes the attribute rather than the element: punctuation, images, child elements, CSS classes, namespaces and other attributes remain. Known marker spelling is matched without ASCII case sensitivity. It does not rename or remove unrelated custom data attributes, scan CSS/plain text, rewrite entity definitions, or fetch external entities. Start tags are identified by an XML parser and edits are applied to the original decoded source, without serializing a DOM. Malformed XML is left untouched by this pass and a warning names the entry. Existing uncertain-encoding refusal still applies.

The charset pass retains an existing meta tag's attributes and self-closing structure. A missing inserted charset tag is self-closing so it remains valid XHTML. A leading BOM no longer hides an existing XML declaration and triggers a duplicate declaration.

Backups, checksum updates, run history and repeat-run behavior retain the existing policy. Only changed books are rewritten. Fixer logs report the marker count per changed entry. This repairs a concrete EPUB validation error; it does not guarantee Amazon acceptance of every book or change email delivery.
