# Install / switch on Unraid

For Unraid's **Docker** tab. Works whether you're installing fresh or switching from the
standard Calibre-Web-Automated (CWA) image.

**Your books, users, settings and Read checkmarks live in the appdata + library shares you
map into the container — not in the image — so switching keeps everything and is reversible.**

> **Needs hardware verification:** these Unraid labels and steps are docs-verified, but we do not
> yet have a real Unraid walkthrough or screenshots. Please use the help links below if your screen differs.

## Switching from CWA (fastest path)

If you already run CWA on Unraid, you only need to change the image it uses:

1. **Docker** tab → click your existing Calibre-Web-Automated container → **Edit**.
2. Set **Repository** to:
   ```
   ghcr.io/new-usemame/calibre-web-nextgen:latest
   ```
3. Leave every **Path** mapping as-is — `/config`, `/calibre-library`, `/cwa-book-ingest`
   should keep pointing at the same `/mnt/user/...` shares you already use. Leave `PUID`/`PGID`
   (Unraid's default is `99`/`100`) and your port mapping unchanged.
4. **Apply.** Unraid pulls the NextGen image and recreates the container with all your data
   mounted. Open the WebUI and log in — everything's there.

> Keeping the same container name and paths means it's a true in-place swap. If you'd rather
> keep CWA around as a fallback, instead follow "Fresh install" below with a **different**
> container name but the **same** library share, and only one of the two running at a time.

## Fresh install

1. **Docker** tab → **Add Container**.
2. Fill in:
   - **Name:** `calibre-web-nextgen`
   - **Repository:** `ghcr.io/new-usemame/calibre-web-nextgen:latest`
   - **Network Type:** `bridge`
   - **Port:** add a Port mapping — Container Port `8083` → a Host Port of your choice (e.g. `8083`).
   - **Path** mappings (add three):
     - Container `/config` → Host `/mnt/user/appdata/calibre-web-nextgen`
     - Container `/calibre-library` → Host `/mnt/user/books` (wherever your Calibre library lives)
     - Container `/cwa-book-ingest` → Host `/mnt/user/books-ingest` (a folder to drop new books into)
   - **Variables** (add three): `PUID=99`, `PGID=100`, `TZ=America/New_York` (use your timezone).
3. **Apply.** Unraid pulls the image and starts it. Open the WebUI on the host port you chose.

## Updating later

1. **Docker** tab → toggle **Advanced View** (top-right) so the "version" column shows.
2. NextGen will show **update ready** when a newer image is published. Click it (or
   **force update** from the container's context menu) — Unraid re-pulls and recreates the
   container with your data intact.

   *(If you don't see "update ready", click **Check for Updates** at the bottom of the Docker tab.)*

---

**Your setup might differ.** If a step doesn't match what you see on screen, or if
sync / auto-ingest isn't working after you switch, we'll help you through it:

- **Open an issue** (best for tracking): https://github.com/new-usemame/Calibre-Web-NextGen/issues
- **Ask on Discord** (faster back-and-forth): https://discord.gg/B8NXZmcp32

Include your platform and a screenshot of the screen you're stuck on, and we'll tell you
the exact buttons to press.
