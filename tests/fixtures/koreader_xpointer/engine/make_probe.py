"""Build a small EPUB that exercises every crengine text-node rule."""
import zipfile, sys
out = sys.argv[1]
XH = '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n<head><title>{t}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
ch1 = XH.format(t="Rules") + '''<body>
nu body direct text before blocks
<div class="chapter" id="c1">
<h2>Chapter <br/>One</h2>
<p>
<a id="a1"></a>
alpha after a leading empty anchor</p>
<p>lead <span>
<b>bold</b> beta inside span</span> tail</p>
<div class="mix">
<p>para one</p>
 gamma loose text <i>italic</i> 
<p>para two</p>
</div>
<div>
<p>x</p>
<i>ia</i> <i>ib</i> delta after two italics
<p>y</p>
</div>
<p>epsilon start <i>mid</i>
</p>
<p>zeta
\t\t   spaced    words   here   and\tthere</p>
<p>eta \U0001F600 emoji \U0001D518 then target word</p>
<p>theta   nbsp  run   end</p>
<div>
lambda direct text in div
<p>after lambda</p>
</div>
<div>
<p>m1</p>
<span hidden="hidden">hid</span> <i>mu</i> text after hidden
<p>m2</p>
</div>
<p>xi &amp; omicron target &lt;tag&gt; here</p>
<p>pi hy­phen soft target</p>
<p>sigma <a id="s1"/>after self closed anchor</p>
<div>
 tau line one<br/>
 tau line two
</div>
<p>upsilon before <!-- a comment --> after comment</p>
<p><b>phi</b> <i>chi</i></p>
<div>
<p>w1</p>
<span epub:type="pagebreak" id="pg6" title="6"></span>
<i>omega</i> after pagebreak
<p>w2</p>
</div>
</div>
</body>
</html>
'''
ch2 = XH.format(t="Nonlinear") + '<body>\n<p>nonlinear chapter kappa words</p>\n</body>\n</html>\n'
long_words = " ".join(f"rho{i}" for i in range(2200))
ch3 = XH.format(t="Long") + f'<body>\n<p>\n{long_words}\n</p>\n<p>after long paragraph</p>\n</body>\n</html>\n'
ch4 = (XH.format(t="CRLF") + '<body>\r\n<p>\r\nchi crlf   line\r\none\r\n  two</p>\r\n</body>\r\n</html>\r\n')
css = "p { text-indent: 1em } .chapter { margin-top: 2em } h2 { text-align: center }\n"
opf = '''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">probe-xpointer-1</dc:identifier><dc:title>XPointer probe</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">2026-09-23T00:00:00Z</meta></metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="c1" href="text/rules.xhtml" media-type="application/xhtml+xml"/>
<item id="c2" href="text/nonlinear.xhtml" media-type="application/xhtml+xml"/>
<item id="c3" href="text/long.html" media-type="application/xhtml+xml"/>
<item id="c4" href="text/crlf.xhtml" media-type="application/xhtml+xml"/>
<item id="css" href="text/style.css" media-type="text/css"/>
</manifest>
<spine>
<itemref idref="c1"/>
<itemref idref="c2" linear="no"/>
<itemref idref="c3"/>
<itemref idref="c4" id="crlf-ref"/>
</spine>
</package>
'''
nav = XH.format(t="Nav").replace('<link rel="stylesheet" type="text/css" href="style.css"/>','') + '<body><nav epub:type="toc"><ol><li><a href="text/rules.xhtml">Rules</a></li></ol></nav></body></html>\n'
with zipfile.ZipFile(out, "w") as z:
    z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
    z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    z.writestr("OEBPS/content.opf", opf)
    z.writestr("OEBPS/nav.xhtml", nav)
    z.writestr("OEBPS/text/rules.xhtml", ch1.encode("utf-8"))
    z.writestr("OEBPS/text/nonlinear.xhtml", ch2)
    z.writestr("OEBPS/text/long.html", ch3)
    z.writestr("OEBPS/text/crlf.xhtml", ch4.encode("utf-8"))
    z.writestr("OEBPS/text/style.css", css)
