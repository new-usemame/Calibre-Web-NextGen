### Fixed

- **OPDS apps that show link titles, such as KOReader, can now tell EPUB and KEPUB downloads apart.** A book with both formats listed two download links that differed only in their URL, so apps showed both as "epub". Each link now carries a `title` of EPUB or KEPUB. The MIME type is unchanged. Thanks @sgreadly for the fix (#1100).
