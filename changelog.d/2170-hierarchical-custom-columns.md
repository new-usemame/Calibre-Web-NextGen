### Added

- **Custom columns that use Calibre's dotted sub-groups can be browsed as a tree in the classic UI
  and over OPDS.** A value like `Computers.DB.Oracle` now shows under Computers › DB › Oracle, and
  opening a level lists its books together with everything filed beneath it. Each such column
  gets its own sidebar entry, which you can switch off per column on your profile page, and appears
  in the OPDS catalog root. Columns whose values merely contain a dot (Dewey `778.3`) stay flat.
  Contributed by @Rol3333.
