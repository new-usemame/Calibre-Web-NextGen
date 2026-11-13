### Fixed

- **An uploaded book whose file has no title of its own is now titled from its
  file name, not from an internal upload name.** Uploads are saved for import
  under a unique name such as `new_1_20261113_112627_417824_Rig Doc.docx`, and
  Calibre titles a file with no embedded title from its name, so a plain
  `.txt`, `.docx`, `.rtf` or untitled PDF uploaded from the web UI came in as
  "new 1 20261113 112627 417824 Rig Doc". Ingest now shows Calibre the name
  you uploaded. Files dropped into the ingest folder keep their own names as
  before. Books already imported keep their titles; edit them to fix.
