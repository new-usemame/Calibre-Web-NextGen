### Fixed

- **The same book catalogued under two different titles is now found as a
  duplicate.** Sources disagree about whether to append the series, the volume
  or the imprint, so a library ends up holding "Golden Son" and "Golden Son
  (Red Rising Series Book 2)", or "The Road" and "The Road (Vintage
  International)", as unrelated books. Duplicate detection now compares titles
  with that annotation set aside, and treats "Liu, Cixin" and "Cixin Liu" as
  one author, so those pairs group. On a 202-book test library the scan found
  17 additional duplicate pairs and lost none.
- **Two volumes of a series are no longer reported as duplicates of each
  other.** When Calibre kept the volume only in `series_index`, both books
  carried one identical title and grouped together — and resolving that group
  would have deleted a book the library only had one copy of. A volume check
  now splits them apart, and a copy that declares no volume at all inside a
  title that spans several is left out of every group rather than offered for
  deletion.
