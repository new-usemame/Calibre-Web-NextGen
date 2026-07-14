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
| **Synology** (Container Manager / DSM 7.2+) | [synology.md](https://github.com/new-usemame/Calibre-Web-NextGen/blob/main/docs/install/synology.md) |
| **Unraid** (Docker tab) | [unraid.md](https://github.com/new-usemame/Calibre-Web-NextGen/blob/main/docs/install/unraid.md) |
| **Portainer** (Stacks) | [portainer.md](https://github.com/new-usemame/Calibre-Web-NextGen/blob/main/docs/install/portainer.md) |
| **TrueNAS SCALE** (Apps) | [truenas.md](https://github.com/new-usemame/Calibre-Web-NextGen/blob/main/docs/install/truenas.md) |
| **A terminal / `docker compose`** | [compose.md](https://github.com/new-usemame/Calibre-Web-NextGen/blob/main/docs/install/compose.md) |
| QNAP, Dockge, something else | not written yet — [open an issue](https://github.com/new-usemame/Calibre-Web-NextGen/issues) or [ask on Discord](https://discord.gg/B8NXZmcp32) and we'll walk you through it |

---

{{repo:README.md#full-docker-compose-setup|heading}}

**Next:** **[[First Run]]** → **[[Updating]]**.
