Native Windows source checkouts no longer fail at startup because `fcntl` is
unavailable. Restore, Kobo exchange capture, and Kobo PATCH spool locks share a
platform helper using POSIX `flock` or Windows `msvcrt` byte-range locks. Restore
still refuses a lock held by another process. If neither backend is available,
locking explicitly degrades to a logged no-op; use one app process and avoid
concurrent restore/service writers on such platforms. Thanks to Rol3333 for the
Windows 11 / Python 3.11 report (#2168).
