### Fixed

- **Upgrading no longer tells an existing Kobo that its whole library is
  new.** The one-time delivery-record check used to throw away everything the
  server knew about what a reader already held, so the first sync after the
  update re-announced every book, and the reader re-downloaded the lot and lost
  its place in each one. A Kobo that is the only one paired to its account now
  keeps that record. Books it already has stay quiet on the upgrade sync and on
  any later sync that goes over the whole library again, such as a sync after
  the reader lost its sync token or a magic-shelf refresh. A book edited since
  the reader last received it is still sent once as an update, and books
  deleted from the library while the reader was away are still removed from
  it. Accounts that have paired more than one Kobo, counting readers since
  removed, are unchanged: their old record mixes the readers' histories, so
  their books are announced again.
- **Known limit:** the old version counted a book as delivered once it sent
  it, not once the reader stored it. If a reader that is its account's only
  Kobo is missing a book the old version sent, the upgrade does not bring it
  back. Use **Resend one book to this Kobo** on the account page for that
  book, or **Force full kobo sync**, which sends the whole library again.
