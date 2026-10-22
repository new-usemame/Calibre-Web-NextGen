### Fixed

- **Uploading a book whose filename is all non-ASCII no longer loses it.** A file such as `龙蛋.epub` was staged for import without its extension, so the import step deleted it even though the upload had been reported as queued. The staged copy now keeps the extension, so CJK, Cyrillic and other non-ASCII filenames import normally. The original filename is still recorded as before (#2701, thanks @HernandoR).
