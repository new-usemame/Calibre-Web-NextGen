# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

#  Flask License
#
#  Copyright © 2010 by the Pallets team, cervinko, janeczku, OzzieIsaacs
#
#  Some rights reserved.
#
#  Redistribution and use in source and binary forms of the software as
#  well as documentation, with or without modification, are permitted
#  provided that the following conditions are met:
#
#  * Redistributions of source code must retain the above copyright notice,
#  this list of conditions and the following disclaimer.
#
#  * Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
#  * Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
#  THIS SOFTWARE AND DOCUMENTATION IS PROVIDED BY THE COPYRIGHT HOLDERS AND
#  CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING,
#  BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND
#  FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
#  COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
#  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
#  NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
#  USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
#  ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
#  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
#  THIS SOFTWARE AND DOCUMENTATION, EVEN IF ADVISED OF THE POSSIBILITY OF
#  SUCH DAMAGE.
#
# Inspired by http://flask.pocoo.org/snippets/35/

import ipaddress
import os
import re

from . import logger

log = logger.create()

# Where a reverse proxy normally sits: the same host, the docker network, the
# LAN, a Tailscale tailnet (100.64/10) or an IPv6 private range.
DEFAULT_TRUSTED_PROXY_NETWORKS = (
    "127.0.0.0/8", "::1/128",
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "fe80::/10",
    "100.64.0.0/10", "fc00::/7",
)

# Headers only a reverse proxy has any business setting. ProxyFix and
# ReverseProxied turn them into the client's address, scheme, host and mount
# prefix, so a client that reaches the app directly must not be able to.
_PROXY_HEADERS = (
    "HTTP_X_FORWARDED_FOR", "HTTP_X_FORWARDED_PROTO", "HTTP_X_FORWARDED_HOST",
    "HTTP_X_FORWARDED_PORT", "HTTP_X_FORWARDED_PREFIX", "HTTP_FORWARDED",
    "HTTP_X_SCHEME", "HTTP_X_SCRIPT_NAME", "HTTP_X_REAL_IP",
)

DIRECT_PEER = "cps.direct_peer"
# The proxy headers as the connection sent them, before any were removed:
# whether a call is this host's own depends on them even when they are not
# believed (cwa_functions._is_local_call).
SENT_PROXY_HEADERS = "cps.sent_proxy_headers"


def parse_trusted_networks(value):
    """The networks a reverse proxy may connect from, from TRUSTED_PROXY_NETWORKS.

    Unset or empty means the private ranges above; ``private`` stands for
    them inside a list, so a proxy can be added without dropping them. ``*``
    trusts every peer (the behaviour before this setting existed). Entries
    are separated by commas or spaces; one that is not an address or network
    is logged and skipped, and a list with no usable entry means the default.
    """
    entries = (value or "").replace(",", " ").split()
    if not entries:
        entries = ["private"]
    if "*" in entries:
        return None
    networks = []
    for entry in entries:
        if entry.lower() == "private":
            networks.extend(ipaddress.ip_network(n) for n in DEFAULT_TRUSTED_PROXY_NETWORKS)
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            log.error("TRUSTED_PROXY_NETWORKS: %r is not an address or network; ignored", entry)
    if not networks:
        log.error("TRUSTED_PROXY_NETWORKS has no usable entry; using the default (private)")
        networks = [ipaddress.ip_network(n) for n in DEFAULT_TRUSTED_PROXY_NETWORKS]
    return tuple(networks)


def _address(value):
    try:
        address = ipaddress.ip_address((value or "").split("%", 1)[0])
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def is_loopback(value):
    address = _address(value)
    return address is not None and address.is_loopback


_FORWARDED_FOR = re.compile(r'for\s*=\s*"?\[?([^;,"\]]*)', re.IGNORECASE)


def named_clients(sent_headers):
    """Every client address a proxy recorded in these headers, as sent.

    X-Forwarded-For hops, X-Real-IP, and the ``for=`` of each Forwarded
    element. A port on a Forwarded address is dropped; an obfuscated or
    unknown one is returned as written, so it never passes as loopback.
    """
    names = [hop.strip() for hop in sent_headers.get("HTTP_X_FORWARDED_FOR", "").split(",")]
    names.append(sent_headers.get("HTTP_X_REAL_IP", "").strip())
    for match in _FORWARDED_FOR.finditer(sent_headers.get("HTTP_FORWARDED", "")):
        name = match.group(1).strip()
        if name.count(":") == 1:  # an IPv4 address with a port
            name = name.split(":", 1)[0]
        names.append(name)
    return [name for name in names if name]


class TrustedProxyPeers(object):
    """Honour reverse-proxy headers only from a peer on a trusted network.

    It wraps everything that reads those headers (ReverseProxied, ProxyFix),
    so it must be the outermost middleware. A request from any other peer
    has them removed before anything sees them: the app then takes the peer
    at its own address and scheme. The peer's own address is kept in the
    environ under ``DIRECT_PEER`` for checks that must not trust any header.
    """

    # Peers already told about in the log; bounded so a stream of clients
    # sending proxy headers cannot grow it or flood the log.
    _WARN_AT_MOST = 32

    def __init__(self, app, networks):
        self.app = app
        self.networks = networks
        self._warned = set()

    def trusts(self, peer):
        if self.networks is None:
            return True
        address = _address(peer)
        if address is None or address.is_unspecified:
            # Not an IP peer: a Unix socket, which only this host can reach
            # (gevent reports none, tornado 0.0.0.0).
            return True
        return any(address in network for network in self.networks)

    def __call__(self, environ, start_response):
        peer = environ.get("REMOTE_ADDR", "")
        environ[DIRECT_PEER] = peer
        environ[SENT_PROXY_HEADERS] = {
            header: environ[header] for header in _PROXY_HEADERS if header in environ}
        if not self.trusts(peer):
            dropped = [header for header in _PROXY_HEADERS if environ.pop(header, None) is not None]
            if dropped and peer not in self._warned and len(self._warned) < self._WARN_AT_MOST:
                self._warned.add(peer)
                log.warning(
                    "Ignoring reverse-proxy headers from %s: it is not on a trusted proxy "
                    "network. If it is your proxy, add it to TRUSTED_PROXY_NETWORKS.", peer)
        return self.app(environ, start_response)

    @property
    def is_proxied(self):
        # Kobo URL building asks the outermost middleware this
        # (current_app.wsgi_app.is_proxied); ReverseProxied, inside, knows.
        return getattr(self.app, "is_proxied", False)

    def describe(self):
        if self.networks is None:
            return "every peer (TRUSTED_PROXY_NETWORKS=*)"
        return ", ".join(str(network) for network in self.networks) or "no peer"


class ReverseProxied(object):
    """Wrap the application in this middleware and configure the
    front-end server to add these headers, to let you quietly bind
    this to a URL other than / and to an HTTP scheme that is
    different than what is used locally.

    Code courtesy of: http://flask.pocoo.org/snippets/35/

    In nginx:
    location /myprefix {
        proxy_pass http://127.0.0.1:8083;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header X-Script-Name /myprefix;
        }

    If the front-end can't be configured to inject those headers
    (Tailscale Funnel, Cloudflare Tunnel, other opaque TLS
    terminators), the same effect can be achieved through environment
    variables. Headers always win when both are set:

    * ``PROXY_SCRIPT_NAME`` — path-prefix mount (e.g. ``/calibre``).
    * ``PROXY_SCHEME``      — ``http`` or ``https``.
    * ``PROXY_HOST``        — externally-visible hostname.
    * ``PROXY_PORT``        — optional, appended to ``PROXY_HOST`` (or
      to the ``X-Forwarded-Host`` value) only when no port is present.

    Backport of janeczku/calibre-web PR #3369 (@chtzvt).
    """

    def __init__(self, application,
                 script_name=None, scheme=None, forwarded_host=None, port=None):
        self.app = application
        self.proxied = False

        self.env_script = script_name or os.getenv('PROXY_SCRIPT_NAME', '')
        self.env_scheme = scheme or os.getenv('PROXY_SCHEME', '')
        self.env_host = forwarded_host or os.getenv('PROXY_HOST', '')
        self.env_port = port or os.getenv('PROXY_PORT', '')

    def __call__(self, environ, start_response):
        self.proxied = False

        # Normalise the mount prefix to the one shape PEP 3333 allows: a
        # non-empty SCRIPT_NAME starts with exactly one '/' and does not end in
        # one. Operators write PROXY_SCRIPT_NAME=/cwa/ and PROXY_SCRIPT_NAME=cwa
        # about as often as they write /cwa, and proxies get configured with
        # X-Script-Name: /cwa/ ; un-normalised those produced SCRIPT_NAME='/cwa/'
        # with PATH_INFO='books', or SCRIPT_NAME='cwa' which matches no path at
        # all — each 404ing every page in its own way. Any all-slash value ('/',
        # '//') means "mounted at root", i.e. no prefix. Collapsing the leading
        # run also keeps a spoofed 'X-Script-Name: //host' from becoming a
        # scheme-relative '//host/...' prefix in generated URLs; cps/spa.py
        # sanitises again at the point it reflects the prefix into HTML.
        script_name = (environ.get('HTTP_X_SCRIPT_NAME', '') or self.env_script).rstrip('/')
        if script_name:
            script_name = '/' + script_name.lstrip('/')
            self.proxied = True
            environ['SCRIPT_NAME'] = script_name
            path_info = environ.get('PATH_INFO', '')
            # Strip the mount prefix only on a path-SEGMENT boundary (#1248).
            # A bare startswith() also fires on sibling paths that merely share
            # the prefix's characters: with a /cwa mount it rewrote every one of
            # the 37 /cwa-* routes ('/cwa-settings' -> '-settings') into a path
            # matching no rule, so the whole CWA surface 404'd while the
            # neighbouring /admin/* links worked. Equality is the mount root;
            # prefix + '/' is a genuine child path. Keeping the strip narrow
            # also means a prefix-stripping proxy (nginx `proxy_pass …:8083/;`,
            # which hands us an already-stripped PATH_INFO) is left alone,
            # while a non-stripping one still gets its prefix removed.
            if path_info == script_name:
                environ['PATH_INFO'] = ''
            elif path_info.startswith(script_name + '/'):
                environ['PATH_INFO'] = path_info[len(script_name):]

        scheme = (
            environ.get('HTTP_X_SCHEME', '')
            or environ.get('HTTP_X_FORWARDED_PROTO', '')
            or self.env_scheme
        )
        if scheme:
            self.proxied = True
            environ['wsgi.url_scheme'] = scheme

        host = environ.get('HTTP_X_FORWARDED_HOST', '') or self.env_host
        if host:
            self.proxied = True
            if self.env_port and ':' not in host:
                host = '{}:{}'.format(host, self.env_port)
            environ['HTTP_HOST'] = host

        return self.app(environ, start_response)

    @property
    def is_proxied(self):
        return self.proxied
