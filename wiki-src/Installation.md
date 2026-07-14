# Installation

Calibre-Web-NextGen ships as a single Docker image:

```
ghcr.io/new-usemame/calibre-web-nextgen:latest
```

It's a drop-in for the standard Calibre-Web-Automated (CWA) image. **Switching keeps everything** — your books, users, settings, shelves and the Read checkmarks you've set all live in the folders you mount into the container (`/config` and `/calibre-library`), not inside the image. Nothing is converted or deleted, and you can go back to your old image with the same one-line change in reverse.

For the shortest possible path, see **[[Quick Start]]**. Coming from another image? See **[[Migrating]]**.

---

## Not using a terminal? Pick your platform

Every guide covers both a **fresh install** and **switching from stock CWA**, and tells you how to **update** later on that platform (on most NAS GUIs a "restart" does **not** pull a new image — you have to re-pull, and each guide shows exactly how).

| You run Docker through… | Guide |
|---|---|
| **Synology** (Container Manager / DSM 7.2+) | [[Install on Synology]] |
| **Unraid** (Docker tab) | [[Install on Unraid]] |
| **Portainer** (Stacks) | [[Install with Portainer]] |
| **TrueNAS SCALE** (Apps) | [[Install on TrueNAS SCALE]] |
| **QNAP** (Container Station) | [[Install on QNAP]] |
| **Dockge** (compose stacks) | [[Install with Dockge]] |
| **A terminal / `docker compose`** | [[Install with Docker Compose]] |

---

{{repo:README.md#full-docker-compose-setup|heading}}

**Next:** **[[First Run]]** → **[[Updating]]**.
