### Added

- **Custom column values can be chosen from a list while editing a book.** Editing
  a text custom column — a genre, a Dewey or LCC number — meant typing the value
  from memory, and a dotted hierarchy meant reproducing its exact spelling or
  quietly creating a new, near-duplicate one. Supported text custom columns have a
  list button beside its field that opens a searchable checklist of the values
  already in the library, and applying it fills the field for you. The list is
  shaped to match how the column is stored and browsed: a hierarchical column
  such as `Genre` is drawn as an indented tree where nesting is dot depth, while
  a flat column is drawn as a plain list, so `778.3` stays one entry rather than
  a `778` with a `3` underneath it. Values already on the book arrive ticked, you
  can add a new dotted value from the same dialog, and typing by hand still works
  exactly as before. Single-value columns allow one choice, filtering keeps your
  selections, and existing spelling is preserved when you apply it. Choices
  respect your access to books, independently of personal browse visibility.
  Available in both the new and the classic interface. Thanks @Rol3333 (#2434).
