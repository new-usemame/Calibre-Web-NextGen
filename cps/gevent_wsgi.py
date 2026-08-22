# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from datetime import datetime
from gevent.pywsgi import WSGIHandler


class MyWSGIHandler(WSGIHandler):
    def read_request(self, raw_requestline):
        # Force ``Connection: close`` on every response. Reverse-proxy
        # keepalive sockets can stay attached to the gevent process after
        # the client side has gone away; under sustained load, stale
        # sockets accumulate and starve the accept loop, surfacing as
        # unresponsive healthchecks. Closing after each response keeps
        # the proxy renegotiating fresh sockets so recovery is bounded.
        # Backport of CWA #1335 by @I-Would-Like-To-Report-A-Bug-Please;
        # addresses fork issue #193.
        is_valid = super().read_request(raw_requestline)
        self.close_connection = True
        return is_valid

    def start_response(self, status, headers, exc_info=None):
        # ``read_request`` deliberately closes every connection, but gevent only
        # adds the matching response header for HTTP/1.0. Make the same policy
        # explicit to HTTP/1.1 clients, and replace any misleading app-provided
        # value without emitting a duplicate header.
        response_headers = []
        connection_added = False
        for name, value in headers:
            if isinstance(name, str) and name.lower() == 'connection':
                if connection_added:
                    continue
                value = 'close'
                connection_added = True
            response_headers.append((name, value))

        if not connection_added:
            response_headers.append(('Connection', 'close'))

        return super().start_response(status, response_headers, exc_info)

    def get_environ(self):
        env = super().get_environ()
        path, __ = self.path.split('?', 1) if '?' in self.path else (self.path, '')
        env['RAW_URI'] = path
        return env

    def format_request(self):
        now = datetime.now().replace(microsecond=0)
        length = self.response_length or '-'
        if self.time_finish:
            delta = '%.6f' % (self.time_finish - self.time_start)
        else:
            delta = '-'
        # gevent calls ``format_request`` for invalid requests too (e.g. a TLS
        # ClientHello on a plain-HTTP listener). In that case ``get_environ``
        # is never called and ``self.environ`` stays ``None`` — accessing
        # ``.get`` would kill the access-log greenlet. See issue #147.
        forwarded = self.environ.get('HTTP_X_FORWARDED_FOR', None) if self.environ else None
        if forwarded:
            client_address = forwarded
        else:
            client_address = self.client_address[0] if isinstance(self.client_address, tuple) else self.client_address
        return '%s - - [%s] "%s" %s %s %s' % (
            client_address or '-',
            now,
            self.requestline or '',
            # Use the native string version of the status, saved so we don't have to
            # decode. But fallback to the encoded 'status' in case of subclasses
            # (Is that really necessary? At least there's no overhead.)
            (self._orig_status or self.status or '000').split()[0],
            length,
            delta)
