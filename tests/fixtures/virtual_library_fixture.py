#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline Newznab + NNTP fixture for real Prowlarr/SAB integration.

Generates a small original EPUB and serves its single yEnc article. This fixture
has no outbound network access. Run only on an owned isolated Docker network.
"""
import binascii
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import socketserver
from threading import Thread
from urllib.parse import parse_qs, urlsplit
import zipfile

out = io.BytesIO()
with zipfile.ZipFile(out, 'w') as book:
    book.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
    book.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    book.writestr('content.opf', '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">urn:cwng:local:original:fixture</dc:identifier><dc:title>CWNG Local Fixture</dc:title><dc:creator>Fixture Author</dc:creator><dc:language>en</dc:language><meta property="dcterms:modified">2026-09-30T00:00:00Z</meta></metadata><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest><spine><itemref idref="chapter"/></spine></package>')
    book.writestr('chapter.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Local fixture</title></head><body><h1>CWNG Local Fixture</h1><p>This original text was generated to test the local acquisition pipeline. No public indexer or Usenet provider is involved.</p></body></html>')
    book.writestr('nav.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Local fixture</a></li></ol></nav></body></html>')
EPUB = out.getvalue()
MESSAGE_ID = 'cwng-original-fixture@local.test'
NZB = f'<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"><file poster="fixture@local.test" date="1790726400" subject="CWNG Local Fixture - &quot;fixture.epub&quot; yEnc (1/1)"><groups><group>alt.binaries.ebooks</group></groups><segments><segment bytes="{len(EPUB)}" number="1">{MESSAGE_ID}</segment></segments></file></nzb>'.encode()
CAPS = b'<caps><server title="CWNG offline fixture"/><limits max="100" default="50"/><registration available="no" open="no"/><searching><search available="yes" supportedParams="q"/><tv-search available="no"/><movie-search available="no"/><audio-search available="no"/><book-search available="yes" supportedParams="q,author,title"/></searching><categories><category id="7000" name="Books"><subcat id="7020" name="EBook"/></category></categories></caps>'
COUNTS = {'search': 0, 'nzb': 0, 'article': 0}


def yenc():
    result = [f'=ybegin line=128 size={len(EPUB)} name=fixture.epub'.encode()]
    line = bytearray()
    for value in EPUB:
        char = (value + 42) % 256
        encoded = bytes((61, (char + 64) % 256)) if char in (0, 10, 13, 61) else bytes((char,))
        if len(line) + len(encoded) > 128:
            result.append(bytes(line)); line.clear()
        line.extend(encoded)
    if line:
        result.append(bytes(line))
    result.append(f'=yend size={len(EPUB)} crc32={binascii.crc32(EPUB):08x}'.encode())
    return result


class HTTP(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == '/counts':
            body, mime = json.dumps(COUNTS).encode(), 'application/json'
        elif parsed.path == '/book.epub':
            body, mime = EPUB, 'application/epub+zip'
        elif query.get('apikey') != ['fixture-key']:
            body, mime = b'<error code="100" description="Invalid API key"/>', 'text/xml'
        elif query.get('t') == ['caps']:
            body, mime = CAPS, 'text/xml'
        elif parsed.path == '/nzb':
            COUNTS['nzb'] += 1
            body, mime = NZB, 'application/x-nzb'
        else:
            COUNTS['search'] += 1
            body = f'<rss version="2.0" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"><channel><title>CWNG offline fixture</title><item><title>CWNG Local Fixture EPUB</title><guid isPermaLink="false">cwng-original-release</guid><link>http://fixture:8090/nzb?apikey=fixture-key</link><pubDate>Wed, 30 Sep 2026 00:00:00 +0000</pubDate><description>Original locally generated test book</description><category>7020</category><enclosure url="http://fixture:8090/nzb?apikey=fixture-key" length="{len(NZB)}" type="application/x-nzb"/><newznab:attr name="category" value="7020"/><newznab:attr name="size" value="{len(EPUB)}"/><newznab:attr name="guid" value="cwng-original-release"/></item></channel></rss>'.encode()
            mime = 'text/xml'
        self.send_response(200); self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *args):
        pass  # Request URLs carry fixture keys, just as real indexer URLs do.


class NNTP(socketserver.StreamRequestHandler):
    def send(self, value):
        self.wfile.write(value + b'\r\n'); self.wfile.flush()

    def multiline(self, lines):
        for line in lines:
            self.send(b'.' + line if line.startswith(b'.') else line)
        self.send(b'.')

    def handle(self):
        self.send(b'200 CWNG original local fixture ready')
        while raw := self.rfile.readline(8192):
            parts = raw.strip().split(b' ', 1)
            command = parts[0].upper()
            argument = parts[1] if len(parts) == 2 else b''
            if command == b'QUIT':
                self.send(b'205 goodbye'); break
            elif command == b'CAPABILITIES':
                self.send(b'101 capabilities'); self.multiline([b'VERSION 2', b'READER'])
            elif command == b'MODE':
                self.send(b'200 reader ready')
            elif command == b'AUTHINFO':
                self.send(b'281 authentication accepted')
            elif command == b'GROUP':
                self.send(b'211 1 1 1 alt.binaries.ebooks')
            elif command == b'DATE':
                self.send(b'111 20260930000000')
            elif command in (b'BODY', b'ARTICLE', b'STAT', b'HEAD'):
                if argument.strip(b'<>').decode(errors='replace') != MESSAGE_ID:
                    self.send(b'430 no such article'); continue
                COUNTS['article'] += 1
                code = {b'BODY': b'222', b'ARTICLE': b'220', b'STAT': b'223', b'HEAD': b'221'}[command]
                self.send(code + b' 1 <' + MESSAGE_ID.encode() + b'> article follows')
                if command != b'STAT':
                    headers = [b'From: fixture@local.test', b'Subject: fixture.epub yEnc', b'Message-ID: <' + MESSAGE_ID.encode() + b'>', b'Newsgroups: alt.binaries.ebooks']
                    self.multiline((headers + [b''] if command == b'ARTICLE' else headers if command == b'HEAD' else []) + (yenc() if command != b'HEAD' else []))
            else:
                self.send(b'500 unsupported command')


if __name__ == '__main__':
    server = socketserver.ThreadingTCPServer(('0.0.0.0', 8119), NNTP)
    server.daemon_threads = True
    Thread(target=server.serve_forever, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', 8090), HTTP).serve_forever()
