# #330 — custom Kobo sleep screen

## Viability verdict

**Not viable for stock Kobo/Nickel through the server sync protocol.** The reporter's protocol audit is consistent with the product boundary: Kobo sync exposes books, metadata, reading state, shelves, and cover resources, but no device wallpaper/sleep-screen setting or arbitrary device-file channel. A web control would therefore promise an effect CWNG cannot deliver.

## Supported path

Document the device-side “show current book cover while sleeping” setting. Once enabled on the Kobo, the cover CWNG already syncs for the current book becomes the sleep image; changing that book's cover in CWNG changes the later cover resource naturally. The UI must describe this as a Kobo setting, not a remote CWNG toggle.

## Rejected alternatives

- Reusing a cover endpoint for arbitrary art would corrupt the book-cover contract and caches.
- Writing a file over USB requires a host/device connection outside the server protocol.
- KOReader custom screensavers are a different device-side runtime and do not satisfy the requested stock Nickel behavior. Extending `cwasync.koplugin` could only produce a KOReader-specific feature and would need a separate request, explicit storage/path compatibility work, and device testing.

## Disposition

Close as protocol-limited after adding operator-approved documentation. Reopen only with primary evidence of a supported Nickel sync field or authenticated file-transfer surface. No code, route, dependency, or external URL is added.
