### Changed

- **Reverse-proxy headers are believed only from where a proxy sits.** CWNG
  reads the client's address, scheme and host from `X-Forwarded-For`,
  `X-Forwarded-Proto` and the other proxy headers. It now does so only when the
  connection comes from this host, a private network (the docker network, your
  LAN) or a Tailscale tailnet. Anything else is taken at its own address. Most
  setups need no change. If your proxy reaches CWNG from a public address, add
  it to the new `TRUSTED_PROXY_NETWORKS` setting. The common case is
  Cloudflare's proxy forwarding straight to the container, with no proxy of
  your own in between. Set it to `*` to trust every peer as before. The log
  names any peer whose proxy headers are ignored.
