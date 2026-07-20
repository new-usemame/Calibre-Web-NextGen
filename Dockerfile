# syntax=docker/dockerfile:1

# ── Global build version pins (single source of truth) ──────────────────────
# Declared before any FROM so they are TRUE global ARGs: usable in FROM lines
# AND inheritable by every later stage via a bare re-declare. PYTHON_* pin the
# interpreter and address our GHCR tarball mirror; CALIBRE_RELEASE and
# KEPUBIFY_RELEASE pin the binaries the dependencies stage downloads. A
# stage-local ARG (declared after a FROM) only reaches that one stage, so a
# bare re-declare elsewhere would inherit an empty string and build a malformed
# download URL. To bump any pin, change ONLY the line here.
# See notes/PYTHON-BUILD-MIRROR.md.
ARG PYTHON_VERSION=3.13.14
ARG PYTHON_BUILD_STANDALONE_RELEASE=20260623
ARG CALIBRE_RELEASE=9.1.0
ARG KEPUBIFY_RELEASE=v4.0.4

# Where the Python tarball and the kepubify binary come from. Two sources, same
# artifacts:
#
#   upstream (default) — download them from the public GitHub release CDN. Works
#     for anyone with a checkout and no credentials, which is what a contributor
#     running `docker build .` has.
#   ghcr — COPY them from ghcr.io/new-usemame/pbs-cache, our own mirror of those
#     exact artifacts. That CDN intermittently 404s the Actions egress and broke
#     every image build, so CI passes PBS_SOURCE=ghcr and pulls from the same
#     registry the base images come from. The mirror is built/refreshed by
#     scripts/ensure-python-mirror.sh before the image build.
#
# The mirror is a CI reliability measure, so it must not be a hard build
# requirement: the package is private, and making it one locked every community
# contributor out of building the image at all (#943). BuildKit only resolves
# stages that the selected target actually reaches, so an upstream build never
# touches the private mirror. This needs BuildKit — the default since Docker
# 23, and required by the `# syntax=` directive above anyway. The legacy builder
# (DOCKER_BUILDKIT=0) evaluates every preceding stage and does not supply
# TARGETARCH, so it cannot build this image regardless of PBS_SOURCE.
# See notes/PYTHON-BUILD-MIRROR.md.
ARG PBS_SOURCE=upstream

FROM ghcr.io/new-usemame/pbs-cache:cpython-${PYTHON_VERSION}-${PYTHON_BUILD_STANDALONE_RELEASE} AS pbs_ghcr
FROM ghcr.io/new-usemame/pbs-cache:kepubify-${KEPUBIFY_RELEASE} AS kepubify_ghcr

FROM ghcr.io/linuxserver/baseimage-ubuntu:noble AS pbs_upstream
ARG PYTHON_VERSION
ARG PYTHON_BUILD_STANDALONE_RELEASE
ARG TARGETARCH
RUN \
  apt-get update && \
  apt-get install -y --no-install-recommends curl ca-certificates && \
  case "${TARGETARCH}" in \
    amd64) pbs_arch=x86_64 ;; \
    arm64) pbs_arch=aarch64 ;; \
    *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
  esac && \
  curl -fL --connect-timeout 30 --retry 8 --retry-delay 5 --retry-all-errors -o /python.tar.gz \
    "https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD_STANDALONE_RELEASE}/cpython-${PYTHON_VERSION}+${PYTHON_BUILD_STANDALONE_RELEASE}-${pbs_arch}-unknown-linux-gnu-install_only.tar.gz"

FROM ghcr.io/linuxserver/baseimage-ubuntu:noble AS kepubify_upstream
ARG KEPUBIFY_RELEASE
ARG TARGETARCH
RUN \
  apt-get update && \
  apt-get install -y --no-install-recommends curl ca-certificates && \
  case "${TARGETARCH}" in \
    amd64) kep_arch=64bit ;; \
    arm64) kep_arch=arm64 ;; \
    *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
  esac && \
  curl -fL --connect-timeout 30 --retry 8 --retry-delay 5 --retry-all-errors -o /kepubify \
    "https://github.com/pgaskin/kepubify/releases/download/${KEPUBIFY_RELEASE}/kepubify-linux-${kep_arch}" && \
  chmod 755 /kepubify

# Selector stages. Everything downstream COPYs from pbs_mirror / kepubify_mirror
# and does not care which source won.
FROM pbs_${PBS_SOURCE} AS pbs_mirror
FROM kepubify_${PBS_SOURCE} AS kepubify_mirror

# Simple Example Build Command:
# docker build \
# --tag crocodilestick/calibre-web-automated:dev \
# --build-arg="BUILD_DATE=27-09-2024 12:06" \
# --build-arg="VERSION=2.1.0-test-5" .

# Good guide on how to set up a buildx builder here:
# https://a-berahman.medium.com/simplifying-docker-multiplatform-builds-with-buildx-3d7efd670f58

# Multi-Platform Example Build & Push Command:
# docker buildx build \
# --push \
# --platform linux/amd64,linux/arm64, \
# --build-arg="BUILD_DATE=02-08-2024 20:52" \
# --build-arg="VERSION=2.1.0" \
# --tag crocodilestick/calibre-web-automated:latest .

# ==========================================================================
# STAGE 0: Frontend - Build the React SPA bundle (Vite).
# Build-time only: Node/npm never enter the runtime image. The compiled
# bundle (cps/static/app) is copied into the final stage below. The source
# tree's cps/static/app is .dockerignore'd, so this stage is the ONLY source
# of the shipped bundle — CI/production builds the SPA reproducibly instead of
# silently baking in a developer's local artifact. See
# notes/FRONTEND-REBUILD-DESIGN.md (§5 build topology).
# ==========================================================================
FROM node:22-slim AS frontend-build

WORKDIR /build/frontend

# Install deps first so this layer caches unless package*.json changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

# Then build. vite.config.ts outDir is ../cps/static/app => /build/cps/static/app.
COPY frontend/ ./
RUN npm run build

# ==========================================================================
# STAGE 1: Dependencies - Install system packages and Python dependencies
# ==========================================================================
# CALIBRE_RELEASE / KEPUBIFY_RELEASE / PYTHON_VERSION / PYTHON_BUILD_STANDALONE_RELEASE
# are declared as global ARGs at the top of this file; the dependencies stage
# re-declares them bare below so they inherit those global default values.

FROM ghcr.io/linuxserver/baseimage-ubuntu:noble AS dependencies

ARG CALIBRE_RELEASE
ARG KEPUBIFY_RELEASE
ARG PYTHON_BUILD_STANDALONE_RELEASE
ARG PYTHON_VERSION

# Set the default shell for the following RUN instructions to bash instead of sh
SHELL ["/bin/bash", "-c"]

# STEP 1 - Install required apt packages (Python comes from PBS, see STEP 1.5)
RUN \
  echo "**** install build + runtime apt packages ****" && \
  apt-get update && \
  apt-get install -y --no-install-recommends \
  build-essential \
  clang \
  libldap2-dev \
  libsasl2-dev \
  gettext \
  curl \
  ca-certificates \
  imagemagick \
  ghostscript \
  libldap2 \
  libmagic1 \
  libsasl2-2 \
  libxi6 \
  libxslt1.1 \
  xdg-utils \
  inotify-tools \
  nano \
  sqlite3 \
  zip \
  libxtst6 \
  libxrandr2 \
  libxkbfile1 \
  libxcomposite1 \
  libxcursor1 \
  libxfixes3 \
  libxrender1 \
  libopengl0 \
  libnss3 \
  libxkbcommon0 \
  libegl1 \
  libxdamage1 \
  libgl1 \
  libglx-mesa0 \
  xz-utils \
  binutils && \
  echo "**** install lsof 4.99.5 from source (fixes hanging issue with 4.95, #654) ****" && \
  LSOF_VERSION="4.99.5" && \
  curl -fL "https://github.com/lsof-org/lsof/archive/${LSOF_VERSION}.tar.gz" -o /tmp/lsof.tar.gz && \
  cd /tmp && \
  tar -xzf lsof.tar.gz && \
  cd "lsof-${LSOF_VERSION}" && \
  ./Configure -n linux && \
  make && \
  cp lsof /usr/bin/lsof && \
  chmod 755 /usr/bin/lsof && \
  cd / && \
  rm -rf /tmp/lsof*

# STEP 1.5 - Install Python 3.13 from python-build-standalone.
# The tarball comes from the pbs_mirror stage, which is either our GHCR mirror
# (CI, PBS_SOURCE=ghcr) or the public release CDN (default). See the PBS_SOURCE
# block at the top of this file. To bump Python: change PYTHON_VERSION /
# PYTHON_BUILD_STANDALONE_RELEASE above — nothing else, both sources follow the
# pins. Full explanation: notes/PYTHON-BUILD-MIRROR.md.
COPY --from=pbs_mirror /python.tar.gz /tmp/python.tar.gz
RUN \
  echo "**** install Python ${PYTHON_VERSION} from mirrored python-build-standalone ****" && \
  mkdir -p /opt && \
  tar -xzf /tmp/python.tar.gz -C /opt && \
  rm /tmp/python.tar.gz && \
  ln -sf /opt/python/bin/python3.13 /usr/local/bin/python3.13 && \
  ln -sf /opt/python/bin/python3.13 /usr/local/bin/python3 && \
  ln -sf /opt/python/bin/pip3.13 /usr/local/bin/pip3.13 && \
  python3.13 --version && \
  python3.13 -m ensurepip --upgrade

# STEP 2 - Set up Python virtual environment using the PBS interpreter
RUN \
  python3.13 -m venv /lsiopy && \
  /lsiopy/bin/pip install -U --no-cache-dir \
  pip \
  wheel

# STEP 3 - Copy requirements files and install Python packages
# Copy only requirements files first to leverage Docker layer caching
COPY --chown=abc:abc requirements.txt optional-requirements.txt /app/calibre-web-automated/

RUN \
  # STEP 3.1 - Installing the required python packages listed in 'requirements.txt' and 'optional-requirements.txt'
  # HOWEVER, they are not pulled from PyPi directly, they are pulled from linuxserver's Ubuntu Wheel Index
  # This is essentially a repository of precompiled some of the most popular packages with C/C++ source code
  # This provides the install maximum compatibility with multiple different architectures including: x86_64, armv71 and aarch64
  # You can read more about python wheels here: https://realpython.com/python-wheels/
  /lsiopy/bin/pip install -U --no-cache-dir --find-links https://wheel-index.linuxserver.io/ubuntu/ -r \
  /app/calibre-web-automated/requirements.txt -r /app/calibre-web-automated/optional-requirements.txt

# STEP 4 - Install kepubify from the kepubify_mirror stage (GHCR mirror in CI,
# public release CDN by default). Either way /kepubify arrives mode 0755 for the
# build platform.
COPY --from=kepubify_mirror /kepubify /usr/bin/kepubify

# STEP 5 - Install Calibre
RUN \
  # STEP 5.1 - Make the /app/calibre directory for the installed files
  mkdir -p /app/calibre && \
  # STEP 5.2 - Download the desired version of Calibre, determined by the CALIBRE_RELEASE variable and the architecture of the build environment
  if [ "$(uname -m)" == "x86_64" ]; then \
  curl -fL --retry 30 --retry-delay 15 --retry-all-errors -o \
  /calibre.txz \
  "https://download.calibre-ebook.com/${CALIBRE_RELEASE}/calibre-${CALIBRE_RELEASE}-x86_64.txz"; \
  elif [ "$(uname -m)" == "aarch64" ]; then \
  curl -fL --retry 30 --retry-delay 15 --retry-all-errors -o \
  /calibre.txz \
  "https://download.calibre-ebook.com/${CALIBRE_RELEASE}/calibre-${CALIBRE_RELEASE}-arm64.txz"; \
  fi && \
  # STEP 5.3 - Extract the downloaded file to /app/calibre
  tar xf \
  /calibre.txz -C \
  /app/calibre && \
  # STEP 5.3.1 - Remove the ABI tag from the extracted libQt6* files to allow them to be used on older kernels
  # Removed in V3.1.4 because it was breaking Calibre features that require Qt6. Replaced with a kernel check in the cwa-init service
  # STEP 5.4 - Delete the extracted calibre.txz to save space in final image
  rm /calibre.txz

# ============================================================================
# STAGE 2: Final - Build the final runtime image
# ============================================================================
FROM ghcr.io/linuxserver/baseimage-ubuntu:noble AS unrar-stage
FROM ghcr.io/linuxserver/unrar:latest AS unrar

FROM ghcr.io/linuxserver/baseimage-ubuntu:noble

ARG BUILD_DATE
ARG VERSION
ARG CALIBRE_RELEASE
ARG KEPUBIFY_RELEASE

LABEL build_version="Version:- ${VERSION}"
LABEL build_date="${BUILD_DATE}"
LABEL maintainer="CrocodileStick"

# Set the default shell for the following RUN instructions to bash instead of sh
SHELL ["/bin/bash", "-c"]

# Copy installed dependencies from the dependencies stage
COPY --from=dependencies /lsiopy /lsiopy
COPY --from=dependencies /usr/bin/kepubify /usr/bin/kepubify
COPY --from=dependencies /app/calibre /app/calibre
COPY --from=dependencies /usr/bin/lsof /usr/bin/lsof
# Self-contained Python 3.13 from python-build-standalone — no PPA needed at runtime
COPY --from=dependencies /opt/python /opt/python

# Install only runtime apt packages (no Python — that came from PBS via the COPY above)
RUN \
  echo "**** install runtime packages ****" && \
  apt-get update && \
  apt-get install -y --no-install-recommends \
  imagemagick \
  ghostscript \
  libldap2 \
  libmagic1 \
  libsasl2-2 \
  libxi6 \
  libxslt1.1 \
  xdg-utils \
  inotify-tools \
  nano \
  sqlite3 \
  zip \
  gettext \
  libasound2t64 \
  libxtst6 \
  libxrandr2 \
  libxkbfile1 \
  libxcomposite1 \
  libxcursor1 \
  libxfixes3 \
  libxrender1 \
  libopengl0 \
  libnss3 \
  libxkbcommon0 \
  libegl1 \
  libxdamage1 \
  libgl1 \
  libglx-mesa0 \
  xz-utils \
  ca-certificates \
  curl && \
  # Wire Python on PATH and as the default python3
  ln -sf /opt/python/bin/python3.13 /usr/local/bin/python3.13 && \
  ln -sf /opt/python/bin/python3.13 /usr/local/bin/python3 && \
  ln -sf /opt/python/bin/pip3.13 /usr/local/bin/pip3.13 && \
  python3.13 --version && \
  # Cleanup
  apt-get -y autoremove && \
  rm -rf \
  /tmp/* \
  /var/lib/apt/lists/* \
  /var/tmp/* \
  /root/.cache

# STEP 6 - Copy application files
# Copy the rest of the application code (changes most frequently)
COPY --chown=abc:abc . /app/calibre-web-automated/

# STEP 6.1 - Copy the Vite-built SPA bundle from the frontend-build stage.
# The source tree's cps/static/app is .dockerignore'd, so this COPY is the
# only bundle that ships — guaranteeing the SPA is built in-image rather than
# inherited from a developer's local build context.
COPY --from=frontend-build --chown=abc:abc /build/cps/static/app /app/calibre-web-automated/cps/static/app

# STEP 7 - Configure application
RUN \
  # STEP 7.1 - Move contents of /app/calibre-web-automated/root to / and delete the /app/calibre-web-automated/root directory
  cp -R /app/calibre-web-automated/root/* / && \
  rm -R /app/calibre-web-automated/root/ && \
  # STEP 7.2 - Run CWA install script to make required dirs, set script permissions and add aliases for CLI commands  ect.
  chmod +x /app/calibre-web-automated/scripts/setup-cwa.sh && \
  /app/calibre-web-automated/scripts/setup-cwa.sh && \
  # STEP 7.3 - Create koplugin.zip from KOReader plugin folder
  echo "~~~~ Creating koplugin.zip from KOReader plugin folder... ~~~~" && \
  if [ -d "/app/calibre-web-automated/koreader/plugins/cwasync.koplugin" ]; then \
  cd /app/calibre-web-automated/koreader/plugins && \
  # Calculate digest of all files in the plugin for debugging purposes
  echo "Calculating digest of plugin files..." && \
  PLUGIN_DIGEST=$(find cwasync.koplugin -type f -name "*.lua" -o -name "*.json" | sort | xargs sha256sum | sha256sum | cut -d' ' -f1) && \
  echo "Plugin digest: $PLUGIN_DIGEST" && \
  # Create a file named after the digest inside the plugin folder
  echo "Plugin files digest: $PLUGIN_DIGEST" > cwasync.koplugin/${PLUGIN_DIGEST}.digest && \
  echo "Build date: $(date)" >> cwasync.koplugin/${PLUGIN_DIGEST}.digest && \
  echo "Files included:" >> cwasync.koplugin/${PLUGIN_DIGEST}.digest && \
  find cwasync.koplugin -type f -name "*.lua" -o -name "*.json" | sort >> cwasync.koplugin/${PLUGIN_DIGEST}.digest && \
  zip -r koplugin.zip cwasync.koplugin/ && \
  echo "Created koplugin.zip from cwasync.koplugin folder with digest file: ${PLUGIN_DIGEST}.digest"; \
  else \
  echo "Warning: cwasync.koplugin folder not found, skipping zip creation"; \
  fi && \
  # STEP 7.4 - Move koplugin.zip to static directory
  if [ -f "/app/calibre-web-automated/koreader/plugins/koplugin.zip" ]; then \
  mkdir -p /app/calibre-web-automated/cps/static && \
  cp /app/calibre-web-automated/koreader/plugins/koplugin.zip /app/calibre-web-automated/cps/static/ && \
  echo "Moved koplugin.zip to static directory"; \
  else \
  echo "Warning: koplugin.zip not found, skipping move to static directory"; \
  fi && \
  # STEP 7.5 - ADD files referencing the versions of the installed main packages
  echo "$VERSION" >| /app/CWA_RELEASE && \
  echo "$KEPUBIFY_RELEASE" >| /app/KEPUBIFY_RELEASE && \
  echo "$CALIBRE_RELEASE" > /CALIBRE_RELEASE

# Add unrar from unrar stage
COPY --from=unrar /usr/bin/unrar-ubuntu /usr/bin/unrar

# Bake Calibre's /usr/bin symlinks into the image (#875; original patch by
# @chloeroform in #1014). The binaries themselves already ship in the image
# (COPY --from=dependencies /app/calibre above), but the /usr/bin entry
# points did not, so `calibredb --version` failed on a cold boot and the
# calibre-binaries-setup s6 service ran calibre_postinstall on EVERY start --
# ~12s of a ~60s startup. With the links present that check passes and the
# service no-ops. The service stays in place as the fallback for images
# where the links are missing (e.g. a non-Ubuntu base).
#
# The link set mirrors the entry points calibre_postinstall itself creates:
# every executable at the top level of /app/calibre except the installer
# itself and calibre-complete (the bash-completion helper, which upstream
# reaches through the completion scripts rather than through PATH).
# `test -x` resolves the symlink without executing the binary, so this stays
# valid under cross-arch buildx where the target binary cannot run.
#
# Nothing else that ran at boot moves here: the Qt6 / kernel ABI check and
# the PUID/PGID ownership pass live in the cwa-init service, which runs
# before this one and is gated on its own sentinel.
RUN find /app/calibre -maxdepth 1 -type f -perm -u+x \
  ! -name 'calibre_postinstall' ! -name 'calibre-complete' \
  -exec ln -sf {} /usr/bin/ \; && \
  test -x /usr/bin/calibredb

# Deliberately NO global CALIBRE_CONFIG_DIRECTORY here. (A misspelled
# CALIBRE_CONFIG_DIR lived here for a while -- Calibre ignores that name,
# and setting the real one globally would force user-plugin loading on for
# every Calibre subprocess. Plugin loading is opt-in via
# CWA_CALIBRE_USER_PLUGINS; cps/services/calibre_user_plugins.py sets
# CALIBRE_CONFIG_DIRECTORY per-subprocess when the operator enables it.)

# Ports and volumes
WORKDIR /config
# The default port CWA listens on. Can be overridden with the CWA_PORT_OVERRIDE environment variable.
EXPOSE 8083
VOLUME /config
VOLUME /cwa-book-ingest
VOLUME /calibre-library

# Health check for container orchestration
# Targets the /health endpoint (cps/web.py:1051) which verifies the
# Calibre metadata.db is reachable and returns 503 on database failure.
# The helper auto-switches to HTTPS when app.db has a valid cert/key
# configured, which avoids spurious HTTP-on-HTTPS warnings from gevent.
HEALTHCHECK --interval=30s --timeout=3s --start-period=120s --retries=3 \
  CMD /usr/local/bin/cwa-healthcheck || exit 1
