# Managed Calibre content server

The optional Calibre content server lets Calibre clients reach the library while
Calibre-Web NextGen keeps serving its web UI. It is disabled by default. This is
a separate Calibre service, with its own credentials and permissions; the web
UI's user restrictions do not become Calibre content-server restrictions.

Open **Admin → Basic Configuration**, enable **Calibre Content Server**, and
choose its port, listen address, username and password. Use a port different
from the web UI's port. Calibre's binaries must be installed at the configured
binaries location. Save, then connect your Calibre client to that listen address
and port. In Docker, publish the chosen container port separately if clients
outside the container need access.

The default listen address, `127.0.0.1`, limits access to the same machine (or
the same container). Use `0.0.0.0` for network access and configure Docker port
mapping and network access accordingly. IPv6 addresses are also accepted.

The service uses Calibre's default authentication mode: Digest on plain HTTP.
The content server's account can read and write the complete library. Choose
credentials specifically for this service. Username characters must be letters
A–Z, digits, spaces, underscores or hyphens; passwords must contain printable
ASCII characters. The password is encrypted in `app.db`, fed to child processes
through stdin, and stored by Calibre in an owner-only user database. Resetting
the password stops the authenticated service until a new password is saved.

**Allow Anonymous Writes** is an explicit alternative. It also lets anyone who
can reach the service read the entire library. With no trusted addresses, writes
are limited to local connections. Trusted IPs/CIDRs extend that permission.
Docker port mapping and reverse proxies can make every client appear local or
come from one shared address, so address-based permissions may permit all such
clients to write. The form explains this before saving.

`CALIBRE_SERVER_PORT`, `CALIBRE_SERVER_USERNAME` and
`CALIBRE_SERVER_PASSWORD` override their saved fields. The corresponding fields
are disabled in the form. Keep secrets in the deployment's established secret
handling rather than in command-line arguments. Changes to environment values
require an application restart.

## Library operations and freshness

NextGen's supported `calibredb` operations use the running content server,
including its authenticated stdin credentials. Standalone ingest/enforcement
scripts read the same routing policy and encrypted settings from `app.db`.
When the service is disabled or unavailable, operations use the library path.
Saving a different library or binaries location reconciles the managed service.

Convert Library and Restore Calibre Database hold the service stopped while
they own the library. Saving or enabling the service during an operation defers
its startup until all library holds have been released. A failed conversion
launch releases its hold. A child whose exit cannot be confirmed keeps the
library held rather than reopening it to a competing database owner.

Calibre keeps changes made through its own server API in its in-memory cache.
Direct database edits from outside that server require a reload. NextGen checks
`metadata.db` and its WAL every five seconds, and reloads after thirty seconds
without further changes. This deliberately conservative watcher cannot identify
which process wrote the database; even server-originated writes may cause a
later reload. Reads can be briefly interrupted during that reload.

An unexpected server exit is retried. Three quick exits stop automatic retries
and preserve the last server output in the application log. Correct the reported
problem and save the settings to retry. A successful settings save confirms the
configuration was stored; use the log and the actual client connection to check
that Calibre started successfully.

Split-library mode is currently unsupported. Disable it before enabling the
content server, or disable the server before enabling split-library mode. The
manager also refuses the combination at startup, including a previously saved
configuration. Calibre's library broker expects a library-local `metadata.db`;
setting a database override alone does not provide complete split-library
support.

This integration began with [@benjitobz's contribution](https://github.com/new-usemame/Calibre-Web-NextGen/pull/2210).
