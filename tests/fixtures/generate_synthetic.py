#!/usr/bin/env python3
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""
Generate synthetic test ebook files for edge case testing.

Creates minimal valid and intentionally corrupted files to test
error handling, file validation, and edge cases.

Usage:
    python generate_synthetic.py
"""

import zipfile
from pathlib import Path
import sys
import io


def create_minimal_epub(output_path: Path) -> None:
    """
    Create the smallest possible valid EPUB file (~2-3 KB).
    
    This file has the absolute minimum structure required by the EPUB spec:
    - mimetype file
    - META-INF/container.xml
    - content.opf (package document)
    - Single HTML content file
    """
    print(f"Creating minimal valid EPUB: {output_path.name}")
    
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as epub:
        # 1. mimetype (MUST be first, MUST be uncompressed)
        epub.writestr(
            'mimetype',
            'application/epub+zip',
            compress_type=zipfile.ZIP_STORED  # No compression!
        )
        
        # 2. META-INF/container.xml (points to content.opf)
        epub.writestr('META-INF/container.xml', '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>''')
        
        # 3. content.opf (package document with minimal metadata)
        epub.writestr('content.opf', '''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">test-minimal-001</dc:identifier>
    <dc:title>Minimal Test Book</dc:title>
    <dc:creator>CWA Test Suite</dc:creator>
    <dc:language>en</dc:language>
    <dc:date>2025-01-01</dc:date>
  </metadata>
  <manifest>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="content"/>
  </spine>
</package>''')
        
        # 4. content.xhtml (minimal HTML content)
        epub.writestr('content.xhtml', '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Test Content</title>
</head>
<body>
  <h1>Minimal Test Book</h1>
  <p>This is a minimal valid EPUB file for testing purposes.</p>
  <p>It contains only the required structural elements.</p>
</body>
</html>''')
    
    size = output_path.stat().st_size
    print(f"  ✓ Created ({size:,} bytes)")


def create_corrupted_epub(output_path: Path) -> None:
    """
    Create an invalid EPUB that looks like a ZIP but has corrupted structure.
    
    This tests error handling when processing malformed files.
    """
    print(f"Creating corrupted EPUB: {output_path.name}")
    
    with open(output_path, 'wb') as f:
        # Write ZIP magic bytes followed by random data
        f.write(b'PK\x03\x04')  # ZIP local file header signature
        f.write(b'\x00' * 10)   # Partial header
        f.write(b'CORRUPTED_DATA_NOT_A_VALID_ZIP_STRUCTURE' * 10)
        f.write(b'\xFF' * 100)  # More garbage
    
    size = output_path.stat().st_size
    print(f"  ✓ Created ({size:,} bytes)")


def create_empty_file(output_path: Path) -> None:
    """Create a 0-byte file to test empty file handling."""
    print(f"Creating empty file: {output_path.name}")
    output_path.touch()
    print(f"  ✓ Created (0 bytes)")


def create_huge_filename_epub(output_dir: Path) -> None:
    """
    Create an EPUB with a filename that's exactly at the truncation limit.
    
    Tests filename length handling (ingest_processor truncates at 150 chars).
    """
    # Create filename that's 160 characters (should be truncated to 150)
    base_name = "a" * 155  # Will be truncated to 145 + ".epub" = 150
    filename = f"{base_name}.epub"
    output_path = output_dir / filename
    
    print(f"Creating huge filename EPUB: {filename[:50]}...{filename[-20:]}")
    
    # Create a minimal valid EPUB with this long filename
    create_minimal_epub(output_path)
    
    print(f"  ✓ Filename length: {len(filename)} characters")


def create_special_chars_epub(output_dir: Path) -> None:
    """
    Create an EPUB with special characters in filename.
    
    Tests path handling with spaces, unicode, and special chars.
    """
    filename = "test_special_chars !@#$%^&()_+-={}[].epub"
    output_path = output_dir / filename
    
    print(f"Creating special chars EPUB: {filename}")
    
    try:
        create_minimal_epub(output_path)
    except Exception as e:
        print(f"  ✗ Failed (filesystem may not support these chars): {e}")


def create_international_chars_epub(output_dir: Path) -> None:
    """
    Create an EPUB with international/unicode characters in filename.
    
    Tests handling of diacritics, umlauts, and accents common in:
    - German (äöüß)
    - French (éèêë)
    - Spanish (áéíóú ñ)
    - Portuguese (ãõç)
    - Nordic languages (åøæ)
    - Eastern European (ąćęłńśźż)
    
    This is critical for international users whose filenames contain
    these characters in author names, book titles, etc.
    """
    filename = "test_international_äöüß_éèêë_áéíóú_ñ_åøæ_książka.epub"
    output_path = output_dir / filename
    
    print(f"Creating international chars EPUB: {filename}")
    
    try:
        create_minimal_epub(output_path)
        print(f"  ✓ Successfully created with international characters")
    except Exception as e:
        print(f"  ✗ Failed (filesystem may not support these chars): {e}")


def _create_directional_epub(
    output_path: Path,
    *,
    book_id: str,
    title: str,
    language: str,
    marker_prefix: str,
    direction: str | None,
    vertical: bool,
) -> None:
    """
    Build a small, valid EPUB 3 whose spine direction is explicit (#1303).

    `direction` becomes `page-progression-direction` on <spine>, or is omitted
    entirely when None — that omission is the ordinary left-to-right case, and
    is the shape most real books ship. It is the only place epub.js reads the
    reading direction from (`Packaging.parse` assigns it to `metadata.direction`).

    Three sections with distinct ASCII markers, so a test can assert that a page
    turn moved FORWARD through the spine rather than merely that something
    happened. RTL and LTR fixtures come from this one builder so the pair cannot
    drift apart and quietly stop being a matched control.
    """
    spine_attr = f' page-progression-direction="{direction}"' if direction else ''
    print(f"Creating {direction or 'default (ltr)'} EPUB: {output_path.name}")

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as epub:
        epub.writestr('mimetype', 'application/epub+zip',
                      compress_type=zipfile.ZIP_STORED)

        epub.writestr('META-INF/container.xml', '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>''')

        epub.writestr('content.opf', f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{book_id}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>CWA Test Suite</dc:creator>
    <dc:language>{language}</dc:language>
    <dc:date>2025-01-01</dc:date>
    <meta property="dcterms:modified">2025-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch3" href="ch3.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine{spine_attr}>
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
    <itemref idref="ch3"/>
  </spine>
</package>''')

        epub.writestr('nav.xhtml', '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body>
  <nav epub:type="toc" id="toc">
    <ol>
      <li><a href="ch1.xhtml">First Section</a></li>
      <li><a href="ch2.xhtml">Second Section</a></li>
      <li><a href="ch3.xhtml">Third Section</a></li>
    </ol>
  </nav>
</body>
</html>''')

        style = 'html { writing-mode: vertical-rl; }' if vertical else ''
        sections = (
            ('ch1', 'First Section'),
            ('ch2', 'Second Section'),
            ('ch3', 'Third Section'),
        )
        for index, (name, section_title) in enumerate(sections, start=1):
            epub.writestr(f'{name}.xhtml', f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="{language}" xml:lang="{language}">
<head>
  <title>{section_title}</title>
  <style>{style}</style>
</head>
<body>
  <h1>{marker_prefix}-{index}</h1>
  <p>{section_title}</p>
</body>
</html>''')

    size = output_path.stat().st_size
    print(f"  \u2713 Created ({size:,} bytes)")


def create_rtl_epub(output_path: Path) -> None:
    """Right-to-left spine, vertical Japanese text — the #1303 reproduction."""
    _create_directional_epub(
        output_path,
        book_id='test-rtl-vertical-001',
        title='RTL Vertical Sample',
        language='ja',
        marker_prefix='RTL-SECTION',
        direction='rtl',
        vertical=True,
    )


def create_ltr_epub(output_path: Path) -> None:
    """
    The matched left-to-right control for #1303.

    Deliberately omits `page-progression-direction` rather than setting "ltr":
    an absent attribute is what ordinary books ship, and it is the case a
    direction-aware reader must leave alone. Having a deterministic LTR book
    lets the e2e control assert what the two zones DO, not merely where their
    labels sit — a reader that flipped every book's page turns while keeping
    the labels conditional would otherwise pass.
    """
    _create_directional_epub(
        output_path,
        book_id='test-ltr-horizontal-001',
        title='LTR Horizontal Sample',
        language='en',
        marker_prefix='LTR-SECTION',
        direction=None,
        vertical=False,
    )


def create_missing_mimetype_epub(output_path: Path) -> None:
    """
    Create an EPUB without mimetype file (invalid but structurally ZIP-valid).

    Tests validation logic that checks for required EPUB components.
    """
    print(f"Creating EPUB without mimetype: {output_path.name}")
    
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as epub:
        # Skip mimetype entirely (invalid EPUB but valid ZIP)
        
        epub.writestr('META-INF/container.xml', '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>''')
        
        epub.writestr('content.opf', '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Invalid EPUB - No Mimetype</dc:title>
    <dc:creator>Test</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="content"/></spine>
</package>''')
        
        epub.writestr('content.xhtml', '''<?xml version="1.0"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Test</title></head>
<body><p>Invalid EPUB without mimetype.</p></body>
</html>''')
    
    size = output_path.stat().st_size
    print(f"  ✓ Created ({size:,} bytes)")


def create_epub_with_metadata(output_path: Path) -> None:
    """
    Create an EPUB with rich metadata for metadata parsing tests.
    
    Includes ISBN, publisher, description, tags, series, etc.
    """
    print(f"Creating EPUB with rich metadata: {output_path.name}")
    
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as epub:
        epub.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        
        epub.writestr('META-INF/container.xml', '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>''')
        
        # Rich metadata in content.opf
        epub.writestr('content.opf', '''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="bookid" opf:scheme="ISBN">978-1234567890</dc:identifier>
    <dc:title>The Complete Test Book</dc:title>
    <dc:creator opf:role="aut">Test Author</dc:creator>
    <dc:creator opf:role="edt">Test Editor</dc:creator>
    <dc:publisher>Test Publisher</dc:publisher>
    <dc:date>2025-01-01</dc:date>
    <dc:language>en</dc:language>
    <dc:subject>Fiction</dc:subject>
    <dc:subject>Testing</dc:subject>
    <dc:subject>Software Engineering</dc:subject>
    <dc:description>A comprehensive test book with rich metadata including ISBN, publisher, multiple authors, tags, series information, and description. Used for testing metadata parsing and enforcement.</dc:description>
    <meta name="calibre:series" content="Test Series"/>
    <meta name="calibre:series_index" content="1"/>
    <meta name="calibre:rating" content="5"/>
  </metadata>
  <manifest>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="content"/>
  </spine>
</package>''')
        
        epub.writestr('content.xhtml', '''<?xml version="1.0"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Test Content</title></head>
<body>
  <h1>The Complete Test Book</h1>
  <p>This EPUB contains rich metadata for testing.</p>
  <ul>
    <li>ISBN: 978-1234567890</li>
    <li>Publisher: Test Publisher</li>
    <li>Tags: Fiction, Testing, Software Engineering</li>
    <li>Series: Test Series #1</li>
  </ul>
</body>
</html>''')
    
    size = output_path.stat().st_size
    print(f"  ✓ Created ({size:,} bytes)")


def main():
    """Main entry point."""
    print("=" * 70)
    print("CWA Synthetic Test File Generator")
    print("Creating minimal and edge-case test files")
    print("=" * 70)
    
    # Get output directory
    script_dir = Path(__file__).parent
    output_dir = script_dir / "sample_books"
    output_dir.mkdir(exist_ok=True)
    
    print(f"\n📂 Output directory: {output_dir}\n")
    
    # Generate all synthetic files
    files_created = []
    
    try:
        # 1. Minimal valid EPUB (smallest possible)
        path = output_dir / "test_minimal_valid.epub"
        create_minimal_epub(path)
        files_created.append(path)
        
        # 2. Corrupted EPUB (looks like ZIP but isn't)
        path = output_dir / "test_corrupted.epub"
        create_corrupted_epub(path)
        files_created.append(path)
        
        # 3. Empty file (0 bytes)
        path = output_dir / "test_empty.epub"
        create_empty_file(path)
        files_created.append(path)
        
        # 4. Huge filename (tests truncation)
        create_huge_filename_epub(output_dir)
        # Files created inside function
        
        # 5. Special characters in filename
        create_special_chars_epub(output_dir)
        # May fail on some filesystems
        
        # 6. International/unicode characters (German, French, Spanish, Polish, etc.)
        create_international_chars_epub(output_dir)
        # Critical for international users
        
        # 7. Missing mimetype (invalid EPUB)
        path = output_dir / "test_no_mimetype.epub"
        create_missing_mimetype_epub(path)
        files_created.append(path)
        
        # 8. Rich metadata (for parsing tests)
        path = output_dir / "test_rich_metadata.epub"
        create_epub_with_metadata(path)
        files_created.append(path)

        # 9. Right-to-left spine (#1303 — RTL page-turn direction)
        path = output_dir / "test_rtl_vertical.epub"
        create_rtl_epub(path)
        files_created.append(path)

        # 10. Left-to-right control for the same test (#1303)
        path = output_dir / "test_ltr_horizontal.epub"
        create_ltr_epub(path)
        files_created.append(path)

    except Exception as e:
        print(f"\n❌ Error creating files: {e}")
        return 1
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 Generation Summary")
    print("=" * 70)
    
    all_files = sorted(output_dir.glob("test_*.epub")) + sorted(output_dir.glob("test_*.txt"))
    print(f"✓ Created {len(all_files)} synthetic test files:\n")
    
    total_size = 0
    for f in all_files:
        size = f.stat().st_size
        total_size += size
        print(f"  - {f.name:<60} {size:>10,} bytes")
    
    print(f"\n💾 Total size: {total_size:,} bytes ({total_size / 1024:.2f} KB)")
    
    print("\n✅ Generation complete!")
    print("\nThese files can be used in tests:")
    print("  - test_minimal_valid.epub        → Fast positive tests")
    print("  - test_corrupted.epub            → Error handling tests")
    print("  - test_empty.epub                → Edge case tests")
    print("  - test_international_*.epub      → Unicode/international chars")
    print("  - test_rich_metadata.epub        → Metadata parsing tests")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
