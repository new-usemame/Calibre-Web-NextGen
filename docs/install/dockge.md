# Install / switch with Dockge

For [Dockge](https://github.com/louislam/dockge), the compose-file stack manager. Each app is
a **stack** — a `compose.yaml` Dockge stores on disk and manages from the web UI. Works
whether you're installing fresh or switching from the standard Calibre-Web-Automated (CWA)
image.

**Your books, users, settings and the Read checkmarks you've set live in the volumes you bind
into the container — not in the image — so switching keeps everything and is reversible.**

> **Needs hardware verification:** these Dockge labels and steps are docs-verified, but we do not
> yet have a real Dockge walkthrough or screenshots. Please use the help links below if your screen differs.

## Fresh install (new stack)

1. In Dockge, click **+ Compose** (new stack).
2. **Stack name:** `calibre-web-nextgen`. Paste this into the compose editor:

   ```yaml
   services:
     calibre-web-nextgen:
       image: ghcr.io/new-usemame/calibre-web-nextgen:latest
       container_name: calibre-web-nextgen
       environment:
         - PUID=1000
         - PGID=1000
         - TZ=America/New_York
       volumes:
         - /opt/stacks/calibre-web-nextgen/config:/config
         - /opt/stacks/calibre-web-nextgen/library:/calibre-library
         - /opt/stacks/calibre-web-nextgen/ingest:/cwa-book-ingest
       ports:
         - 8083:8083
       restart: unless-stopped
   ```

   Replace the three host paths with real folders on the Docker host, and set
   `PUID`/`PGID`/`TZ` to your user and timezone.
3. Click **Deploy.** Dockge pulls the image and starts the stack. Open `http://<host>:8083`.

## Switching from CWA

- **If your CWA already runs as a Dockge stack:** open that stack, change the image line to
  `ghcr.io/new-usemame/calibre-web-nextgen:latest`, keep the same volume binds, then
  **Deploy** (Dockge re-pulls and recreates).
- **If your CWA runs outside Dockge** (a plain `docker run`, another compose file, or another
  GUI): create the stack above, but point the three volumes at the **same host folders** CWA
  already uses so your library and settings carry over. Stop the old CWA container first so
  only one app uses the library at a time.

## Updating later

1. Open your `calibre-web-nextgen` stack in Dockge.
2. Click the stack's **update** control — Dockge shows an update indicator when a newer image
   is available; use it, or just click **Deploy** again to re-pull and recreate. Dockge pulls
   the newest `ghcr.io/new-usemame/calibre-web-nextgen:latest` and recreates the container with
   your data intact.

   *(A plain restart does not pull a new image — you have to re-pull, which Deploy does.)*

---

**Your setup might differ.** If a step doesn't match what you see on screen, or if
sync / auto-ingest isn't working after you switch, we'll help you through it:

- **Open an issue** (best for tracking): https://github.com/new-usemame/Calibre-Web-NextGen/issues
- **Ask on Discord** (faster back-and-forth): https://discord.gg/B8NXZmcp32

Include your platform and a screenshot of the screen you're stuck on, and we'll tell you
the exact buttons to press.
