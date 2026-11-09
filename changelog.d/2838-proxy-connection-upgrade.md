### Fixed

- **Signing in and creating app passwords no longer hang behind a proxy that sends `Connection: upgrade` on every request.** A common nginx and Nginx Proxy Manager WebSocket snippet sets `proxy_set_header Connection "upgrade"` for all traffic. The web server then treated every request as a protocol switch and waited on the raw connection for the request body, so sign-in, app-password creation and other form or JSON posts sat until the proxy gave up with a 504. Requests are now always read as ordinary HTTP requests, since nothing in CWNG uses a protocol upgrade. (#2838)
