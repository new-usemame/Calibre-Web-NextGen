### Fixed

- **Saving a PDF from the reader now keeps the book's name.** The PDF reader's Save button always wrote `document.pdf`, because the page it loads the file from has no `.pdf` in its address and the server gave it no filename. The reader now sends the same "Title - Author" name the Download link uses, made safe for file systems (#2729).
