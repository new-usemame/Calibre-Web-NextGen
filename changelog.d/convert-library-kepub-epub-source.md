### Fixed
- **Convert Library sends an epub straight to kepubify.** With kepub as the target format, an epub was first run through ebook-convert epub to epub because the format check compared ".epub" to "epub". The result was the same, just slower and one more step that could fail. When converting another format to epub first does fail, the Convert Library log now includes the tool's own output.
