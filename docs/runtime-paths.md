# Runtime path overrides for packagers

Bare-metal installs and distro packages can set all three runtime paths from the process environment instead of editing `dirs.json` inside the installation:

| Environment variable | `dirs.json` fallback | Compiled-in default |
|---|---|---|
| `CWA_INGEST_FOLDER` | `ingest_folder` | `/cwa-book-ingest` |
| `CWA_CALIBRE_LIBRARY_DIR` | `calibre_library_dir` | `/calibre-library` |
| `CWA_TMP_CONVERSION_DIR` | `tmp_conversion_dir` | `/config/.cwa_conversion_tmp` |

A non-blank environment value wins for its key. If the variable is unset or blank, CWNG reads that key from the file named by `CWA_DIRS_JSON`. A missing or malformed file, a document that isn't an object, or a null or blank value falls back to the compiled-in default, so existing hand-edited `dirs.json` files keep working.

Path values are trimmed and lexically normalized. Each must be an absolute, non-root path with no `..` component. Repeated separators, `.` components and trailing separators are collapsed without resolving symlinks. A non-blank environment or `dirs.json` value that breaks these rules stops the affected startup service, so an unsafe path never reaches the file watchers or the recursive ownership fix.

For example, a systemd unit can load a packager-owned file:

```ini
[Service]
EnvironmentFile=/etc/calibre-web-nextgen/paths.env
```

```bash
CWA_INGEST_FOLDER=/srv/calibre-web-nextgen/ingest
CWA_CALIBRE_LIBRARY_DIR=/srv/calibre/library
CWA_TMP_CONVERSION_DIR=/var/cache/calibre-web-nextgen/conversion
```

When `CWA_CALIBRE_LIBRARY_DIR` is set, it is authoritative. Automatic library discovery leaves `dirs.json` unchanged, and if discovery finds a different library, startup stops and reports both paths so you can correct the environment file.

In Docker, the same three variables appear, commented out, in the [full Docker Compose setup](../README.md#full-docker-compose-setup). The volume targets must use the same paths.
