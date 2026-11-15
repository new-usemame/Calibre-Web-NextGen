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
When the service is stopped and no managed owner remains, operations use the
library path. An unready server that still owns the library causes a retryable
failure instead of opening that path concurrently.
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
later reload. Reads can be briefly interrupted during that reload. Until it reloads, a Calibre
client can see old metadata or a path that NextGen has renamed. Avoid editing the
same book simultaneously through both applications.

An unexpected server exit is retried. Three quick exits stop automatic retries
and preserve the last server output in the application log. Correct the reported
problem and save the settings to retry. A successful settings save confirms the
configuration was stored; use the log and the actual client connection to check
that Calibre started successfully.
Planned maintenance pauses do not count toward that crash limit. Contention
defers startup and reload until the writer gate becomes available. Failed or
busy ingest operations retain their input and add-format intent for the ingest
service's retry queue instead of acknowledging an uncommitted format. While the
library is busy, ingest checks before conversion and retries periodically when
maintenance ends. Other failures retry at startup or after another book is
successfully ingested, including a successful periodic busy retry. That gives
other queued inputs one retry opportunity without repeatedly converting a
persistently failing input. Restarting the service rechecks the durable queue.

Split-library mode is currently unsupported. Disable it before enabling the
content server, or disable the server before enabling split-library mode. The
manager also refuses the combination at startup, including a previously saved
configuration. Calibre's library broker expects a library-local `metadata.db`;
setting a database override alone does not provide complete split-library
support.

This integration began with [@benjitobz's contribution](https://github.com/new-usemame/Calibre-Web-NextGen/pull/2210).


## Process ownership and platform support

The managed server supports POSIX platforms, including the Linux container.
Native Windows cannot enable it; use the Linux container there. The ownership
protocol relies on a descriptor inherited by the Calibre child and shared local
filesystem locks. Keep `/config` on a local filesystem; NFS/CIFS lock inheritance
and ownership changes are not verified. Without that
ownership, a killed supervisor could leave a running server invisible to the
next app process. This restriction applies to the optional server; the normal
server-disabled app and command-line tools keep their path-based operation.

The supervisor stops and reaps Calibre when the app lifeline closes. On Linux,
Calibre also receives a parent-death signal if its supervisor is killed. On
other POSIX platforms, a child that survives a forced supervisor kill retains
the owner lock: subsequent operations refuse path access until it exits.
Raw ingest imports pause the managed cache only around raw Calibre inspection and
transactions; their Calibre child inherits both maintenance and metadata-writer
locks. Network metadata fetch and delivery run after the raw transaction releases
that pause. Optional generated-cover updates during ingest and cover enforcement take a maintenance scope and the shared writer gate for
the cover file and its metadata flag. Existing cover entries are skipped before taking maintenance ownership, preserving deliberate generic-cover choices. If a new generated file is saved but its flag commit fails, cleanup removes only that unchanged owned file when the flag is confirmed zero. A changed file or uncertain flag state is preserved. A later explicit cover pass with automatic generation still enabled can generate again after successful cleanup; no automatic later pass or recovery after a hard kill is promised. A cover failure remains best-effort and does not requeue a committed import. Publication uses an atomic no-replace hard link so a reader-selected cover that arrives during rendering wins; when hard links are unavailable, native exclusive rename on Linux, macOS or Windows retains the same complete-file and no-replace guarantees. If the platform or filesystem supports neither operation, the optional cover is skipped with a warning. A staging cleanup error is logged without suppressing a successfully published file or its metadata flag commit. Cleanup is conservative on network or FUSE libraries: uncertain identity or metadata can leave a file for manual inspection. Publication prevents a live reader from seeing a partial destination file; it does not promise durability after power loss. A failed staging unlink or hard kill can leave a staging file for manual cleanup. Its isolated renderer and font probe each
have a 25-second limit, so an enabled managed server can be paused for tens of
seconds per generated cover. Automatic cover generation is off by default;
leave it off when uninterrupted external reads matter more than generated covers. Raw imports and Convert Library serialize even
when the optional server is disabled, because both use Calibre directly on the
same library. Convert Library waits up to two minutes for an existing raw import;
an uncommitted busy ingest retains its source and retries through the service
queue. After import commits, optional path and timestamp enrichment failures
are warnings; they cannot abort cover generation or delivery of that new book.
A standalone Convert Library run owns its maintenance lock for the whole run,
including across an app restart. Restore holds the shared writer gate through
its subprocesses, and settings changes retain that gate until the server uses
the saved generation. These locks coordinate NextGen's supported operations;
external programs must still stop the managed server before changing its library.

Calibre readiness is checked with a credential-free HTTP probe. A port accepting
TCP connections alone cannot receive the configured credentials. Authenticated
calibredb calls use a private stdin pipe, preserving passwords with trailing
spaces and avoiding a controlling-terminal prompt or a password-bearing command
argument. Anonymous writes on a specific local LAN bind trust that local address
in addition to the administrator's explicit trusted addresses.
