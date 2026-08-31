### Fixed

- **The catalog no longer gets stuck showing one oversized book per row in
  Safari.** A transient browser layout could report one full-width grid column
  even when the available space fit a complete row, causing the catalog to load
  only two books and preserve that incorrect layout. Column measurements now
  have to agree with the grid width, card minimum, and gap before they can
  control pagination.
