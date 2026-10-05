# Uploaded EPUB reader fonts

Administrators can open **New UI → Admin → Reader fonts** to upload a TTF, OTF, WOFF or WOFF2 file. Classic Admin links to the same management screen. An optional display name makes the choice easier to recognize; otherwise the font’s family name is used. Choose fonts you are allowed to share with this server’s readers.

The uploaded choice becomes available to all users in both EPUB readers. In New UI, open **Reading appearance → Font family**; in Classic, open the reader settings and choose the font. Each signed-in user’s selection is saved across books and reloads. Existing font choices remain available. PDF readers do not use this catalog.

Each upload is a single font face. Browsers can synthesize bold or italic for that face. Upload distinct faces with distinct display names if readers should choose between them; this first version does not group multiple files into a family. Uploading identical bytes returns the existing choice and keeps its original name.

Files live in `CONFIG_DIR/reader-fonts`, in the persistent configuration volume. Preserve that directory with your configuration backups. There is no external font service or new package dependency. Each file must be at most 8 MiB; the catalog supports 32 fonts and 128 MiB total. The server checks actual font tables using the Calibre parser before publishing a choice; changing a file extension cannot bypass validation. Resource-limited upload validation requires a POSIX server with the shipped Calibre binary. An unavailable validator refuses new uploads rather than accepting unvalidated bytes.

**Remove** asks for confirmation and removes the shared choice for everyone. Readers using that choice return to Book default when they next open a book. An already open book may keep a font already loaded in the browser. Removal does not rewrite all user profiles, and a newly uploaded file receives a new identity even if it has the old display name.
