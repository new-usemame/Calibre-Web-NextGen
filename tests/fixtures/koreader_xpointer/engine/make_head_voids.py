"""Build head-voids.epub: HTML chapters whose <head> leaves void elements open.

Many calibre-converted and retail EPUBs write ``<meta charset="utf-8">`` and
``<link ...>`` without ``/>``. Browsers parse ``.html`` members with the HTML
parser, where those elements are void, and crengine is lenient, so both
readers see the same tree; only a strict XML parse rejects the file.

Spine (text is public domain, from *Alice's Adventures in Wonderland*):
  1 open.html      head voids left open, the body below
  2 closed.html    the same file with the head voids self-closed
  3 bodyvoid.html  a void left open in the BODY
  4 badhead.html   a head still malformed once its voids are closed
  5 open.xhtml     the open-void head again, but an XHTML member (the browser
                   parses it as XML, fails, and renders no body)

usage: python make_head_voids.py <out.epub>
"""
import sys
import zipfile

out = sys.argv[1]

DECL = '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml">\n'
OPEN_HEAD = ('<head>\n<meta charset="utf-8">\n'
             '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
             '<title>Down the Rabbit-Hole</title>\n'
             '<link rel="stylesheet" type="text/css" href="style.css">\n</head>\n')
CLOSED_HEAD = ('<head>\n<meta charset="utf-8"/>\n'
               '<meta name="viewport" content="width=device-width, initial-scale=1"/>\n'
               '<title>Down the Rabbit-Hole</title>\n'
               '<link rel="stylesheet" type="text/css" href="style.css"/>\n</head>\n')
BODY = '''<body>
<h1 id="ch1">Down the Rabbit-Hole</h1>
<p>Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do.</p>
<p>Once or twice she had peeped into the <i>book</i> her sister was reading, but it had no <b>pictures</b> or conversations in it.</p>
<div class="aside">
<p>“and what is the use of a book,” thought Alice</p>
 “without <em>pictures</em> or conversations?”
<p>So she was considering in her own mind.</p>
</div>
<p class="verse">How doth the little crocodile
    Improve his shining tail</p>
<p>There was nothing so <span>very</span> remarkable in that.</p>
</body>
</html>
'''
BODY_VOID = '''<body>
<p>Down, down, down.<br>Would the fall never come to an end?</p>
</body>
</html>
'''
BAD_HEAD = ('<head>\n<meta name=robots content=noindex>\n<title>Bad head</title>\n</head>\n'
            '<body>\n<p>Presently she began again.</p>\n</body>\n</html>\n')

css = ".verse { white-space: pre-wrap }\np { text-indent: 1em }\n"
opf = '''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">head-voids-1</dc:identifier><dc:title>Head voids</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">2026-09-25T00:00:00Z</meta></metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="open" href="text/open.html" media-type="application/xhtml+xml"/>
<item id="closed" href="text/closed.html" media-type="application/xhtml+xml"/>
<item id="bodyvoid" href="text/bodyvoid.html" media-type="application/xhtml+xml"/>
<item id="badhead" href="text/badhead.html" media-type="application/xhtml+xml"/>
<item id="openx" href="text/open.xhtml" media-type="application/xhtml+xml"/>
<item id="css" href="text/style.css" media-type="text/css"/>
</manifest>
<spine>
<itemref idref="open"/>
<itemref idref="closed"/>
<itemref idref="bodyvoid"/>
<itemref idref="badhead"/>
<itemref idref="openx"/>
</spine>
</package>
'''
nav = (DECL + '<head><title>Nav</title></head>\n<body><nav xmlns:epub="http://www.idpf.org/2007/ops" '
       'epub:type="toc"><ol><li><a href="text/open.html">Open</a></li></ol></nav></body></html>\n')
with zipfile.ZipFile(out, "w") as z:
    z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
    z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    z.writestr("OEBPS/content.opf", opf)
    z.writestr("OEBPS/nav.xhtml", nav)
    z.writestr("OEBPS/text/open.html", (DECL + OPEN_HEAD + BODY).encode("utf-8"))
    z.writestr("OEBPS/text/closed.html", (DECL + CLOSED_HEAD + BODY).encode("utf-8"))
    z.writestr("OEBPS/text/bodyvoid.html", (DECL + OPEN_HEAD + BODY_VOID).encode("utf-8"))
    z.writestr("OEBPS/text/badhead.html", (DECL + BAD_HEAD).encode("utf-8"))
    z.writestr("OEBPS/text/open.xhtml", (DECL + OPEN_HEAD + BODY).encode("utf-8"))
    z.writestr("OEBPS/text/style.css", css)
