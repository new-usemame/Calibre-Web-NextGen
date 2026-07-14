# Install / switch with Portainer (Stacks)

For Portainer CE/BE on any host. The cleanest path is a **Stack** (Portainer's name for a
docker-compose file you manage in the web UI).

**Your books, users, settings and Read checkmarks live in the volumes you bind into the
container — not in the image — so switching keeps everything and is reversible.**

> **Needs hardware verification:** these Portainer labels and steps are docs-verified, but we do not
> yet have a real Portainer walkthrough or screenshots. Please use the help links below if your screen differs.

## Fresh install (Stack)

1. **Stacks → Add stack.**
2. **Name:** `calibre-web-nextgen`. Build method: **Web editor.** Paste:

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
         - /path/on/host/config:/config
         - /path/on/host/library:/calibre-library
         - /path/on/host/ingest:/cwa-book-ingest
       ports:
         - 8083:8083
       restart: unless-stopped
   ```

   Replace the three `/path/on/host/...` values with real folders on the Docker host, and set
   `PUID`/`PGID`/`TZ` to your user and timezone.
3. **Deploy the stack.** Portainer pulls the image and starts it. Open `http://<host>:8083`.

## Switching from CWA

- **If your CWA runs as a Stack:** open that stack → **Editor**, change the image line to
  `ghcr.io/new-usemame/calibre-web-nextgen:latest`, keep the same volume binds, then
  **Update the stack** with **Re-pull image and redeploy** ticked.
- **If your CWA runs as a standalone container:** the tidiest switch is to recreate it as the
  stack above, pointing the three volumes at the **same host folders** CWA already uses
  (Containers → your CWA container → **Inspect** shows its current bind mounts). Stop the old
  container first so only one app uses the library at a time.

## Updating later

1. **Stacks →** your `calibre-web-nextgen` stack → **Editor**.
2. Click **Update the stack** and tick **Re-pull image and redeploy** (older Portainer:
   **Pull and redeploy**). Portainer pulls the newest image and recreates the container with
   your data intact.

   *(Just restarting the container does not pull a new image — you must re-pull.)*

---

**Your setup might differ.** If a step doesn't match what you see on screen, or if
sync / auto-ingest isn't working after you switch, we'll help you through it:

- **Open an issue** (best for tracking): https://github.com/new-usemame/Calibre-Web-NextGen/issues
- **Ask on Discord** (faster back-and-forth): https://discord.gg/B8NXZmcp32

Include your platform and a screenshot of the screen you're stuck on, and we'll tell you
the exact buttons to press.
