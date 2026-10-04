# EPUB stylesheet encoding after Kindle repair

The Kindle EPUB Fixer already decodes supported text entries and normally writes them in UTF-8. A lower-case CSS entry could retain a declaration such as `@charset "iso-8859-1";` after its bytes became UTF-8. A reader using that declaration could then interpret an accented character incorrectly.

The fixer now synchronizes an existing leading CSS encoding declaration with the output encoding it selected. It recognizes the strict leading declaration syntax described by [CSS Syntax](https://www.w3.org/TR/css-syntax-3/#input-byte-stream); comments, leading whitespace, single-quoted lookalikes and ordinary CSS contents are preserved. It does not insert a declaration into an undeclared stylesheet. Existing encoding detection, UTF-16 with a byte-order mark, and unsupported or undecodable entries retain their previous handling. Upper-case CSS ZIP names keep the existing verbatim policy.

The full writer is checked with Latin-1, Windows-1252, ASCII and UTF-8 BOM fixtures. Tests compare complete stylesheet bytes, preserve unrelated ZIP entries and check that the second run makes no backup, checksum or timestamp change. UTF-8 and UTF-16 controls remain byte-identical. These synthetic writer checks establish declaration/byte coherence, not every reader's encoding precedence or behavior with every possible malformed stylesheet.
