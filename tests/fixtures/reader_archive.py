"""Emit a small EPUB from JSON chapter bodies on stdin for browser tests.

Standard-library only; all book content is authored by the tests. Each chapter
uses a different directory but the same basename, exercising real spine lookup.
"""
import io
import json
import sys
import zipfile


def archive(chapters):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr("META-INF/container.xml", '''<container version="1.0"
          xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>
          <rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/>
          </rootfiles></container>''')
        manifest = []
        spine = []
        for index, chapter in enumerate(chapters):
            href = f"part{index}/chapter.xhtml"
            manifest.append(f'<item id="c{index}" href="{href}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{index}"/>')
            book.writestr(f"OPS/{href}", '<html xmlns="http://www.w3.org/1999/xhtml">'
                          '<head><title>Reader fixture</title></head><body>' + chapter + '</body></html>')
        book.writestr("OPS/package.opf", '<package xmlns="http://www.idpf.org/2007/opf" '
                      'version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                      '<dc:identifier id="id">native-display-fixture</dc:identifier>'
                      '<dc:title>Native display fixture</dc:title><dc:language>en</dc:language></metadata>'
                      '<manifest>' + ''.join(manifest) + '</manifest><spine>' + ''.join(spine) + '</spine></package>')
    return output.getvalue()


if __name__ == "__main__":
    sys.stdout.buffer.write(archive(json.load(sys.stdin)))
