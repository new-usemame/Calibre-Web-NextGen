# Install / switch on Synology (Container Manager)

For DSM 7.2+ with **Container Manager**. Works whether you're installing fresh or switching
from the standard Calibre-Web-Automated (CWA) image.

**Your books, users, settings and the Read checkmarks you've set all live in the folders
mapped into the container — nothing gets converted or deleted, and you can undo the whole
thing in one click** by starting your old container again.

**Hardware verification:** the update steps below were recorded on a real DSM 7.2 Container
Manager system by @uschi1 in [issue #527](https://github.com/new-usemame/Calibre-Web-NextGen/issues/527#issuecomment-4951503223).
The project-creation steps still need a clean-install recording on Synology hardware.

> DSM is often shown in your local language. Where a button has a German label we list both,
> e.g. *Speicherplatz / Volume*. If yours is in another language, the menu **position** is the
> same — match the screenshots, and use the help links at the bottom if anything looks off.

## Switching from CWA? Note your current setup first

1. **Container Manager → Container**, click your existing container (often called `CMA` or
   `calibre-web-automated`) → **Details**.
2. On the **Speicherplatz / Volume** tab, note which Synology folder maps to each of
   `/config`, `/calibre-library`, and `/cwa-book-ingest`.
3. On the **Umgebung / Environment** tab, note `PUID`, `PGID`, `TZ`.
4. Back in **Container**, select it → **Aktion / Action → Anhalten / Stop**.
   **Leave it — don't delete it.** A stopped container is your instant undo.

*(Fresh install? Skip the four steps above and just decide which Synology folders you want for
`/config`, `/calibre-library` and `/cwa-book-ingest`, and your `PUID`/`PGID` — usually your
own user's, shown under Control Panel → User → your account → details.)*

## Create the NextGen project

5. **Container Manager → Projekt / Project → Erstellen / Create.**
   - **Projektname / Project name:** `calibre-web-nextgen`
   - **Quelle / Source:** choose *"docker-compose.yml erstellen / create docker-compose.yml"*
     and paste the block below. Replace the three `/volume1/...` paths and the
     `PUID`/`PGID`/`TZ` with the values you noted:

   ```yaml
   services:
     calibre-web-nextgen:
       image: ghcr.io/new-usemame/calibre-web-nextgen:latest
       container_name: calibre-web-nextgen
       environment:
         - PUID=1026
         - PGID=100
         - TZ=Europe/Berlin
       volumes:
         - /volume1/docker/calibre/config:/config
         - /volume1/docker/calibre/library:/calibre-library
         - /volume1/docker/calibre/ingest:/cwa-book-ingest
       ports:
         - 8083:8083
       restart: unless-stopped
   ```

6. Click through (**Weiter / Next**, then **Fertig / Done**). Container Manager pulls the image
   from ghcr.io on its own — no registry setup needed — and starts it.
7. Open the same web address you always use for Calibre-Web (the `8083` you mapped, or whatever
   host port you chose), and log in with your usual account. Your library, users and Read
   checkmarks are all there.

## Updating later — important

On Synology, **stopping and starting a container does NOT pull a newer image.** To actually
update NextGen:

1. **Container Manager → Projekt / Project** → your `calibre-web-nextgen` project →
   **Aktion / Action → Stop**.
2. **Container Manager → Container** → select the `calibre-web-nextgen` container →
   **Aktion / Action → Löschen / Delete**. Do this *before* the next step — an image can't be
   removed while a container still uses it (even a stopped one), so deleting the image first
   gives an "in use" error. Deleting the container is safe: your books, users and settings
   live in the mounted folders, and the project's compose file recreates the container for you
   in step 4.
3. **Container Manager → Image** → click the `ghcr.io/new-usemame/calibre-web-nextgen:latest`
   row → **Aktion / Action → Löschen / Delete**. Now that no container uses it, this succeeds
   and clears the cached copy so the next build pulls a fresh image. (Your data is in the
   mounted folders, not here.)
4. **Container Manager → Projekt / Project** → your project → **Aktion / Action → Build**.
   Container Manager pulls the newest image fresh and recreates the container. Wait about
   30 seconds, then reload the page.

Real DSM 7.2 update flow (German interface; labels are translated in the steps above):

![The NextGen and stopped CWA containers in Synology Container Manager](images/verified/synology-01-container-action.jpeg)

![The old NextGen image selected before it is removed](images/verified/synology-02-image-details.jpeg)

![Container Manager pulling and rebuilding the NextGen project](images/verified/synology-03-rebuild-finished.jpeg)

---

**Your setup might differ.** If a step doesn't match what you see on screen, or if
sync / auto-ingest isn't working after you switch, we'll help you through it:

- **Open an issue** (best for tracking): https://github.com/new-usemame/Calibre-Web-NextGen/issues
- **Ask on Discord** (faster back-and-forth): https://discord.gg/B8NXZmcp32

Include your platform and a screenshot of the screen you're stuck on, and we'll tell you
the exact buttons to press.
