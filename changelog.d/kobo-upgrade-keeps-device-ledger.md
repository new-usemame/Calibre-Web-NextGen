### Fixed

- **Updating the server no longer tells an existing Kobo that its whole
  library is new.** The first sync after the update used to announce every
  book again, so each reader downloaded its library again and lost its place
  in every book. Each Kobo now keeps the books it already has: on the first
  sync after the update, on the syncs after it, on a sync after the reader
  lost its sync token (as after a USB eject), and when a magic shelf changes.
  This holds for a single Kobo, for several Kobos sharing one account, and
  whichever Kobo syncs first, including one paired after the update. It holds
  for servers updating from v4.1.43 and from older releases, which kept one
  delivery history per account rather than per Kobo.
- **What still arrives, once:** a book added or edited since the reader last
  synced; a book deleted from the library while the reader was away, as a
  removal; and a book an older version sent that nothing ever downloaded.
  That last case covers the books after the first hundred that older
  versions announced in a way an empty Kobo ignores (#1735), and a sync
  whose reply never reached the reader. Those books now arrive as new books.
- **Known limits:**
  - The server records that an account downloaded a book, not which Kobo
    downloaded it. If one Kobo is missing a book that another Kobo on the
    same account, or a browser, downloaded, the update does not send it
    again. Use **Resend one book to this Kobo** on the account page, or
    **Force full kobo sync**.
  - A book is also sent again once if its download records are gone and no
    Kobo ever reported reading it. Until this release, opening **Hot Books**
    deleted every account's download records of the books its viewer could
    not see; the server cannot tell those books from ones a Kobo never
    received, so it sends them rather than risk one never arriving.
  - Three more cases apply only to a server updating from a release older
    than v4.1.43, which kept no delivery record per Kobo. A Kobo whose first
    sync after the update carries no sync position, for example after a
    factory reset or an automatic sync right after a USB eject, receives its
    library again once: the server cannot tell a reader that was reset from
    one that still holds everything. From v4.1.33 or older, the server had
    not recorded its Kobos yet, so a second Kobo on the same account is
    treated as new and receives its library again once. With **only sync
    selected shelves** and more than one Kobo on the account, a book that
    only a magic shelf selects is sent again once.
