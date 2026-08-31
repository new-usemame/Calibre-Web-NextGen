# KOReader emulator for CWNG plugin development.
#
# Runs the official koreader-linux-arm64 build inside Xvfb, exposed over noVNC so
# the reader can be driven from a browser (and therefore from Playwright) without
# any physical e-reader. Same Lua, same plugin loader, same lua-Spore as a real
# Kobo/Kindle, which is what makes a green run here mean something.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      libsdl2-2.0-0 xvfb x11vnc novnc websockify fluxbox x11-utils \
      ca-certificates fonts-dejavu-core xz-utils curl procps \
      xdotool \
    && rm -rf /var/lib/apt/lists/*

# Screen geometry defaults to the Kindle 11th-gen (2024) panel so layout bugs
# surface at the size the household device actually uses.
ENV SCREEN_W=1072 SCREEN_H=1448 DISPLAY=:99 HOME=/koreader-home

# Pinned upstream build. Fetched at build time rather than vendored so the repo
# stays free of a 29 MB binary (see tests/unit/test_dockerignore_build_context.py).
# The build must follow the host architecture. This was pinned to arm64 because
# it was written on Apple Silicon, and an arm64 binary on an amd64 CI runner does
# not fail loudly: the entrypoint prints "starting KOReader..." and then nothing
# at all, which reads as a hung boot rather than a wrong binary.
ARG KOREADER_VERSION=v2026.07.1
ARG TARGETARCH
RUN set -eu; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
      arm64|aarch64) ko_arch=arm64 ;; \
      amd64|x86_64)  ko_arch=x86_64 ;; \
      armhf|arm)     ko_arch=armhf ;; \
      *) echo "unsupported architecture: ${TARGETARCH:-unknown}" >&2; exit 1 ;; \
    esac; \
    mkdir -p /opt/koreader; \
    curl -fsSL -o /tmp/ko.tar.xz \
      "https://github.com/koreader/koreader/releases/download/${KOREADER_VERSION}/koreader-linux-${ko_arch}-${KOREADER_VERSION}.tar.xz"; \
    tar -xJf /tmp/ko.tar.xz -C /opt/koreader; rm /tmp/ko.tar.xz

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 6080
ENTRYPOINT ["/entrypoint.sh"]
