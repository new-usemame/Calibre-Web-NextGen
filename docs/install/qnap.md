# Install / switch on QNAP (Container Station)

For QTS/QuTS with **Container Station 3+** (the "Applications" feature, which runs
docker-compose files). Works whether you're installing fresh or switching from the standard
Calibre-Web-Automated (CWA) image.

**Your books, users, settings and the Read checkmarks you've set all live in the folders
mapped into the container — nothing gets converted or deleted, and you can undo the whole
thing** by starting your old application again.

> **Needs hardware verification:** these QNAP labels and steps are docs-verified, but we do not
> yet have a real Container Station walkthrough or screenshots. Please use the help links below if your screen differs.

## Switching from CWA? Note your current setup first

1. **Container Station → Containers** (or **Applications**), open your existing CWA container →
   its overview.
2. Note which QNAP shared folder maps to each of `/config`, `/calibre-library`, and
   `/cwa-book-ingest` (Container Station shows these under the container's **Storage** /
   volume settings).
3. Note the `PUID`, `PGID`, and `TZ` from its **Environment** settings.
4. **Stop** the old container — but **don't delete it.** A stopped container is your instant
   undo.

*(Fresh install? Skip the four steps above and just decide which QNAP shared folders you want
for `/config`, `/calibre-library` and `/cwa-book-ingest`, and your `PUID`/`PGID`.)*

## Create the NextGen application

1. **Container Station → Applications → Create.**
2. **Application name:** `calibre-web-nextgen`. In the YAML editor, paste the block below and
   replace the three `/share/...` paths and the `PUID`/`PGID`/`TZ` with your values:

   ```yaml
   services:
     calibre-web-nextgen:
       image: ghcr.io/new-usemame/calibre-web-nextgen:latest
       container_name: calibre-web-nextgen
       environment:
         - PUID=1000
         - PGID=100
         - TZ=America/New_York
       volumes:
         - /share/Container/calibre/config:/config
         - /share/Container/calibre/library:/calibre-library
         - /share/Container/calibre/ingest:/cwa-book-ingest
       ports:
         - 8083:8083
       restart: unless-stopped
   ```

   Point the three `/share/...` paths at the **same shared folders** your CWA used (if you're
   switching), so your existing library and settings carry over. To find a QNAP path, browse
   the folder in **File Station** and read the path at the top.
3. **Create.** Container Station pulls the image from ghcr.io and starts it. Open
   `http://<qnap-ip>:8083` and log in with your usual account — your library, users and Read
   checkmarks are all there.

## Updating later — important

Restarting the container does **not** pull a newer image. To actually update:

1. **Container Station → Applications** → your `calibre-web-nextgen` application → **Stop**.
2. Open the application's YAML editor and **Recreate** (some QTS versions label this
   **Update** or **Pull image and recreate**). Container Station re-pulls
   `ghcr.io/new-usemame/calibre-web-nextgen:latest` and recreates the container with your data
   intact.

   If your Container Station version has no "recreate/pull" option, delete the **container**
   first (your data in the shared folders is untouched), then delete the cached
   `ghcr.io/new-usemame/calibre-web-nextgen:latest` image under **Images**, then **Create** the
   application again from the same YAML — that forces a fresh pull.

---

**Your setup might differ.** If a step doesn't match what you see on screen, or if
sync / auto-ingest isn't working after you switch, we'll help you through it:

- **Open an issue** (best for tracking): https://github.com/new-usemame/Calibre-Web-NextGen/issues
- **Ask on Discord** (faster back-and-forth): https://discord.gg/B8NXZmcp32

Include your platform and a screenshot of the screen you're stuck on, and we'll tell you
the exact buttons to press.
