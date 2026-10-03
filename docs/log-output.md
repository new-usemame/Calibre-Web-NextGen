# Application log output

The application normally sends each log record to stdout and a rotating log file. Docker logs receives stdout, while the administrator's **View Logs** page reads the file. The default file is `calibre-web.log` in the configured application directory.

On a bare-metal service, stdout may already be redirected to that same file, for example through `StandardOutput=append:/path/calibre-web.log` or `>>`. When the two opened sinks have the same device and inode, the application uses only the rotating file handler. Records appear once, and subsequent records follow the new log file after rollover. This also works when stdout uses a symlink or hardlink to the file, or when the requested log path falls back to a default file shared with stdout.

Settings reloads retain this shared-output relationship after rollover, while the inherited stdout stream and the current rotating target remain the same open sinks. Changing the configured target or redirecting stdout restores both outputs when their sinks differ.

When the opened sinks differ, both receive records. If stdout has no usable file descriptor, the application retains both outputs because it cannot establish that they share a file. Only regular files are deduplicated. This choice preserves Docker logging and administrator log visibility.

Explicit `/dev/stdout` and `/dev/stderr` log settings remain stream-only. Application logging retains the existing 5MiB rollover threshold and five backups. Other code that writes directly to an inherited stdout descriptor is outside this logging configuration; after rollover that descriptor still points to the original inode.
