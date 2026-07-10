# Install / switch to Calibre-Web-NextGen

Calibre-Web-NextGen ships as a single Docker image:

```
ghcr.io/new-usemame/calibre-web-nextgen:latest
```

It's a drop-in for the standard Calibre-Web-Automated (CWA) image. **Switching keeps
everything** — your books, users, settings, shelves and the Read checkmarks you've set all
live in the folders you mount into the container (`/config` and `/calibre-library`), not
inside the image. Nothing is converted or deleted, and you can go back to your old image
with the same one-line change in reverse.

Pick the guide that matches how you run Docker:

| You run Docker through… | Guide |
|---|---|
| **Synology** (Container Manager / DSM 7.2+) | [synology.md](synology.md) |
| **Unraid** (Docker tab) | [unraid.md](unraid.md) |
| **Portainer** (Stacks) | [portainer.md](portainer.md) |
| **TrueNAS SCALE** (Apps) | [truenas.md](truenas.md) |
| **QNAP** (Container Station) | [qnap.md](qnap.md) |
| **Dockge** (compose stacks) | [dockge.md](dockge.md) |
| **A terminal / `docker compose`** | [compose.md](compose.md) |
| Something else | not listed yet — see the help links below, we'll walk you through it |

Every guide covers both a **fresh install** and **switching from stock CWA**, and tells you
how to **update** later on that platform (on most NAS GUIs a "restart" does **not** pull a new
image — you have to re-pull, and each guide shows exactly how).

---

**Your setup might differ.** If a step doesn't match what you see on screen, or if
sync / auto-ingest isn't working after you switch, we'll help you through it:

- **Open an issue** (best for tracking): https://github.com/new-usemame/Calibre-Web-NextGen/issues
- **Ask on Discord** (faster back-and-forth): https://discord.gg/B8NXZmcp32

Include your platform and a screenshot of the screen you're stuck on, and we'll tell you
the exact buttons to press.
