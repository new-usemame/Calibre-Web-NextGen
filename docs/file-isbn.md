# Find an ISBN inside a book

Open **Edit metadata** and choose **Find ISBN in book files** below Identifiers. The scan reads the book’s stored EPUB, KEPUB, PDF or plain-text files and offers checksum-valid ISBN-13s with nearby text and the source format. It prioritizes the beginning and end before the middle, within its scan limits.

Check the edition before choosing **Use ISBN**. A copyright page may list several editions or quote another book’s ISBN. Choosing a suggestion replaces ISBN entries in the current form and preserves other identifiers, such as DOI or provider IDs. Choose **Save changes** to persist it. Scanning and choosing alone do not write metadata.

The scan reports formats it could not read and when it reached a limit. No result means no valid ISBN-13 was found in the scanned text; it does not prove the book has none. Image-only PDF pages require OCR and are not read by this feature. MOBI/AZW3 and Google Drive storage are not supported. The bounded parser runs on Linux servers; other server platforms report that extraction is unavailable. An editor can still enter an identifier manually.
