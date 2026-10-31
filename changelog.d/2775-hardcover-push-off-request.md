### Fixed

- **KOReader no longer reports a stored reading as "the server took too long" when Hardcover sync is on.** Each matched reading from KOReader or a Kobo waited for several Hardcover requests before the server answered, 2.7 to 5 seconds on a Raspberry Pi, past the plugin's 2 second limit, and the rest of the app waited with it. The reading is now saved and answered first, and Hardcover gets it a moment later. If you turn pages while Hardcover is slow, it gets your latest place rather than every step. Found and measured by @sgreadly (#2775).
