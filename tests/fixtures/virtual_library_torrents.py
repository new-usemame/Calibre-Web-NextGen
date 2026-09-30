#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local Torznab/tracker + original EPUB + NNTP, with no outbound services.

A real Transmission seeder shares the generated payloads. Disable DHT/PEX/LSD
on clients; this tracker returns only that owned seeder's compact address.
"""
import hashlib
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import socketserver
from threading import Thread
from urllib.parse import parse_qs, quote, urlsplit
import zipfile
import virtual_library_fixture as source


def bencode(value):
    if isinstance(value, int): return b'i'+str(value).encode()+b'e'
    if isinstance(value, str): value=value.encode()
    if isinstance(value, bytes): return str(len(value)).encode()+b':'+value
    if isinstance(value, list): return b'l'+b''.join(bencode(v) for v in value)+b'e'
    return b'd'+b''.join(bencode(k)+bencode(value[k]) for k in sorted(value))+b'e'


# Give this slice its own original book identity, reusing the slice-3 fixture.
out=io.BytesIO()
with zipfile.ZipFile(io.BytesIO(source.EPUB)) as old, zipfile.ZipFile(out, 'w') as new:
    for item in old.infolist():
        new.writestr(item, old.read(item).replace(b'CWNG Local Fixture', b'CWNG Client Breadth Fixture').replace(b'urn:cwng:local:original:fixture', b'urn:cwng:local:original:breadth'))
source.EPUB=out.getvalue()
source.NZB=source.NZB.replace(b'CWNG Local Fixture', b'CWNG Client Breadth Fixture')
TORRENTS={}
for name, multi in (('single', False), ('multi', True)):
    root='breadth-'+name
    payload=source.EPUB+(b'Original fixture companion.\n' if multi else b'')
    info={'name': root if multi else root+'.epub', 'piece length': 16384,
        'pieces': b''.join(hashlib.sha1(payload[i:i+16384]).digest() for i in range(0,len(payload),16384)), 'private': 0}
    if multi: info['files']=[{'length':len(source.EPUB),'path':['book.epub']},{'length':len(payload)-len(source.EPUB),'path':['readme.txt']}]
    else: info['length']=len(payload)
    torrent=bencode({'announce':'http://fixture:8090/announce','info':info})
    identity=hashlib.sha1(bencode(info)).hexdigest()
    TORRENTS[name]={'body':torrent,'hash':identity,'magnet':'magnet:?xt=urn:btih:'+identity+'&tr='+quote('http://fixture:8090/announce',safe='')}
    assets=Path(os.environ.get('FIXTURE_ASSETS','/assets')); assets.mkdir(parents=True,exist_ok=True)
    (assets/(name+'.torrent')).write_bytes(torrent)
    if multi:
        (assets/root).mkdir(exist_ok=True); (assets/root/'book.epub').write_bytes(source.EPUB); (assets/root/'readme.txt').write_bytes(payload[len(source.EPUB):])
    else: (assets/(root+'.epub')).write_bytes(source.EPUB)


class HTTP(source.HTTP):
    def do_GET(self):
        parsed=urlsplit(self.path); query=parse_qs(parsed.query)
        if parsed.path=='/announce':
            peer=socket.inet_aton(socket.gethostbyname('seeder'))+(51423).to_bytes(2,'big')
            body=bencode({'interval':2,'complete':1,'incomplete':0,'peers':peer}); mime='text/plain'
            source.COUNTS['announce']=source.COUNTS.get('announce',0)+1
        elif parsed.path in ('/single.torrent','/multi.torrent') and query.get('apikey')==['fixture-key']:
            body=TORRENTS[parsed.path[1:].split('.')[0]]['body']; mime='application/x-bittorrent'
            source.COUNTS['torrent']=source.COUNTS.get('torrent',0)+1
        elif parsed.path=='/torznab' and query.get('apikey')==['fixture-key'] and query.get('t') != ['caps']:
            source.COUNTS['torznab']=source.COUNTS.get('torznab',0)+1
            items=[]
            for name in ('single','multi'):
                href='http://fixture:8090/single.torrent' if name=='single' else TORRENTS[name]['magnet']
                items.append(f'<item><title>Original breadth {name} EPUB</title><guid>{TORRENTS[name]["hash"]}</guid><enclosure url="{href.replace("&","&amp;")}" type="application/x-bittorrent"/></item>')
            body=('<rss><channel>'+''.join(items)+'</channel></rss>').encode(); mime='text/xml'
        else: return super().do_GET()
        self.send_response(200); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)


if __name__=='__main__':
    server=socketserver.ThreadingTCPServer(('0.0.0.0',8119),source.NNTP); server.daemon_threads=True
    Thread(target=server.serve_forever,daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0',8090),HTTP).serve_forever()
