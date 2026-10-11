### Fixed

- **PDF links to a page open on that page (#2537).** A reader URL like `/app/view/<id>/pdf#page=12` opened the book on page 1, because the new UI dropped the `#…` part before starting the PDF viewer. The viewer now gets it, so bookmarked pages work, along with the other PDF.js options such as `#zoom=page-width` and `#search=word`. Changing the `#page=` in the address bar while the book is open also moves the viewer. Thanks @darkmatterpelican.
