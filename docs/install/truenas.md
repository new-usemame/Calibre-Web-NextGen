# Install / switch on TrueNAS SCALE (Apps)

For TrueNAS SCALE. **Your books, users, settings and Read checkmarks live in the host-path
storage you attach to the app — not in the image — so switching keeps everything.**

> **Needs hardware verification:** these TrueNAS labels and steps are docs-verified, but we do not
> yet have a real TrueNAS walkthrough or screenshots. Please use the help links below if your screen differs.

> **A note on TrueNAS versions.** SCALE changed its Apps system between releases. On
> **ElectricEel (24.10) and newer**, Apps run on a Docker Compose backend and there's an
> **"Install via YAML" / Custom App** flow. On older releases (Dragonfish/Cobia) the
> equivalent is **Apps → Discover Apps → Custom App** with form fields. The values below are
> the same either way — only where you type them moves. If your screen differs, use the help
> links at the bottom and we'll match it to your version.

## Custom App (ElectricEel 24.10+)

1. **Apps → Discover Apps → Custom App** (top-right). Choose **Install via YAML** if offered.
2. Paste / fill:

   ```yaml
   services:
     calibre-web-nextgen:
       image: ghcr.io/new-usemame/calibre-web-nextgen:latest
       container_name: calibre-web-nextgen
       environment:
         - PUID=568
         - PGID=568
         - TZ=America/New_York
       volumes:
         - /mnt/pool/apps/calibre-web-nextgen/config:/config
         - /mnt/pool/books:/calibre-library
         - /mnt/pool/books-ingest:/cwa-book-ingest
       ports:
         - 8083:8083
       restart: unless-stopped
   ```

   - Replace `/mnt/pool/...` with real dataset paths.
   - `PUID`/`PGID` `568` is the built-in `apps` user on SCALE; use that unless your library
     files are owned by a different user.
3. **Install / Save.** SCALE pulls the image and starts the app. Open `http://<truenas-ip>:8083`.

## Form-based Custom App (older SCALE)

If you don't have a YAML box, use the form fields:
- **Image repository:** `ghcr.io/new-usemame/calibre-web-nextgen` — **Image tag:** `latest`
- **Container port:** `8083`, with a Node port to reach it on.
- **Storage / Host Path Volumes** (add three): host dataset → `/config`,
  your library → `/calibre-library`, an ingest folder → `/cwa-book-ingest`.
- **Environment variables:** `PUID`, `PGID`, `TZ` as above.

## Switching from CWA

Point the new app's three storage paths at the **same datasets** your CWA app already uses
(your old app's config dataset holds `app.db` with users + settings; the library dataset holds
your books). Stop the old CWA app first so only one app uses the library at a time. Nothing in
those datasets is modified by the switch, so you can re-enable CWA to roll back.

## Updating later

**Apps → Installed →** select `calibre-web-nextgen`. When an update is available SCALE shows an
**Update** badge — click it to pull the newest image. (A simple **Stop/Start** does not pull a
new image.) To force a re-pull of `:latest`, **Edit** the app and re-save, or **Update** when
the badge appears.

---

**Your setup might differ.** If a step doesn't match what you see on screen, or if
sync / auto-ingest isn't working after you switch, we'll help you through it:

- **Open an issue** (best for tracking): https://github.com/new-usemame/Calibre-Web-NextGen/issues
- **Ask on Discord** (faster back-and-forth): https://discord.gg/B8NXZmcp32

Include your platform and a screenshot of the screen you're stuck on, and we'll tell you
the exact buttons to press.
